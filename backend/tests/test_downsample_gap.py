"""降采样申请与数据断点补采链路的回归测试。

覆盖需求里的关键约束：
- 缺口按设备 + 测点 + 时段计算，``[起始, 截止)``，忽略区间外的断点；
- 另一设备/测点的回执不减少当前申请的缺口；
- 续办只领取仍缺的区间，重复签收不重复扣减；
- 迟到回执与新一轮领取冲突时，只把对应时刻从「已领取」变为「已收」；
- 补回的样本真正进入降采样执行，全部补齐后才完成。
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.services.cross_module import cross_service
from app.services.data_gap import (
    canonical,
    data_gap_service,
    granularity_seconds,
    resolve_range,
)
from app.services.downsample import DownsampleService
from app.store import store


@pytest.fixture(autouse=True)
def reset_store():
    for name, rows in SEED_ROWS.items():
        store.rows(name).clear()
        store.rows(name).extend(dict(row) for row in rows)
    yield


def _apply(**kwargs):
    params = {
        "source_point": "P1",
        "target_granularity": "10m",
        "agg_function": "avg",
        "device_id": "DEVI-P1",
        "date": "2026-11-20",
        "time_range": "10:00-10:30",
    }
    params.update(kwargs)
    return cross_service.downsample_with_gap_check(**params)


class TestRangeSemantics:
    def test_half_open_range(self):
        start, end = resolve_range(
            date_value="2026-11-20", time_start="10:00", time_end="10:30"
        )
        assert canonical(start) == "2026-11-20T10:00:00"
        assert canonical(end) == "2026-11-20T10:30:00"
        # 起始时刻自身落在区间内，截止时刻自身不在
        assert start <= start < end
        assert not (start <= end < end)

    def test_time_only_uses_application_date(self):
        start, end = resolve_range(
            date_value="2026-11-21", time_start="08:15", time_end="09:00"
        )
        assert canonical(start) == "2026-11-21T08:15:00"
        assert canonical(end) == "2026-11-21T09:00:00"

    def test_granularity_forms(self):
        assert granularity_seconds("10m") == 600
        assert granularity_seconds("10分钟") == 600
        assert granularity_seconds("600") == 600


class TestGapScope:
    def test_existing_sample_leaves_only_two_gaps(self):
        result = _apply()
        assert result["ok"] is True
        assert [item["时隙"] for item in result["missing"]] == [
            "10:10-10:20", "10:20-10:30",
        ]
        detail = result["detail"]
        assert detail["missing_count"] == 2
        assert [p["状态"] for p in detail["progress"]] == ["已收", "已领取", "已领取"]

    def test_gap_outside_range_is_ignored(self):
        # 10:30 恰是截止时刻（不含）；再塞一条 11:00 的断点也不应计入
        data_gap_service.claim_windows(
            "DEVI-P1", "P1", [resolve_range(date_value="2026-11-20", time_start="10:30",
                                           time_end="11:00")[0]],
            request_id="OTHER",
        )
        result = _apply()
        assert result["detail"]["missing_count"] == 2

    def test_same_point_id_on_other_device_does_not_count(self):
        from datetime import datetime

        # 另一台设备也有 P1 测点，样本齐全；它不应减少本申请的缺口
        for hour, minute in ((10, 10), (10, 20)):
            data_gap_service.record_sample(
                "DEVI-OTHER", "P1", datetime(2026, 11, 20, hour, minute), 30.0
            )
        result = _apply()
        assert result["detail"]["missing_count"] == 2
        assert result["detail"]["device_id"] == "DEVI-P1"

    def test_workbench_matches_detail(self):
        result = _apply()
        detail = result["detail"]
        workbench = cross_service.gap_workbench()
        row = next(
            item for item in workbench["applications"]
            if item["任务编号"] == detail["任务编号"]
        )
        assert row["缺失点数"] == detail["missing_count"]
        assert workbench["pending_count"] == detail["missing_count"]


class TestReceipts:
    def test_late_receipt_conflicts_with_new_round(self):
        applied = _apply()
        task_id = applied["downsample_task"]["id"]
        claim = cross_service.claim_downsample_gaps(task_id)
        assert claim["claimed_intervals"] == [
            "2026-11-20T10:10:00", "2026-11-20T10:20:00",
        ]

        late = cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5,
            receipt_id="R-OLD-1010", request_id="DOWN-ROUND1", round_no=1,
        )
        assert late["duplicated"] is False
        assert late["conflict"] is True
        assert late["applied"] is True

        detail = cross_service.downsample_detail(task_id)
        window = next(p for p in detail["progress"] if p["时刻"].endswith("10:10:00"))
        assert window["状态"] == "已收"
        assert window["上一状态"] == "已领取"
        assert window["冲突处理"]

    def test_other_device_and_out_of_range_receipts_do_not_reduce(self):
        applied = _apply()
        task_id = applied["downsample_task"]["id"]

        cross_service.receive_backfill_receipt(
            "DEVI-P2", "P2", "2026-11-20T10:20:00", 31.2, receipt_id="R-P2-1020",
        )
        cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:30:00", 23.1, receipt_id="R-1030",
        )
        detail = cross_service.downsample_detail(task_id)
        assert detail["missing_count"] == 2
        matched_ids = {row["回执标识"] for row in detail["receipts"]}
        assert matched_ids == set()
        reasons = {row["回执标识"]: row["match_reason"] for row in detail["unrelated_receipts"]}
        assert "设备" in reasons["R-P2-1020"]
        assert "时段" in reasons["R-1030"]

    def test_duplicate_receipt_does_not_reduce_twice(self):
        applied = _apply()
        task_id = applied["downsample_task"]["id"]
        first = cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5, receipt_id="R-1",
        )
        assert first["pending_changed"] is True
        assert cross_service.downsample_detail(task_id)["missing_count"] == 1

        again = cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5, receipt_id="R-1",
        )
        assert again["duplicated"] is True
        assert again["pending_changed"] is False
        assert again["applied"] is False
        assert cross_service.downsample_detail(task_id)["missing_count"] == 1

    def test_same_moment_under_new_receipt_id_still_counts_once(self):
        applied = _apply()
        task_id = applied["downsample_task"]["id"]
        cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5, receipt_id="R-1",
        )
        repeated = cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5, receipt_id="R-1-BIS",
        )
        assert repeated["duplicated"] is True
        assert repeated["pending_changed"] is False
        assert cross_service.downsample_detail(task_id)["missing_count"] == 1

    def test_continuation_only_claims_remaining(self):
        applied = _apply()
        task_id = applied["downsample_task"]["id"]
        cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5, receipt_id="R-1",
        )
        claim = cross_service.claim_downsample_gaps(task_id, request_id="ROUND-2")
        assert claim["claimed_intervals"] == ["2026-11-20T10:20:00"]

    def test_full_scenario_restores_downsample(self):
        applied = _apply()
        task_id = applied["downsample_task"]["id"]

        # 断点服务开始新一轮补采
        cross_service.claim_downsample_gaps(task_id, request_id="ROUND-2")
        # 上一轮 10:10 迟到，与刚领取的请求冲突
        cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:10:00", 22.5,
            receipt_id="R-OLD-1010", request_id="ROUND-1", round_no=1,
        )
        # 同期其它签收
        cross_service.receive_backfill_receipt(
            "DEVI-P2", "P2", "2026-11-20T10:20:00", 31.2, receipt_id="R-P2-1020",
        )
        cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:30:00", 23.1, receipt_id="R-1030",
        )

        detail = cross_service.downsample_detail(task_id)
        states = {p["时隙"]: p for p in detail["progress"]}
        assert states["10:00-10:10"]["状态"] == "已收"
        # 冲突的那一条：已领取 → 已收
        assert states["10:10-10:20"]["上一状态"] == "已领取"
        assert states["10:10-10:20"]["状态"] == "已收"
        assert states["10:20-10:30"]["状态"] == "已领取"
        assert detail["missing_count"] == 1

        # 实际只需再补 10:20
        claim = cross_service.claim_downsample_gaps(task_id, request_id="ROUND-3")
        assert claim["claimed_intervals"] == ["2026-11-20T10:20:00"]

        downsampler = DownsampleService()
        blocked = downsampler.execute_downsample(detail["任务编号"])
        assert blocked["ok"] is False
        assert [item["时刻"] for item in blocked["missing"]] == ["2026-11-20T10:20:00"]

        cross_service.receive_backfill_receipt(
            "DEVI-P1", "P1", "2026-11-20T10:20:00", 22.9,
            receipt_id="R-NEW-1020", request_id="ROUND-3", round_no=1,
        )
        detail = cross_service.downsample_detail(task_id)
        assert detail["missing_count"] == 0
        assert detail["completed"] is True

        done = downsampler.execute_downsample(detail["任务编号"])
        assert done["ok"] is True
        assert done["restored_points"] == 2
        assert done["aggregated_points"] == 3
        assert {item["window_start"] for item in done["result"]} == {
            "2026-11-20T10:00:00", "2026-11-20T10:10:00", "2026-11-20T10:20:00",
        }


class TestDownsampleExecutesRestoredSamples:
    def test_samples_outside_range_not_used(self):
        applied = _apply()
        detail = applied["detail"]
        # 补齐两个缺口，其中 10:20 拿到的是本申请的样本
        data_gap_service.record_sample(
            "DEVI-P1", "P1", __import__("datetime").datetime(2026, 11, 20, 10, 10), 22.5,
            source="补采",
        )
        data_gap_service.record_sample(
            "DEVI-P1", "P1", __import__("datetime").datetime(2026, 11, 20, 10, 20), 22.9,
            source="补采",
        )
        result = DownsampleService().execute_downsample(detail["任务编号"])
        assert result["ok"] is True
        assert result["restored_points"] == 2
        # 10:00 的原始样本和 10:10/10:20 的补采样本各成一个窗口，
        # 区间外的 00:00 样本不参与
        assert result["raw_points"] == 3

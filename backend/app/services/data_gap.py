"""数据断点领域服务：按待补区间领取补采任务，并接收补采回执。

这里承担三类真实业务职责：

1. 把「日期 + 时段 + 目标粒度」换算成待检查的时间窗口，区间语义固定为
   ``[起始时刻, 截止时刻)`` —— 含起点、不含终点。
2. 以「设备编号 + 测点编号 + 采样时刻」为唯一键领取待补区间。同一编号的
   测点挂在其它设备上时，绝不会互相影响。
3. 接收补采回执（签收）。回执按设备、测点、采样时刻归属，重复到达只记录
   一次有效签收，不会重复扣减缺口。
"""
from __future__ import annotations

import re
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.services.base import BaseModuleService
from app.store import store

CST = timezone(timedelta(hours=8))

SAMPLE_MODULE = "ts_sample"
RECEIPT_MODULE = "gap_receipt"

# 窗口状态：待补采（尚未派工）→ 已领取（已派工、等待回执）→ 已收（已签收）
WINDOW_PENDING = "待补采"
WINDOW_CLAIMED = "已领取"
WINDOW_RECEIVED = "已收"


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def canonical(moment: datetime) -> str:
    """统一的时刻字符串，便于在内存表里当唯一键使用。"""
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .strip()
    )
    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date_cls(year, month, day).isoformat()
    except ValueError:
        return None


def parse_moment(value: Any, base_date: Any = None) -> datetime | None:
    """尽量宽松地解析日期时间；只有时分的值按 ``base_date`` 补日期。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = (
        text.replace("年", "-")
        .replace("月", "-")
        .replace("日", "")
        .replace("/", "-")
        .replace("T", " ")
    )
    text = re.sub(r"\s+", " ", text).strip()

    # 仅有时间（10:00 / 10:00:00）时补上业务日期。
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        date_part = _date_text(base_date)
        if date_part is None and isinstance(base_date, (date_cls, datetime)):
            date_part = base_date.strftime("%Y-%m-%d")
        if date_part is None:
            return None
        text = f"{date_part} {text}"
    elif re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", text):
        text = f"{text} 00:00:00"

    numeric_match = re.fullmatch(
        r"(\d{4})-(\d{1,2})-(\d{1,2})[ ](\d{1,2}):(\d{2})(?::(\d{2}))?", text
    )
    if numeric_match:
        year, month, day, hour, minute, second = numeric_match.groups()
        try:
            return datetime(
                int(year), int(month), int(day),
                int(hour), int(minute), int(second or 0),
            )
        except ValueError:
            return None

    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(CST).replace(tzinfo=None)
    return moment


def _time_range_parts(value: Any) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    text = str(value)
    match = re.search(
        r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:~|～|-|至|到|—)\s*(\d{1,2}:\d{2}(?::\d{2})?)",
        text,
    )
    if not match:
        return None, None
    return match.group(1), match.group(2)


def resolve_range(
    date_value: Any = None,
    time_start: Any = None,
    time_end: Any = None,
    time_range: Any = None,
    default_date: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """把申请里的日期、时段归一成 ``[start, end)``。

    取值顺序始终以申请里的实际内容为准：先看完整的日期时间，再用日期补齐
    只有时分的值，最后才回落到日期当天。申请里完全没有日期、只给了时分时，
    使用 ``default_date``（缺省为当前业务日期），绝不把「现在」当成截止时刻。
    """
    range_start, range_end = _time_range_parts(time_range)
    start_raw = time_start if time_start not in (None, "") else range_start
    end_raw = time_end if time_end not in (None, "") else range_end

    explicit_date = _date_text(date_value) or (
        date_value.strftime("%Y-%m-%d") if isinstance(date_value, (date_cls, datetime)) else None
    )

    # 先无基准日期解析一次，拿到可能已经带日期的端点。
    probe_end = parse_moment(end_raw)
    probe_start = parse_moment(start_raw)
    base_date = explicit_date
    if base_date is None:
        if probe_start is not None:
            base_date = probe_start.strftime("%Y-%m-%d")
        elif probe_end is not None:
            base_date = probe_end.strftime("%Y-%m-%d")
        elif date_value not in (None, ""):
            base_date = str(date_value)
        elif start_raw not in (None, "") or end_raw not in (None, ""):
            fallback = default_date or datetime.now(CST)
            base_date = fallback.strftime("%Y-%m-%d")

    start = parse_moment(start_raw, base_date)
    end = parse_moment(end_raw, base_date or (start.strftime("%Y-%m-%d") if start else None))
    if start is None or end is None:
        return None, None
    if end <= start:
        return None, None
    return start, end


def granularity_seconds(value: Any, default: int = 300) -> int:
    """把 ``10m``、``10分钟``、``600`` 等粒度写法换算成秒。"""
    if value is None or str(value).strip() == "":
        return default
    text = str(value).strip().lower()
    named = {
        "1m": 60, "5m": 300, "10m": 600, "15m": 900,
        "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
        "1分钟": 60, "5分钟": 300, "10分钟": 600, "15分钟": 900,
        "30分钟": 1800, "1小时": 3600, "4小时": 14400, "1天": 86400,
        "1min": 60, "5min": 300, "10min": 600, "15min": 900,
        "30min": 1800, "1hour": 3600, "1day": 86400,
    }
    if text in named:
        return named[text]
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sec|秒|m|min|分钟|h|hour|小时|d|day|天)", text)
    if match:
        amount = float(match.group(1))
        unit = match.group(2)
        if unit in {"s", "sec", "秒"}:
            return max(1, int(amount))
        if unit in {"m", "min", "分钟"}:
            return max(1, int(amount * 60))
        if unit in {"h", "hour", "小时"}:
            return max(1, int(amount * 3600))
        return max(1, int(amount * 86400))
    if re.fullmatch(r"\d+", text):
        seconds = int(text)
        return max(1, seconds)
    return default


def expected_windows(
    start: datetime, end: datetime, step_seconds: int, limit: int = 10000
) -> list[datetime]:
    """生成检查窗口，包含起始时刻、排除截止时刻。"""
    step = timedelta(seconds=max(1, int(step_seconds)))
    windows: list[datetime] = []
    current = start
    while current < end and len(windows) < limit:
        windows.append(current)
        current += step
    return windows


def _sample_key(device_id: str, point_id: str, moment: str) -> tuple[str, str, str]:
    return str(device_id), str(point_id), moment


def _row_moment(row: dict[str, Any]) -> datetime | None:
    for field in ("采样时刻", "中断起始", "时刻"):
        moment = parse_moment(row.get(field))
        if moment is not None:
            return moment
    return None


class DataGapService(BaseModuleService):
    MODULE = "data_gap"
    ENTITY = "断点记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["记录编号", "设备编号", "测点编号", "中断起始", "中断结束", "缺失点数", "补采策略", "处理状态"]
    REQUIRED_FIELDS = ["记录编号", "设备编号", "测点编号"]
    STATUS_ORDER = ["待补采", "补采中", "已补齐", "放弃补采"]
    ACTION_RULES = {"发起补采": "补采中", "标记补齐": "已补齐", "放弃补采": "放弃补采"}
    NEGATIVE_ACTIONS = []

    # ------------------------------------------------------------------
    # 采样与回执的基础查询
    # ------------------------------------------------------------------

    def _sample_rows(self) -> list[dict[str, Any]]:
        return store.rows(SAMPLE_MODULE)

    def _receipt_rows(self) -> list[dict[str, Any]]:
        return store.rows(RECEIPT_MODULE)

    def _gap_rows(self, device_id: str, point_id: str) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(self.MODULE)
            if str(row.get("设备编号", "")) == str(device_id)
            and str(row.get("测点编号", "")) == str(point_id)
        ]

    def sample_timestamps(self, device_id: str, point_id: str) -> set[str]:
        timestamps: set[str] = set()
        for row in self._sample_rows():
            if (
                str(row.get("设备编号", "")) == str(device_id)
                and str(row.get("测点编号", "")) == str(point_id)
            ):
                moment = _row_moment(row)
                if moment is not None:
                    timestamps.add(canonical(moment))
        return timestamps

    def has_sample(self, device_id: str, point_id: str, moment: datetime) -> bool:
        return canonical(moment) in self.sample_timestamps(device_id, point_id)

    def record_sample(
        self,
        device_id: str,
        point_id: str,
        moment: datetime,
        value: Any = None,
        *,
        source: str = "补采",
        receipt_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """登记一条真实样本；同一设备/测点/时刻只保存一次。"""
        moment_text = canonical(moment)
        rows = self._sample_rows()
        for row in rows:
            if (
                str(row.get("设备编号", "")) == str(device_id)
                and str(row.get("测点编号", "")) == str(point_id)
                and _row_moment(row) == moment
            ):
                return row, False
        max_id = max((int(row.get("id", 0)) for row in rows), default=0)
        row = {
            "id": max_id + 1,
            "设备编号": device_id,
            "测点编号": point_id,
            "采样时刻": moment_text,
            "数值": "" if value is None else value,
            "来源": source,
            "回执标识": receipt_id,
            "status": "已补齐",
            "pending": False,
            "abnormal": False,
        }
        rows.append(row)
        return row, True

    def samples_between(
        self, device_id: str, point_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self._sample_rows():
            if (
                str(row.get("设备编号", "")) != str(device_id)
                or str(row.get("测点编号", "")) != str(point_id)
            ):
                continue
            moment = _row_moment(row)
            if moment is not None and start <= moment < end:
                result.append({**row, "_moment": moment})
        result.sort(key=lambda item: item["_moment"])
        return result

    # ------------------------------------------------------------------
    # 待补范围计算
    # ------------------------------------------------------------------

    def _gap_index(self, device_id: str, point_id: str) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for row in self._gap_rows(device_id, point_id):
            moment = _row_moment(row)
            if moment is not None:
                index[canonical(moment)] = row
        return index

    @staticmethod
    def _gap_is_received(row: dict[str, Any]) -> bool:
        if str(row.get("回执标识", "")).strip():
            return True
        return str(row.get("处理状态", "")) in (WINDOW_RECEIVED, "已补齐")

    def progress(
        self,
        device_id: str,
        point_id: str,
        start: datetime,
        end: datetime,
        step_seconds: int,
    ) -> list[dict[str, Any]]:
        """按实际测点计算每个窗口的状态：已收 / 已领取 / 待补采。"""
        samples = self.sample_timestamps(device_id, point_id)
        gap_index = self._gap_index(device_id, point_id)
        records: list[dict[str, Any]] = []
        for moment in expected_windows(start, end, step_seconds):
            moment_text = canonical(moment)
            window_end = moment + timedelta(seconds=max(1, int(step_seconds)))
            gap = gap_index.get(moment_text)
            if moment_text in samples or (gap is not None and self._gap_is_received(gap)):
                state = WINDOW_RECEIVED
            elif gap is not None and str(gap.get("处理状态", "")) in (
                WINDOW_CLAIMED, "补采中"
            ):
                state = WINDOW_CLAIMED
            else:
                state = WINDOW_PENDING
            records.append({
                "时刻": moment_text,
                "窗口起始": moment_text,
                "窗口结束": canonical(window_end),
                "时隙": f"{moment.strftime('%H:%M')}-{window_end.strftime('%H:%M')}",
                "状态": state,
                "上一状态": (gap or {}).get("上次处理状态", ""),
                "记录编号": (gap or {}).get("记录编号", ""),
                "领取轮次": int((gap or {}).get("领取轮次", 0) or 0),
                "领取申请": (gap or {}).get("领取申请", ""),
                "回执标识": (gap or {}).get("回执标识", ""),
                "回执时间": (gap or {}).get("回执时间", ""),
                "冲突处理": (gap or {}).get("冲突处理", ""),
            })
        return records

    @staticmethod
    def pending_from_progress(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [record for record in records if record.get("状态") != WINDOW_RECEIVED]

    # ------------------------------------------------------------------
    # 补采派工：按待补区间领取
    # ------------------------------------------------------------------

    def claim_windows(
        self,
        device_id: str,
        point_id: str,
        windows: Iterable[datetime],
        *,
        request_id: str = "",
        step_seconds: int = 600,
        strategy: str = "自动补采",
    ) -> dict[str, Any]:
        """按「设备 + 测点 + 采样时刻」领取待补区间。

        已收到样本的区间直接跳过；仍缺的区间若已有旧一轮领取记录，则递增
        领取轮次并交给新的请求，旧的领取记录不会被重复派工。
        """
        step = timedelta(seconds=max(1, int(step_seconds)))
        gap_rows = store.rows(self.MODULE)
        samples = self.sample_timestamps(device_id, point_id)
        index = self._gap_index(device_id, point_id)

        claimed: list[dict[str, Any]] = []
        already_received: list[str] = []
        already_claimed: list[str] = []
        for moment in windows:
            moment_text = canonical(moment)
            if moment_text in samples:
                already_received.append(moment_text)
                continue
            gap = index.get(moment_text)
            if gap is not None and self._gap_is_received(gap):
                already_received.append(moment_text)
                continue
            if gap is None:
                max_id = max((int(row.get("id", 0)) for row in gap_rows), default=0)
                gap = {
                    "id": max_id + 1,
                    "记录编号": f"GAP-{max_id + 1:04d}",
                    "设备编号": device_id,
                    "测点编号": point_id,
                    "中断起始": moment_text,
                    "中断结束": canonical(moment + step),
                    "采样时刻": moment_text,
                    "缺失点数": "1",
                    "补采策略": strategy,
                    "处理状态": WINDOW_CLAIMED,
                    "领取申请": request_id,
                    "领取轮次": 1,
                    "上次处理状态": WINDOW_PENDING,
                    "回执标识": "",
                    "回执时间": "",
                    "回执数值": "",
                    "冲突处理": "",
                    "status": "补采中",
                    "pending": True,
                    "abnormal": False,
                }
                gap_rows.append(gap)
                index[moment_text] = gap
            else:
                previous = str(gap.get("处理状态", ""))
                if (
                    request_id
                    and str(gap.get("领取申请", "")) == request_id
                    and previous in (WINDOW_CLAIMED, "补采中")
                ):
                    already_claimed.append(moment_text)
                    continue
                # 同一申请编号下的每次续办都算新一轮派工，旧轮次的迟到回执
                # 会被 receive_receipt 判为冲突。
                gap["上次处理状态"] = previous
                gap["处理状态"] = WINDOW_CLAIMED
                gap["领取申请"] = request_id
                gap["领取轮次"] = int(gap.get("领取轮次", 0) or 0) + 1
                gap["status"] = "补采中"
                gap["pending"] = True
            claimed.append({
                "时刻": moment_text,
                "时隙": f"{moment.strftime('%H:%M')}-{(moment + step).strftime('%H:%M')}",
                "记录编号": gap.get("记录编号", ""),
                "领取轮次": int(gap.get("领取轮次", 0) or 0),
                "领取申请": gap.get("领取申请", ""),
                "状态": WINDOW_CLAIMED,
            })
        return {
            "ok": True,
            "device_id": device_id,
            "point_id": point_id,
            "request_id": request_id,
            "claimed": claimed,
            "claimed_intervals": [item["时刻"] for item in claimed],
            "already_received": already_received,
            "already_claimed": already_claimed,
            "message": (
                f"已领取 {len(claimed)} 个待补区间"
                if claimed else "没有需要领取的待补区间"
            ),
        }

    # ------------------------------------------------------------------
    # 回执签收
    # ------------------------------------------------------------------

    def _find_receipt(self, receipt_id: str) -> dict[str, Any] | None:
        for row in self._receipt_rows():
            if str(row.get("回执标识", "")) == str(receipt_id):
                return row
        return None

    def _find_receipt_by_moment(
        self, device_id: str, point_id: str, moment_text: str
    ) -> dict[str, Any] | None:
        for row in self._receipt_rows():
            if (
                str(row.get("设备编号", "")) == str(device_id)
                and str(row.get("测点编号", "")) == str(point_id)
                and str(row.get("采样时刻", "")) == moment_text
            ):
                return row
        return None

    def _matching_applications(
        self, device_id: str, point_id: str, moment: datetime
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for task in store.rows("downsample"):
            if str(task.get("设备编号", "")) != str(device_id):
                continue
            if str(task.get("源测点", "")) != str(point_id):
                continue
            start = parse_moment(task.get("开始时刻"))
            end = parse_moment(task.get("截止时刻"))
            if start is None or end is None:
                continue
            if start <= moment < end:
                matches.append(task)
        return matches

    def receive_receipt(
        self,
        device_id: str,
        point_id: str,
        timestamp: Any,
        value: Any = None,
        *,
        receipt_id: str = "",
        request_id: str = "",
        round_no: int | str | None = None,
        source: str = "补采",
        remark: str = "",
    ) -> dict[str, Any]:
        """接收一条补采回执（签收）。

        回执的归属键是设备、测点与采样时刻；同一份回执重复到达时只做审计
        记录，不会再次登记样本，也不会重复减少任何申请的剩余缺口。
        """
        moment = parse_moment(timestamp)
        if moment is None:
            return {
                "ok": False,
                "message": f"无法识别采样时刻：{timestamp!r}",
                "duplicated": False,
                "applied": False,
            }
        moment_text = canonical(moment)

        existing_receipt = None
        if receipt_id:
            existing_receipt = self._find_receipt(receipt_id)
        else:
            existing_receipt = self._find_receipt_by_moment(
                device_id, point_id, moment_text
            )
        if existing_receipt is not None:
            receipt = self._log_receipt(
                device_id, point_id, moment_text, value, receipt_id,
                request_id, round_no, source, remark,
                duplicated=True,
                applied=False,
                note="重复回执，不重复扣减缺口",
            )
            return {
                "ok": True,
                "message": "同一签收记录已处理过，本次不重复扣减缺口",
                "duplicated": True,
                "applied": False,
                "pending_changed": False,
                "receipt": receipt,
                "matched_tasks": [],
                "timestamp": moment_text,
            }

        same_moment_sample = self.has_sample(device_id, point_id, moment)
        if same_moment_sample:
            # 同一设备/测点/时刻已经补回过样本：这条回执只做审计，
            # 不再算作一次新的「补齐」，避免重复扣减。
            receipt = self._log_receipt(
                device_id, point_id, moment_text, value, receipt_id,
                request_id, round_no, source, remark,
                duplicated=True,
                applied=False,
                note="该采样时刻已有样本，回执仅记录不重复扣减",
            )
            return {
                "ok": True,
                "message": "该采样时刻已经有了补回的样本，本次回执不重复扣减缺口",
                "duplicated": True,
                "applied": False,
                "pending_changed": False,
                "receipt": receipt,
                "matched_tasks": [],
                "timestamp": moment_text,
            }

        gap_index = self._gap_index(device_id, point_id)
        gap = gap_index.get(moment_text)
        prior_state = str((gap or {}).get("处理状态", "")) if gap else ""

        claim_round = int((gap or {}).get("领取轮次", 0) or 0)
        try:
            incoming_round = int(round_no) if round_no not in (None, "") else None
        except (TypeError, ValueError):
            incoming_round = None
        claim_owner = str((gap or {}).get("领取申请", "")) if gap else ""
        conflict = False
        conflict_reason = ""
        if gap is not None and not self._gap_is_received(gap):
            if request_id and claim_owner and request_id != claim_owner:
                conflict = True
                conflict_reason = (
                    f"迟到回执属于请求 {request_id}，当前区间已由 {claim_owner} 领取"
                )
            elif incoming_round is not None and claim_round > incoming_round:
                conflict = True
                conflict_reason = (
                    f"迟到回执来自第 {incoming_round} 轮，当前区间已进入第 {claim_round} 轮"
                )

        sample, sample_created = self.record_sample(
            device_id, point_id, moment, value,
            source=source, receipt_id=receipt_id,
        )

        pending_changed = False
        if gap is not None:
            if not self._gap_is_received(gap):
                pending_changed = True
            gap["上次处理状态"] = prior_state or gap.get("上次处理状态", "")
            gap["处理状态"] = WINDOW_RECEIVED
            gap["status"] = "已补齐"
            gap["pending"] = False
            gap["回执标识"] = receipt_id or gap.get("回执标识", "")
            gap["回执时间"] = _now()
            gap["回执数值"] = "" if value is None else value
            if conflict:
                gap["冲突处理"] = conflict_reason
        elif sample_created:
            pending_changed = True

        matched_tasks = self._matching_applications(device_id, point_id, moment)
        matched_summary = [
            {
                "task_id": task.get("id"),
                "任务编号": task.get("任务编号"),
                "设备编号": task.get("设备编号"),
                "测点编号": task.get("源测点"),
            }
            for task in matched_tasks
        ]
        receipt = self._log_receipt(
            device_id, point_id, moment_text, value, receipt_id,
            request_id, round_no, source, remark,
            duplicated=False,
            applied=bool(matched_tasks and (pending_changed or sample_created)),
            note=conflict_reason or ("已按采样时刻归属" if matched_tasks else "未匹配到当前申请时段"),
            conflict=conflict,
            matched_tasks=matched_summary,
        )
        return {
            "ok": True,
            "message": (
                f"回执已签收，采样时刻 {moment_text}"
                + ("（迟到回执，已归入该时刻）" if conflict else "")
            ),
            "duplicated": False,
            "applied": bool(matched_tasks and (pending_changed or sample_created)),
            "pending_changed": pending_changed or sample_created,
            "conflict": conflict,
            "conflict_reason": conflict_reason,
            "receipt": receipt,
            "sample": sample,
            "sample_created": sample_created,
            "matched_tasks": matched_summary,
            "timestamp": moment_text,
        }

    def _log_receipt(
        self,
        device_id: str,
        point_id: str,
        moment_text: str,
        value: Any,
        receipt_id: str,
        request_id: str,
        round_no: Any,
        source: str,
        remark: str,
        *,
        duplicated: bool,
        applied: bool,
        note: str,
        conflict: bool = False,
        matched_tasks: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rows = self._receipt_rows()
        max_id = max((int(row.get("id", 0)) for row in rows), default=0)
        next_id = max_id + 1
        final_receipt_id = receipt_id or f"RCPT-{next_id:04d}"
        row = {
            "id": next_id,
            "回执标识": final_receipt_id,
            "设备编号": device_id,
            "测点编号": point_id,
            "采样时刻": moment_text,
            "数值": "" if value is None else value,
            "来源": source,
            "申请编号": request_id,
            "领取轮次": "" if round_no is None else round_no,
            "签收时间": _now(),
            "是否重复": duplicated,
            "是否应用": applied,
            "冲突": conflict,
            "备注": note,
            "关联申请": matched_tasks or [],
            "remark": remark,
            "status": "已签收",
            "pending": False,
            "abnormal": conflict,
        }
        rows.append(row)
        return row

    # ------------------------------------------------------------------
    # 申请回执视图
    # ------------------------------------------------------------------

    def receipts_for(
        self,
        device_id: str,
        point_id: str,
        start: datetime,
        end: datetime,
        *,
        include_others: bool = True,
    ) -> list[dict[str, Any]]:
        """列出回执，并标明它是否能用于当前申请。

        另一设备、另一测点、或落在 ``[start, end)`` 之外的回执会保留在
        ``unrelated_receipts`` 里，但 ``matches_request`` 为 False，绝不参与
        当前申请的缺口计算。
        """
        result: list[dict[str, Any]] = []
        for row in self._receipt_rows():
            moment = parse_moment(row.get("采样时刻"))
            matches = False
            reason = ""
            if moment is None:
                reason = "回执时刻无法识别"
            elif str(row.get("设备编号", "")) != str(device_id):
                reason = "设备不匹配"
            elif str(row.get("测点编号", "")) != str(point_id):
                reason = "测点不匹配"
            elif not (start <= moment < end):
                reason = "采样时刻不在申请时段内（截止时刻不含）"
            else:
                matches = True
            if not matches and not include_others:
                continue
            result.append({
                **row,
                "matches_request": matches,
                "match_reason": "" if matches else reason,
            })
        return result

    # ------------------------------------------------------------------
    # 通用动作扩展
    # ------------------------------------------------------------------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, message = super().run_action(entry_id, action)
        if entry is None:
            return entry, message
        if action == "发起补采":
            entry["上次处理状态"] = entry.get("处理状态", "")
            entry["处理状态"] = WINDOW_CLAIMED
            entry["领取轮次"] = int(entry.get("领取轮次", 0) or 0) + 1
            entry["status"] = "补采中"
        elif action == "标记补齐":
            entry["处理状态"] = WINDOW_RECEIVED
        elif action == "放弃补采":
            entry["处理状态"] = "放弃补采"
        return entry, message


data_gap_service = DataGapService()

"""跨模块数据流统一接口：暴露9组数据流的操作端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.cross_module import cross_service

router = APIRouter(prefix="/api/cross", tags=["跨模块数据流"])


# ==================== Flow 1: 设备注册与协议适配 ====================

@router.post("/flow1/register")
def flow1_register_device(
    device_id: str = Query(..., description="设备编号"),
    device_type: str = Query(..., description="设备类型"),
    protocol_id: str = Query(..., description="协议配置编号"),
):
    """设备注册时自动绑定协议配置，校验协议状态。"""
    return cross_service.register_device_with_protocol(device_id, device_type, protocol_id)


@router.get("/flow1/status")
def flow1_device_protocol_status(device_id: str | None = None):
    """查询设备-协议关联状态矩阵。"""
    return {"data": cross_service.query_device_protocol_status(device_id)}


# ==================== Flow 2: 报文解码与消息路由 ====================

@router.post("/flow2/decode-route")
def flow2_decode_and_route(
    raw_message: str = Query(..., description="原始报文内容"),
    protocol_type: str = Query(..., description="协议类型"),
    topic: str = Query(..., description="源Topic"),
):
    """报文解码后按路由规则分发到目标Topic。"""
    return cross_service.decode_and_route(raw_message, protocol_type, topic)


# ==================== Flow 3: 时序测点与存储策略 ====================

@router.post("/flow3/configure")
def flow3_configure_point_store(
    point_id: str = Query(..., description="测点编号"),
    device_id: str = Query(..., description="设备编号"),
    point_name: str = Query(..., description="测点名称"),
    storage_engine: str = Query(..., description="存储引擎"),
):
    """为测点配置存储策略，自动关联设备档案。"""
    return cross_service.configure_point_with_store(point_id, device_id, point_name, storage_engine)


@router.get("/flow3/matrix")
def flow3_point_store_matrix():
    """查询测点-存储策略关联矩阵。"""
    return {"data": cross_service.query_point_store_matrix()}


# ==================== Flow 4: 降采样与断点补采 ====================

@router.post("/flow4/downsample")
def flow4_downsample_gap_check(
    source_point: str = Query(..., description="源测点"),
    target_granularity: str = Query(..., description="目标粒度"),
    agg_function: str = Query(..., description="聚合函数"),
    time_start: str | None = None,
    time_end: str | None = None,
    device_id: str | None = Query(default=None, description="设备编号"),
    date: str | None = Query(default=None, description="申请日期，例如 2026-11-20"),
    time_range: str | None = Query(default=None, description="申请时段，例如 10:00-10:30"),
    request_id: str | None = Query(default=None, description="申请编号（续办时沿用）"),
):
    """降采样前先检查数据断点，存在断点则自动发起补采。"""
    return cross_service.downsample_with_gap_check(
        source_point, target_granularity, agg_function, time_start, time_end,
        device_id=device_id, date=date, time_range=time_range, request_id=request_id,
    )


@router.post("/flow4/downsample/{task_ref}/execute")
def flow4_downsample_execute(task_ref: str):
    """执行降采样：只使用该测点真正补回的样本，仍有缺口则保持待补。"""
    from app.services.downsample import DownsampleService

    result = DownsampleService().execute_downsample(task_ref)
    detail = cross_service.downsample_detail(task_ref)
    result["detail"] = detail
    if detail is None and not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "任务不存在"))
    return result


@router.get("/flow4/workbench")
def flow4_gap_workbench():
    """补采工作台：按申请列出服务端计算的待补范围。"""
    return cross_service.gap_workbench()


@router.get("/flow4/downsample/{task_ref}")
def flow4_downsample_detail(task_ref: str):
    """降采样申请详情：逐窗口状态、待补范围与各自归属的回执。"""
    detail = cross_service.downsample_detail(task_ref)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"降采样申请 {task_ref} 不存在")
    return detail


@router.post("/flow4/downsample/{task_ref}/claim")
def flow4_downsample_claim(
    task_ref: str,
    request_id: str | None = Query(default=None, description="续办请求编号"),
):
    """续办补采：只对当前申请里仍然缺的区间向数据断点服务领取。"""
    result = cross_service.claim_downsample_gaps(task_ref, request_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "续办失败"))
    return result


@router.get("/flow4/downsample/{task_ref}/receipts")
def flow4_downsample_receipts(task_ref: str):
    """申请的回执列表：区分当前申请的回执与其它设备/测点/时段外的回执。"""
    detail = cross_service.downsample_detail(task_ref)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"降采样申请 {task_ref} 不存在")
    return {
        "task_id": detail["task_id"],
        "任务编号": detail["任务编号"],
        "receipts": detail["receipts"],
        "unrelated_receipts": detail["unrelated_receipts"],
    }


@router.post("/flow4/recovery")
def flow4_gap_recovery(
    device_id: str = Query(..., description="设备编号"),
    point_id: str = Query(..., description="测点编号"),
    date: str | None = Query(default=None, description="补采日期"),
    time_start: str | None = Query(default=None, description="时段起点（含）"),
    time_end: str | None = Query(default=None, description="时段终点（不含）"),
    time_range: str | None = Query(default=None, description="时段，例如 10:00-10:30"),
    target_granularity: str | None = Query(default=None, description="检查粒度"),
):
    """显式触发断点补采：按设备与测点的待补区间领取。"""
    return cross_service.trigger_gap_recovery(
        device_id, point_id, date=date, time_start=time_start, time_end=time_end,
        time_range=time_range, target_granularity=target_granularity,
    )


@router.post("/flow4/receipt")
def flow4_backfill_receipt(
    device_id: str = Query(..., description="设备编号"),
    point_id: str = Query(..., description="测点编号"),
    timestamp: str = Query(..., description="采样时刻，例如 2026-11-20T10:10:00"),
    value: float | None = None,
    receipt_id: str = Query(default="", description="回执标识，用于识别重复签收"),
    request_id: str = Query(default="", description="回执所属的补采请求"),
    round_no: int | None = Query(default=None, description="回执所属的领取轮次"),
    source: str = Query(default="补采", description="样本来源"),
    remark: str = "",
):
    """补采回执签收：同一份回执重复到达只记录一次。"""
    return cross_service.receive_backfill_receipt(
        device_id, point_id, timestamp, value,
        receipt_id=receipt_id, request_id=request_id, round_no=round_no,
        source=source, remark=remark,
    )


# ==================== Flow 5: 告警触发与联动规则 ====================

@router.post("/flow5/evaluate")
def flow5_evaluate_alarm(
    point_id: str = Query(..., description="测点编号"),
    current_value: float = Query(..., description="当前值"),
):
    """当测点值触发告警时，自动执行关联的联动规则。"""
    return cross_service.evaluate_alarm_with_rule(point_id, current_value)


# ==================== Flow 6: 设备影子与标签绑定 ====================

@router.post("/flow6/sync-shadow")
def flow6_sync_shadow_tags(
    device_id: str = Query(..., description="设备编号"),
    desired_state: str = Query(..., description="期望状态"),
):
    """设备影子同步时自动关联标签。"""
    return cross_service.sync_device_shadow_with_tags(device_id, desired_state)


@router.post("/flow6/bind-tag")
def flow6_bind_tag_shadow(
    device_id: str = Query(..., description="设备编号"),
    tag_key: str = Query(..., description="标签键"),
    tag_value: str = Query(..., description="标签值"),
):
    """绑定标签时校验设备影子状态。"""
    return cross_service.bind_tag_with_shadow_check(device_id, tag_key, tag_value)


# ==================== Flow 7: 网关管理与边缘缓存 ====================

@router.post("/flow7/cache")
def flow7_gateway_cache(
    gateway_id: str = Query(..., description="网关编号"),
    cache_size: str = Query(default="256MB", description="缓存大小"),
    eviction_strategy: str = Query(default="LRU", description="淘汰策略"),
):
    """管理网关边缘缓存配置。"""
    return cross_service.manage_gateway_cache(gateway_id, cache_size, eviction_strategy)


@router.get("/flow7/matrix")
def flow7_gateway_cache_matrix():
    """查询网关-缓存关联矩阵。"""
    return {"data": cross_service.query_gateway_cache_matrix()}


# ==================== Flow 8: 固件升级与聚合分发 ====================

@router.post("/flow8/ota-dispatch")
def flow8_ota_aggregate(
    target_device: str = Query(..., description="目标设备"),
    firmware_version: str = Query(..., description="固件版本"),
    upgrade_strategy: str = Query(default="rolling", description="升级策略"),
    target_count: int = Query(default=10, description="目标设备数"),
):
    """固件升级任务创建后，自动创建聚合分发任务。"""
    return cross_service.ota_with_aggregate_dispatch(
        target_device, firmware_version, upgrade_strategy, target_count
    )


@router.get("/flow8/status")
def flow8_ota_aggregate_status(ota_task_id: str | None = None):
    """查询固件升级与聚合分发关联状态。"""
    return {"data": cross_service.query_ota_aggregate_status(ota_task_id)}


# ==================== Flow 9: 数据流管道与批量导出 ====================

@router.post("/flow9/export")
def flow9_stream_export(
    stream_id: str = Query(..., description="管道编号"),
    device_range: str = Query(..., description="设备范围"),
    point_list: str = Query(..., description="测点列表"),
    time_start: str | None = None,
    time_end: str | None = None,
    export_format: str = Query(default="CSV", description="导出格式"),
):
    """从数据流管道触发批量导出。"""
    return cross_service.stream_to_batch_export(
        stream_id, device_range, point_list, time_start, time_end, export_format
    )


@router.get("/flow9/matrix")
def flow9_stream_export_matrix():
    """查询数据流管道与批量导出关联矩阵。"""
    return {"data": cross_service.query_stream_export_matrix()}


# ==================== 跨模块概览 ====================

@router.get("/overview")
def cross_overview():
    """跨模块数据流总览。"""
    return cross_service.cross_module_overview()
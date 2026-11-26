/** 降采样与补采的接口封装：所有缺口都由服务端按实际测点算好，页面只负责展示与续办。 */

export type WindowState = '待补采' | '已领取' | '已收'

export type WindowProgress = {
  时刻: string
  窗口起始: string
  窗口结束: string
  时隙: string
  状态: WindowState | string
  上一状态?: string
  记录编号?: string
  领取轮次?: number
  领取申请?: string
  回执标识?: string
  回执时间?: string
  冲突处理?: string
}

export type ReceiptRow = {
  id: number
  回执标识: string
  设备编号: string
  测点编号: string
  采样时刻: string
  数值?: string | number
  申请编号?: string
  领取轮次?: string | number
  签收时间?: string
  是否重复?: boolean
  是否应用?: boolean
  冲突?: boolean
  备注?: string
  matches_request?: boolean
  match_reason?: string
}

export type SamplePoint = {
  时刻: string
  数值?: string | number
  来源?: string
}

export type PendingRange = {
  时刻: string
  时隙: string
  状态: string
  领取轮次?: number
}

export type DownsampleDetail = {
  ok: boolean
  task_id: number
  任务编号: string
  申请编号: string
  设备编号: string
  测点编号: string
  开始时刻: string
  截止时刻: string
  目标粒度: string
  窗口数: number
  已收数量: number
  已领取数量: number
  缺失点数: number
  missing_count: number
  device_id: string
  待补区间: PendingRange[]
  progress: WindowProgress[]
  receipts: ReceiptRow[]
  unrelated_receipts: ReceiptRow[]
  sample_points: SamplePoint[]
  execution_status: string
  completed: boolean
  can_complete: boolean
}

export type DownsampleApplyResult = {
  ok: boolean
  message: string
  request_id?: string
  device_id?: string
  start?: string
  end?: string
  granularity?: string
  step_seconds?: number
  downsample_task?: { id: number; 任务编号: string; 申请编号?: string }
  progress?: WindowProgress[]
  missing?: WindowProgress[]
  estimated_missing?: number
  detail?: DownsampleDetail | null
}

export type ClaimResult = {
  ok: boolean
  message: string
  task_id?: number
  任务编号?: string
  request_id?: string
  claimed?: { 时刻: string; 时隙: string; 记录编号?: string; 领取轮次?: number }[]
  claimed_intervals?: string[]
  already_received?: string[]
  already_claimed?: string[]
  detail?: DownsampleDetail | null
}

export type WorkbenchApplication = {
  task_id: number
  任务编号: string
  申请编号: string
  设备编号: string
  测点编号: string
  开始时刻: string
  截止时刻: string
  目标粒度: string
  缺失点数: number
  已领取数量: number
  已收数量: number
  执行状态: string
}

export type WorkbenchRange = {
  task_id: number
  任务编号: string
  申请编号: string
  设备编号: string
  测点编号: string
  时刻: string
  时隙: string
  状态: string
  领取轮次?: number
}

export type WorkbenchResult = {
  applications: WorkbenchApplication[]
  ranges: WorkbenchRange[]
  pending_count: number
  generated_at: string
}

export type ReceiptResult = {
  ok: boolean
  message: string
  duplicated: boolean
  applied: boolean
  pending_changed: boolean
  conflict?: boolean
  conflict_reason?: string
  timestamp?: string
  receipt?: ReceiptRow
  task_details?: DownsampleDetail[]
}

import { request } from './client'

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value) !== '') {
      search.set(key, String(value))
    }
  }
  return search.toString()
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    let detail = fallback
    try {
      const payload = await response.json()
      if (payload?.detail) detail = typeof payload.detail === 'string' ? payload.detail : fallback
    } catch {
      // 保持兜底说明
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export type ApplyForm = {
  deviceId: string
  sourcePoint: string
  targetGranularity: string
  aggFunction: string
  date: string
  timeStart: string
  timeEnd: string
  requestId?: string
}

export async function applyDownsample(form: ApplyForm): Promise<DownsampleApplyResult> {
  const params = query({
    device_id: form.deviceId,
    source_point: form.sourcePoint,
    target_granularity: form.targetGranularity,
    agg_function: form.aggFunction,
    date: form.date,
    time_start: form.timeStart,
    time_end: form.timeEnd,
    request_id: form.requestId,
  })
  const response = await request(`/api/cross/flow4/downsample?${params}`, { method: 'POST' })
  return await readJson<DownsampleApplyResult>(response, '降采样申请提交失败')
}

export async function loadDownsampleDetail(taskRef: string | number): Promise<DownsampleDetail> {
  const response = await request(`/api/cross/flow4/downsample/${taskRef}`)
  return await readJson<DownsampleDetail>(response, '降采样详情读取失败')
}

export async function claimMissing(taskRef: string | number, requestId?: string): Promise<ClaimResult> {
  const response = await request(
    `/api/cross/flow4/downsample/${taskRef}/claim?${query({ request_id: requestId })}`,
    { method: 'POST' },
  )
  return await readJson<ClaimResult>(response, '续办补采失败')
}

export async function loadWorkbench(): Promise<WorkbenchResult> {
  const response = await request('/api/cross/flow4/workbench')
  return await readJson<WorkbenchResult>(response, '补采工作台读取失败')
}

export type ReceiptForm = {
  deviceId: string
  pointId: string
  timestamp: string
  value?: string
  receiptId?: string
  requestId?: string
  roundNo?: string
}

export async function receiveReceipt(form: ReceiptForm): Promise<ReceiptResult> {
  const params = query({
    device_id: form.deviceId,
    point_id: form.pointId,
    timestamp: form.timestamp,
    value: form.value,
    receipt_id: form.receiptId,
    request_id: form.requestId,
    round_no: form.roundNo,
  })
  const response = await request(`/api/cross/flow4/receipt?${params}`, { method: 'POST' })
  return await readJson<ReceiptResult>(response, '回执签收失败')
}

<template>
  <section class="downsample-panel" data-module="downsample">
    <header class="page-head">
      <div>
        <h3>降采样申请与补采</h3>
        <p class="page-desc">
          提交降采样申请后，按实际测点核对每个 {{ form.targetGranularity }} 窗口。缺口由服务端计算，
          区间含起始时刻、不含截止时刻。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh-detail" type="button" @click="reloadDetail">
          刷新申请详情
        </button>
      </div>
    </header>

    <form class="filter-bar" data-testid="downsample-form" @submit.prevent="submitApplication">
      <label class="filter-item">
        <span>设备编号</span>
        <input v-model="form.deviceId" data-testid="apply-device" placeholder="DEVI-P1" required />
      </label>
      <label class="filter-item">
        <span>源测点</span>
        <input v-model="form.sourcePoint" data-testid="apply-point" placeholder="P1" required />
      </label>
      <label class="filter-item">
        <span>目标粒度</span>
        <input v-model="form.targetGranularity" data-testid="apply-granularity" placeholder="10m" required />
      </label>
      <label class="filter-item">
        <span>聚合函数</span>
        <input v-model="form.aggFunction" data-testid="apply-agg" placeholder="avg" />
      </label>
      <label class="filter-item">
        <span>日期</span>
        <input v-model="form.date" data-testid="apply-date" placeholder="2026-11-20" />
      </label>
      <label class="filter-item">
        <span>起始时刻（含）</span>
        <input v-model="form.timeStart" data-testid="apply-start" placeholder="10:00" />
      </label>
      <label class="filter-item">
        <span>截止时刻（不含）</span>
        <input v-model="form.timeEnd" data-testid="apply-end" placeholder="10:30" />
      </label>
      <button class="btn primary" data-testid="apply-submit" type="submit" :disabled="submitting">
        {{ submitting ? '提交中…' : '提交降采样申请' }}
      </button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="downsample-error">{{ errorMessage }}</p>
    <p v-if="notice" class="status-legend" data-testid="downsample-notice">{{ notice }}</p>

    <div class="stat-row" v-if="detail">
      <article class="stat-card">
        <span class="stat-label">申请编号</span>
        <strong class="stat-value" data-testid="detail-task">{{ detail.任务编号 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">窗口数</span>
        <strong class="stat-value">{{ detail.窗口数 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已收</span>
        <strong class="stat-value" data-testid="detail-received">{{ detail.已收数量 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">尚待补采</span>
        <strong class="stat-value" data-testid="detail-missing">{{ detail.missing_count }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">执行状态</span>
        <strong class="stat-value" data-testid="detail-status">{{ detail.execution_status }}</strong>
      </article>
    </div>

    <template v-if="detail">
      <h3>降采样申请详情</h3>
      <p class="status-legend" data-testid="detail-range">
        申请 {{ detail.申请编号 }} · 设备 {{ detail.设备编号 }} · 测点 {{ detail.测点编号 }} ·
        {{ detail.开始时刻 }} ~ {{ detail.截止时刻 }}（含起始、不含截止）
      </p>
      <h3>窗口明细</h3>
      <table class="data-table" data-testid="window-table">
        <thead>
          <tr><th>时隙</th><th>采样时刻</th><th>状态</th><th>上一状态</th><th>领取轮次</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr
            v-for="item in detail.progress"
            :key="item.时刻"
            :data-testid="`window-${item.时刻}`"
            :data-state="item.状态"
          >
            <td>{{ item.时隙 }}</td>
            <td>{{ item.时刻 }}</td>
            <td>{{ item.状态 }}</td>
            <td>{{ item.上一状态 || '—' }}</td>
            <td>{{ item.领取轮次 || 0 }}</td>
            <td>{{ item.冲突处理 || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="page-actions" style="margin: 12px 0">
        <button
          class="btn primary"
          data-testid="claim-missing"
          type="button"
          :disabled="claiming || !detail.missing_count"
          @click="claimRemaining"
        >
          {{ claiming ? '续办中…' : '续办补采（只领取仍缺区间）' }}
        </button>
        <button
          class="btn"
          data-testid="execute-downsample"
          type="button"
          :disabled="executing"
          @click="executeDownsample"
        >
          {{ executing ? '执行中…' : '执行降采样' }}
        </button>
      </div>

      <h3>补采回执</h3>
      <table class="data-table" data-testid="receipt-table">
        <thead>
          <tr><th>回执标识</th><th>设备</th><th>测点</th><th>采样时刻</th><th>归属</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in detail.receipts" :key="`in-${item.id}`" :data-testid="`receipt-${item.回执标识}`">
            <td>{{ item.回执标识 }}</td>
            <td>{{ item.设备编号 }}</td>
            <td>{{ item.测点编号 }}</td>
            <td>{{ item.采样时刻 }}</td>
            <td>本申请</td>
            <td>{{ item.是否重复 ? '重复回执未重复扣减' : item.备注 || '已计入' }}</td>
          </tr>
          <tr v-for="item in detail.unrelated_receipts" :key="`out-${item.id}`" :data-testid="`receipt-out-${item.回执标识}`">
            <td>{{ item.回执标识 }}</td>
            <td>{{ item.设备编号 }}</td>
            <td>{{ item.测点编号 }}</td>
            <td>{{ item.采样时刻 }}</td>
            <td>其它回执</td>
            <td>{{ item.match_reason }}</td>
          </tr>
          <tr v-if="!detail.receipts.length && !detail.unrelated_receipts.length">
            <td colspan="6" class="empty-state">暂无回执</td>
          </tr>
        </tbody>
      </table>
    </template>

    <h3>补采工作台</h3>
    <table class="data-table" data-testid="workbench-table">
      <thead>
        <tr><th>申请编号</th><th>设备</th><th>测点</th><th>时段</th><th>待补时隙</th><th>缺失点数</th></tr>
      </thead>
      <tbody>
        <tr v-for="task in workbench.applications" :key="task.task_id" :data-testid="`workbench-${task.任务编号}`">
          <td>
            <button class="link" type="button" @click="openTask(task.task_id)">{{ task.任务编号 }}</button>
          </td>
          <td>{{ task.设备编号 }}</td>
          <td>{{ task.测点编号 }}</td>
          <td>{{ task.开始时刻 }} ~ {{ task.截止时刻 }}</td>
          <td>
            <span v-for="range in rangesOf(task.task_id)" :key="range.时刻" class="legend-item">
              {{ range.时隙 }}（{{ range.状态 }}）
            </span>
          </td>
          <td>{{ task.缺失点数 }}</td>
        </tr>
        <tr v-if="!workbench.applications.length">
          <td colspan="6" class="empty-state">暂无待补采的降采样申请</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>工作台共 {{ workbench.pending_count }} 个待补区间</span>
      <span v-if="detail">刷新于 {{ workbench.generated_at }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  applyDownsample,
  claimMissing,
  loadDownsampleDetail,
  loadWorkbench,
  type DownsampleDetail,
  type WorkbenchResult,
  type WorkbenchRange,
} from '@/api/downsample'
import { request } from '@/api/client'

const form = ref({
  deviceId: 'DEVI-P1',
  sourcePoint: 'P1',
  targetGranularity: '10m',
  aggFunction: 'avg',
  date: '2026-11-20',
  timeStart: '10:00',
  timeEnd: '10:30',
})

const detail = ref<DownsampleDetail | null>(null)
const workbench = ref<WorkbenchResult>({ applications: [], ranges: [], pending_count: 0, generated_at: '' })
const errorMessage = ref('')
const notice = ref('')
const submitting = ref(false)
const claiming = ref(false)
const executing = ref(false)

function rangesOf(taskId: number): WorkbenchRange[] {
  return workbench.value.ranges.filter((range) => range.task_id === taskId)
}

async function reloadWorkbench() {
  try {
    workbench.value = await loadWorkbench()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补采工作台读取失败'
  }
}

async function openTask(taskRef: number | string) {
  errorMessage.value = ''
  try {
    detail.value = await loadDownsampleDetail(taskRef)
    await reloadWorkbench()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '降采样详情读取失败'
  }
}

async function reloadDetail() {
  if (detail.value) {
    await openTask(detail.value.task_id)
  } else {
    await reloadWorkbench()
  }
}

async function submitApplication() {
  submitting.value = true
  errorMessage.value = ''
  notice.value = ''
  try {
    const result = await applyDownsample(form.value)
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    if (result.downsample_task) {
      await openTask(result.downsample_task.id)
    }
    await reloadWorkbench()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '降采样申请提交失败'
  } finally {
    submitting.value = false
  }
}

async function claimRemaining() {
  if (!detail.value) return
  claiming.value = true
  errorMessage.value = ''
  try {
    const result = await claimMissing(detail.value.task_id)
    if (!result.ok) throw new Error(result.message)
    notice.value = result.message
    await openTask(detail.value.task_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '续办补采失败'
  } finally {
    claiming.value = false
  }
}

async function executeDownsample() {
  if (!detail.value) return
  executing.value = true
  errorMessage.value = ''
  try {
    const response = await request(`/api/cross/flow4/downsample/${detail.value.task_id}/execute`, {
      method: 'POST',
    })
    const payload = await response.json()
    notice.value = payload.message ?? ''
    if (!payload.ok) throw new Error(payload.message ?? '降采样执行未完成')
    await openTask(detail.value.task_id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '降采样执行失败'
  } finally {
    executing.value = false
  }
}

onMounted(async () => {
  await reloadWorkbench()
  const last = workbench.value.applications[workbench.value.applications.length - 1]
  if (last) await openTask(last.task_id)
})
</script>

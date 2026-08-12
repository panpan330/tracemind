<template>
  <div class="detail-view">
    <el-skeleton v-if="loading && !detail" :rows="6" animated />

    <template v-else-if="detail">
      <el-card shadow="never">
        <div class="header">
          <div>
            <h2 class="title">Incident #{{ detail.id }}:{{ ' ' }}{{ detail.title }}</h2>
            <el-descriptions :column="4" size="small" class="meta">
              <el-descriptions-item label="状态"><StatusTag :status="detail.status" /></el-descriptions-item>
              <el-descriptions-item label="级别">{{ detail.severity }}</el-descriptions-item>
              <el-descriptions-item label="服务">{{ detail.service_ref }}</el-descriptions-item>
              <el-descriptions-item label="创建">{{ detail.created_at }}</el-descriptions-item>
            </el-descriptions>
          </div>
          <div class="header-actions">
            <el-button v-if="detail.status === 'created'" type="primary" data-testid="start-investigation" @click="start">
              开始调查
            </el-button>
            <el-button v-if="isTerminal(detail.status)" type="success" @click="goReport">查看复盘报告</el-button>
            <el-button v-if="detail.id" @click="goReplay">查看历史回放</el-button>
          </div>
        </div>
      </el-card>

      <el-alert
        v-if="detail.status === 'needs_human'"
        title="需人工介入"
        type="warning"
        :closable="false"
        show-icon
        class="alert"
        description="证据不足或恢复验证未通过,系统已暂停,请人工检查。"
      />

      <!-- 降级横幅:模型/RAG 降级时显示,说明报告可能不完整 -->
      <el-alert
        v-if="detail.degraded"
        type="warning"
        :closable="false"
        data-testid="degraded-banner"
        :title="'模型降级: ' + (detail.degradation_reasons || []).join(', ')"
        description="部分步骤由确定性程序执行,复盘报告可能不完整。"
        show-icon
        class="alert"
      />
      <div v-if="detail.termination_reason" class="termination-reason">
        终止原因:{{ detail.termination_reason }}
      </div>

      <el-card shadow="never" class="section">
        <template #header>根因假设</template>
        <HypothesisList :hypotheses="detail.hypotheses" />
      </el-card>

      <el-card shadow="never" class="section">
        <template #header>证据链(E1~E5)</template>
        <EvidenceTable :evidence="detail.evidence" />
      </el-card>

      <el-card v-if="detail.approvals.length" shadow="never" class="section">
        <template #header>修复方案与审批</template>
        <el-descriptions :column="3" size="small">
          <el-descriptions-item label="动作">CREATE_INVENTORY_INDEX</el-descriptions-item>
          <el-descriptions-item label="风险">medium</el-descriptions-item>
          <el-descriptions-item label="审批状态">
            <el-tag size="small">{{ detail.approvals[0].status }}</el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <ApprovalPanel
          v-if="detail.approvals[0]"
          :incident-id="detail.id"
          :approval-id="detail.approvals[0].id"
          :status="detail.approvals[0].status"
          :incident-status="detail.status"
          @decided="refresh"
        />
      </el-card>

      <el-card v-if="detail.fix_execution" shadow="never" class="section">
        <template #header>修复执行</template>
        <el-descriptions :column="2" size="small">
          <el-descriptions-item label="状态">
            <el-tag size="small">{{ detail.fix_execution.status }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="执行 ID">#{{ detail.fix_execution.id }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card v-if="detail.recovery" shadow="never" class="section">
        <template #header>恢复验证</template>
        <el-descriptions :column="3" size="small">
          <el-descriptions-item label="结果">
            <el-tag size="small" :type="detail.recovery.status === 'recovered' ? 'success' : 'danger'">
              {{ detail.recovery.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="索引存在">{{ detail.recovery.index_present ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="命中索引">{{ detail.recovery.query_plan_uses_target_index ? '是' : '否' }}</el-descriptions-item>
        </el-descriptions>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import * as client from '@/api/client'
import type { IncidentDetail } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'
import HypothesisList from '@/components/HypothesisList.vue'
import EvidenceTable from '@/components/EvidenceTable.vue'
import ApprovalPanel from '@/components/ApprovalPanel.vue'
import { useIncidentStream } from '@/composables/useIncidentStream'
import { isTerminal } from '@/utils/status'

const route = useRoute()
const router = useRouter()
const incidentId = Number(route.params.id)

const detail = ref<IncidentDetail | null>(null)
const loading = ref(false)
let timer: number | undefined

// SSE 实时状态:状态变化立即刷新详情;轮询保留作为断线兜底
const { status: liveStatus } = useIncidentStream(incidentId)
watch(liveStatus, (s) => {
  if (s) refresh()
})

async function refresh() {
  try {
    detail.value = await client.getIncident(incidentId)
  } catch (e) {
    ElMessage.error(`加载失败: ${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

async function start() {
  await client.startInvestigation(incidentId)
  ElMessage.success('调查已启动')
  await refresh()
}

function goReport() {
  router.push(`/incidents/${incidentId}/report`)
}

function goReplay() {
  router.push({ path: '/replay', query: { incidentId: incidentId } })
}

onMounted(() => {
  loading.value = true
  refresh()
  // 非终态每 3s 轮询兜底(SSE 断线时);终态后停止
  timer = window.setInterval(() => {
    if (detail.value && !isTerminal(detail.value.status)) refresh()
  }, 3000)
})
onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<style scoped>
.header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.title { margin: 0 0 8px; font-size: 18px; }
.header-actions { display: flex; gap: 8px; }
.alert { margin-top: 16px; }
.section { margin-top: 16px; }
.termination-reason { margin-top: 8px; font-size: 12px; color: var(--el-text-color-secondary); }
</style>

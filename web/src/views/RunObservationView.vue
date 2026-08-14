<template>
  <div class="obs-view" data-testid="run-observation">
    <el-page-header content="运行观测" @back="goBack" />

    <el-card v-if="data" shadow="never" class="summary">
      <template #header>诊断摘要</template>
      <el-tag :type="data.run.status === 'recovered' ? 'success' : 'warning'">
        {{ data.run.status }}
      </el-tag>
      <span v-if="data.diagnosis.terminationReason" class="reason">
        归因:{{ data.diagnosis.terminationReason }}
      </span>
      <div class="anomalies">
        <el-tag v-for="a in data.diagnosis.anomalies" :key="a.type" type="danger" size="small">
          {{ a.type }}
        </el-tag>
      </div>
    </el-card>

    <el-card v-if="data" shadow="never" class="timeline">
      <template #header>时间线</template>
      <div v-for="(item, i) in data.timeline" :key="i" class="tl-item">
        <el-tag size="small" :type="item.type === 'llm' ? '' : item.type === 'tool' ? 'info' : 'warning'">
          {{ item.type }} / {{ item.phase }}
        </el-tag>
        <span class="dur">{{ item.durationMs }}ms</span>
        <span v-if="item.type === 'llm'" class="detail">
          {{ item.detail.node }} tokens {{ item.detail.inputTokens }}/{{ item.detail.outputTokens }}
        </span>
        <span v-else-if="item.type === 'tool'" class="detail">
          {{ item.detail.name }} {{ item.detail.outcome }}
        </span>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchRunObservation } from '@/api/client'
import type { RunObservation } from '@/api/types'

const route = useRoute()
const router = useRouter()
const incidentId = Number(route.params.id)
const runId = Number(route.params.runId)
const data = ref<RunObservation | null>(null)

onMounted(async () => {
  try { data.value = await fetchRunObservation(incidentId, runId) }
  catch { data.value = null }
})
function goBack() { router.push(`/incidents/${incidentId}`) }
</script>

<style scoped>
.obs-view { max-width: 960px; margin: 0 auto; }
.summary, .timeline { margin-top: 16px; }
.reason { margin-left: 12px; color: #606266; }
.anomalies { margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; }
.tl-item { padding: 6px 0; border-bottom: 1px solid #f0f0f0; }
.dur { margin-left: 8px; color: #909399; font-size: 12px; }
.detail { margin-left: 8px; color: #606266; font-size: 13px; }
</style>

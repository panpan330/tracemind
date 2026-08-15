<template>
  <div class="eval-dashboard-view">
    <el-row :gutter="16" class="stat-row">
      <el-col :span="8">
        <el-card shadow="never">
          <el-statistic title="评测总数" :value="evals.length" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <el-statistic title="平均成功率" :value="avgSuccessRate" :precision="0" suffix="%" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never">
          <el-statistic title="平均成本(元)" :value="avgCost" :precision="4" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never">
      <template #header>评测记录(按时间倒序,可看版本趋势)</template>
      <el-table :data="evals" data-testid="evals-table" v-loading="loading">
        <el-table-column prop="created_at" label="时间" width="170" />
        <el-table-column prop="scenario" label="场景" width="100" />
        <el-table-column prop="rounds" label="轮次" width="70" />
        <el-table-column label="成功率" width="160">
          <template #default="{ row }">
            <el-progress :percentage="Math.round(row.success_rate * 100)" />
          </template>
        </el-table-column>
        <el-table-column label="平均耗时" width="110">
          <template #default="{ row }">
            {{ (row.avg_duration_ms / 1000).toFixed(1) }}s
          </template>
        </el-table-column>
        <el-table-column prop="total_cost" label="成本(元)" width="100" />
        <el-table-column label="操作" min-width="80">
          <template #default="{ row }">
            <el-button link type="primary" data-testid="eval-detail-btn" @click="goDetail(row.id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listEvals } from '@/api/client'
import type { EvalRunListItem } from '@/api/types'

const router = useRouter()
const evals = ref<EvalRunListItem[]>([])
const loading = ref(false)

const avgSuccessRate = computed(() => {
  if (!evals.value.length) return 0
  return Math.round((evals.value.reduce((s, e) => s + e.success_rate, 0) / evals.value.length) * 100)
})
const avgCost = computed(() => {
  if (!evals.value.length) return 0
  return evals.value.reduce((s, e) => s + e.total_cost, 0) / evals.value.length
})

function goDetail(id: number) {
  router.push(`/evals/${id}`)
}

onMounted(async () => {
  loading.value = true
  try {
    evals.value = await listEvals()
  } finally {
    loading.value = false
  }
})
</script>

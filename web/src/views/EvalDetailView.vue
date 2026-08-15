<template>
  <div class="eval-detail-view">
    <el-page-header @back="goBack" content="评测详情" />

    <template v-if="detail">
      <el-row :gutter="16" class="stat-row">
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="成功率" :value="Math.round((detail.success_rate || 0) * 100)" suffix="%" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="平均耗时" :value="(detail.avg_duration_ms || 0) / 1000" :precision="1" suffix="s" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="总成本(元)" :value="detail.total_cost || 0" :precision="4" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="模型快照" :value="detail.model_snapshot || '-'" />
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never">
        <template #header>汇总</template>
        <p>{{ detail.summary }}</p>
      </el-card>

      <el-card shadow="never">
        <template #header>轮次明细</template>
        <el-table :data="rounds" data-testid="rounds-table">
          <el-table-column prop="round" label="轮次" width="80" />
          <el-table-column prop="scenario" label="场景" width="120" />
          <el-table-column label="终态" width="120">
            <template #default="{ row }">
              <el-tag :type="row.status === 'recovered' ? 'success' : 'danger'">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="100">
            <template #default="{ row }">
              {{ row.elapsed }}s
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
    <el-empty v-else description="未找到评测记录" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getEval } from '@/api/client'
import type { EvalRunDetail } from '@/api/types'

const route = useRoute()
const router = useRouter()
const detail = ref<EvalRunDetail | null>(null)

const rounds = computed(() => {
  if (!detail.value?.raw_json) return []
  try {
    return JSON.parse(detail.value.raw_json)
  } catch {
    return []
  }
})

function goBack() {
  router.push('/evals')
}

onMounted(async () => {
  const id = Number(route.params.id)
  if (!Number.isNaN(id)) {
    try {
      detail.value = await getEval(id)
    } catch {
      detail.value = null
    }
  }
})
</script>

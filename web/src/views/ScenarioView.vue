<template>
  <div class="scenario-view">
    <el-row :gutter="16">
      <el-col :span="24">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>演示场景 SCN-001(库存查询缺联合索引)</span>
              <el-tag :type="scenarioHealthy ? 'success' : 'danger'" data-testid="scenario-tag">
                {{ scenarioHealthy ? '健康' : '故障' }}
              </el-tag>
            </div>
          </template>
          <div class="scenario-actions">
            <el-button type="danger" plain data-testid="inject-fault" @click="inject">注入故障</el-button>
            <el-button type="success" plain data-testid="reset-scenario" @click="reset">重置环境</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="create-card">
      <template #header>创建故障事件(Incident)</template>
      <el-form :inline="true" @submit.prevent>
        <el-form-item label="标题">
          <el-input v-model="form.title" data-testid="incident-title" placeholder="标题" style="width: 260px" />
        </el-form-item>
        <el-form-item label="服务">
          <el-select v-model="form.service_ref" data-testid="incident-service" style="width: 180px">
            <el-option label="inventory-service" value="inventory-service" />
          </el-select>
        </el-form-item>
        <el-form-item label="级别">
          <el-select v-model="form.severity" style="width: 120px">
            <el-option label="高" value="high" />
            <el-option label="中" value="medium" />
            <el-option label="低" value="low" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="可选" style="width: 240px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" data-testid="create-incident" @click="create">创建并调查</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="list-card">
      <template #header>
        <div class="card-header">
          <span>Incident 列表</span>
          <el-select v-model="filterStatus" clearable placeholder="按状态筛选" style="width: 160px">
            <el-option v-for="(meta, key) in STATUS_META" :key="key" :label="meta.label" :value="key" />
          </el-select>
        </div>
      </template>
      <el-table :data="filteredIncidents" stripe>
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="title" label="标题" min-width="200" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <StatusTag :status="row.status" />
          </template>
        </el-table-column>
        <el-table-column prop="severity" label="级别" width="90" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button size="small" @click="view(row)">查看</el-button>
            <el-button v-if="row.status === 'created'" size="small" type="primary" @click="investigate(row)">开始调查</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import * as client from '@/api/client'
import type { IncidentListItem, IncidentStatus } from '@/api/types'
import StatusTag from '@/components/StatusTag.vue'
import { STATUS_META } from '@/utils/status'

const router = useRouter()
const scenarioHealthy = ref(true)
const incidents = ref<IncidentListItem[]>([])
const filterStatus = ref<IncidentStatus | ''>('')
const form = ref({ title: '', service_ref: 'inventory-service', severity: 'medium', description: '' })
let timer: number | undefined

const filteredIncidents = computed(() =>
  filterStatus.value ? incidents.value.filter((i) => i.status === filterStatus.value) : incidents.value,
)

async function refresh() {
  try {
    const status = await client.getScenarioStatus()
    scenarioHealthy.value = status.indexPresent
  } catch {
    scenarioHealthy.value = false
  }
  try {
    incidents.value = await client.listIncidents()
  } catch (e) {
    ElMessage.error(`加载列表失败: ${(e as Error).message}`)
  }
}

async function inject() {
  await client.injectScenario()
  ElMessage.success('故障已注入')
  await refresh()
}

async function reset() {
  await client.resetScenario()
  ElMessage.success('环境已重置')
  await refresh()
}

async function create() {
  if (!form.value.title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  const created = await client.createIncident({
    title: form.value.title.trim(),
    description: form.value.description || undefined,
    severity: form.value.severity,
    service_ref: form.value.service_ref,
  })
  ElMessage.success(`Incident #${created.id} 已创建`)
  router.push(`/incidents/${created.id}`)
}

function view(row: IncidentListItem) {
  router.push(`/incidents/${row.id}`)
}

async function investigate(row: IncidentListItem) {
  await client.startInvestigation(row.id)
  ElMessage.success('调查已启动')
  await refresh()
}

onMounted(() => {
  refresh()
  timer = window.setInterval(refresh, 5000)
})
onBeforeUnmount(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<style scoped>
.card-header { display: flex; align-items: center; justify-content: space-between; }
.create-card, .list-card { margin-top: 16px; }
.scenario-actions { display: flex; gap: 8px; }
</style>

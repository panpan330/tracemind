<template>
  <div class="report-view">
    <el-page-header content="复盘报告" @back="goBack" />

    <el-skeleton v-if="loading" :rows="5" animated class="section" />

    <el-empty v-else-if="!report" description="报告尚未生成" />

    <template v-else>
      <el-card shadow="never" class="section">
        <template #header>根因</template>
        <p class="root-cause">{{ report.root_cause || '—' }}</p>
      </el-card>

      <el-card shadow="never" class="section">
        <template #header>报告全文</template>
        <pre class="report-content">{{ report.content }}</pre>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as client from '@/api/client'

const route = useRoute()
const router = useRouter()
const incidentId = Number(route.params.id)

const loading = ref(true)
const report = ref<Record<string, unknown> | null>(null)

onMounted(async () => {
  try {
    const d = await client.getIncident(incidentId)
    report.value = d.report
  } catch {
    report.value = null
  } finally {
    loading.value = false
  }
})

function goBack() {
  router.push(`/incidents/${incidentId}`)
}
</script>

<style scoped>
.report-view { max-width: 960px; margin: 0 auto; }
.section { margin-top: 16px; }
.root-cause { margin: 0; font-size: 15px; }
.report-content {
  margin: 0;
  white-space: pre-wrap;
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 13px;
  line-height: 1.7;
}
</style>

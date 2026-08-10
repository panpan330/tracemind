<template>
  <el-table :data="evidence" stripe size="small">
    <el-table-column prop="key" label="证据" width="70" />
    <el-table-column prop="source" label="来源工具" min-width="180" />
    <el-table-column label="判定" width="80">
      <template #default="{ row }">
        <el-tag v-if="row.passed === true" type="success" size="small">通过</el-tag>
        <el-tag v-else-if="row.passed === false" type="danger" size="small">未通过</el-tag>
        <el-tag v-else type="info" size="small">未知</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="详情" min-width="220">
      <template #default="{ row }">
        <code class="detail">{{ JSON.stringify(row.content) }}</code>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import type { EvidenceItem } from '@/api/types'

defineProps<{ evidence: EvidenceItem[] }>()
</script>

<style scoped>
.detail { font-size: 12px; word-break: break-all; }
</style>

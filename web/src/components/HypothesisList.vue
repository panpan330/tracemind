<template>
  <div class="hypothesis-list">
    <el-card v-for="h in hypotheses" :key="h.id" shadow="never" class="hypothesis">
      <div class="row">
        <span class="desc">{{ h.description }}</span>
        <el-tag :type="tagType(h.status)" size="small">{{ statusLabel(h.status) }}</el-tag>
      </div>
    </el-card>
    <el-empty v-if="hypotheses.length === 0" description="暂无假设" :image-size="60" />
  </div>
</template>

<script setup lang="ts">
import type { Hypothesis } from '@/api/types'

defineProps<{ hypotheses: Hypothesis[] }>()

function tagType(status: string): 'success' | 'info' | 'danger' | 'primary' {
  if (status === 'confirmed') return 'success'
  if (status === 'refuted') return 'danger'
  if (status === 'supported') return 'primary'
  return 'info'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    confirmed: '已确认', refuted: '已排除', supported: '有支持', proposed: '待验证', unknown: '未知',
  }
  return map[status] ?? status
}
</script>

<style scoped>
.hypothesis { margin-bottom: 8px; }
.row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.desc { flex: 1; }
</style>

<template>
  <el-tag :type="meta.tag" size="small">{{ meta.label }}</el-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { IncidentStatus } from '@/api/types'
import { STATUS_META } from '@/utils/status'

const props = defineProps<{ status: IncidentStatus }>()

const meta = computed(() => STATUS_META[props.status] ?? { label: props.status, tag: 'info' })
</script>

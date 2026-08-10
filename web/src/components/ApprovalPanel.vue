<template>
  <el-card v-if="showActions" shadow="never" class="approval-panel">
    <template #header>
      <div class="header">
        <span>人工审批</span>
        <el-tag type="warning">待审批</el-tag>
      </div>
    </template>
    <el-input
      v-model="comment"
      type="textarea"
      :rows="2"
      placeholder="审批备注(可选)"
      data-testid="approval-comment"
    />
    <div class="actions">
      <el-button type="success" :loading="submitting" :disabled="submitted" data-testid="approve" @click="decide('approved')">
        批准
      </el-button>
      <el-button type="danger" plain :loading="submitting" :disabled="submitted" data-testid="reject" @click="decide('rejected')">
        拒绝
      </el-button>
      <span v-if="submitted" class="hint">已提交,等待执行</span>
    </div>
  </el-card>
  <el-card v-else-if="finishedStatus" shadow="never" class="approval-panel">
    <el-result icon="info" :title="finishedLabel" />
  </el-card>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { decideApproval } from '@/api/client'

const props = defineProps<{
  incidentId: number
  approvalId: number
  status: string
  incidentStatus: string
}>()

const emit = defineEmits<{ (e: 'decided'): void }>()

const comment = ref('')
const submitting = ref(false)
const submitted = ref(false)

const showActions = computed(() => props.status === 'pending' && props.incidentStatus === 'awaiting_approval')
const finishedStatus = computed(() => ['approved', 'rejected', 'expired', 'consumed'].includes(props.status))
const finishedLabel = computed(() => {
  const map: Record<string, string> = {
    approved: '审批已通过', rejected: '审批已拒绝', expired: '审批已过期', consumed: '审批已使用',
  }
  return map[props.status] ?? props.status
})

async function decide(decision: 'approved' | 'rejected') {
  submitting.value = true
  try {
    await decideApproval(props.incidentId, props.approvalId, decision, comment.value)
    submitted.value = true
    ElMessage.success(decision === 'approved' ? '已批准,正在执行修复' : '已拒绝')
    emit('decided')
  } catch (e) {
    ElMessage.error(`审批失败: ${(e as Error).message}`)
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.approval-panel { margin-top: 16px; border: 1px solid var(--el-color-warning); }
.header { display: flex; align-items: center; justify-content: space-between; }
.actions { margin-top: 12px; display: flex; align-items: center; gap: 8px; }
.hint { color: var(--el-text-color-secondary); font-size: 12px; }
</style>

import type { IncidentStatus } from '@/api/types'

export const STATUS_META: Record<IncidentStatus, { label: string; tag: 'success' | 'warning' | 'danger' | 'info' | 'primary' }> = {
  created: { label: '已创建', tag: 'info' },
  investigating: { label: '调查中', tag: 'primary' },
  awaiting_approval: { label: '待审批', tag: 'warning' },
  executing: { label: '执行中', tag: 'primary' },
  verifying: { label: '验证中', tag: 'primary' },
  recovered: { label: '已恢复', tag: 'success' },
  needs_human: { label: '需人工介入', tag: 'danger' },
  rejected: { label: '已拒绝', tag: 'info' },
  failed: { label: '失败', tag: 'danger' },
}

const TERMINAL: ReadonlySet<IncidentStatus> = new Set(['recovered', 'needs_human', 'rejected', 'failed'])

export function isTerminal(status: IncidentStatus): boolean {
  return TERMINAL.has(status)
}

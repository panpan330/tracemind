export type IncidentStatus =
  | 'created' | 'investigating' | 'awaiting_approval' | 'executing' | 'verifying'
  | 'recovered' | 'needs_human' | 'rejected' | 'failed'

export interface IncidentListItem {
  id: number
  title: string
  status: IncidentStatus
  severity: string
  created_at: string
}

export interface Hypothesis {
  id: number
  description: string
  status: string
}

export interface EvidenceItem {
  id: string
  source: string
  key: string | null
  passed: boolean | null
  content: unknown
}

export interface Approval {
  id: number
  fix_proposal_id: number
  status: string
  approver: string | null
  comment: string | null
  expires_at: string | null
}

export interface FixExecution {
  id: number
  fix_proposal_id: number
  status: string
}

export interface RecoveryCheck {
  id: number
  status: string
  index_present: boolean | null
  query_plan_uses_target_index: boolean | null
  estimated_rows_after: number | null
}

export interface IncidentDetail {
  id: number
  title: string
  status: IncidentStatus
  severity: string
  service_ref: string
  created_at: string
  finished_at: string | null
  hypotheses: Hypothesis[]
  evidence: EvidenceItem[]
  approvals: Approval[]
  fix_execution: FixExecution | null
  recovery: RecoveryCheck | null
  report: Record<string, unknown> | null
}

export interface ScenarioStatus {
  indexPresent: boolean
}

export interface CreateIncidentInput {
  title: string
  description?: string
  severity: string
  service_ref: string
  observed_at?: string
}

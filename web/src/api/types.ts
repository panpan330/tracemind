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
  degraded?: boolean
  degradation_reasons?: string[]
  termination_reason?: string | null
  root_cause_code?: string | null
  policy?: Record<string, string>
  facts?: Record<string, boolean>
  tool_calls?: Array<{ tool_name: string; transport?: string; arguments?: Record<string, unknown> }>
}

export interface ScenarioStatus {
  indexPresent: boolean
  lockHeld?: boolean
  activeScenario?: 'SCN-001' | 'SCN-002' | null
}

export interface CreateIncidentInput {
  title: string
  description?: string
  severity: string
  service_ref: string
  observed_at?: string
}

// ---- V1.5 回放 ----
export type ReplayStepState = 'completed' | 'incomplete' | 'failed' | 'started'

export interface ReplayStep {
  stepIndex: number
  logicalStepId: string
  sourceSequenceNos: number[]
  stepState: ReplayStepState
  stepOutcome: string | null
  stepType: string
  stepTitle: string | null
  stateBefore: Record<string, unknown> | null
  stateAfter: Record<string, unknown> | null
  missingParts: string[]
  decisionSummary: Record<string, unknown>
  operationSummary: Record<string, unknown>
  sourceReferenceSummary: Record<string, unknown>
  actualDurationMs: number
  displayDurationMs: number
}

export interface ReplayRunManifest {
  agentRunId: number
  replayStatus: 'complete' | 'partial' | 'partial_legacy' | 'in_progress' | 'unsupported' | 'unavailable'
  runStatus: string
  runOutcome: string | null
  terminationReason: string | null
  asOfSequenceNo: number
  totalSteps: number | null
  keyStepIndexes: Record<string, number> | null
}

export interface IncidentReplayManifest {
  incidentId: number
  runs: Array<{ agentRunId: number; status: string; finishedAt: string | null }>
  defaultRunId: number | null
}

export interface ReplayStepsResponse {
  replayStatus: string
  totalSteps: number
  keyStepIndexes: Record<string, number> | null
  steps: ReplayStep[]
}

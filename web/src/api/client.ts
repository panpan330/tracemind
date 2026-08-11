import type { CreateIncidentInput, IncidentDetail, IncidentListItem, ScenarioStatus } from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`API ${resp.status}: ${text.slice(0, 200)}`)
  }
  return resp.json() as Promise<T>
}

export function listIncidents(): Promise<IncidentListItem[]> {
  return request('/api/incidents')
}

export function getIncident(id: number): Promise<IncidentDetail> {
  return request(`/api/incidents/${id}`)
}

export function createIncident(input: CreateIncidentInput): Promise<{ id: number; status: string; title: string; service_ref: string }> {
  return request('/api/incidents', { method: 'POST', body: JSON.stringify(input) })
}

export function startInvestigation(id: number): Promise<{ run_id: number; thread_id: string; status: string }> {
  return request(`/api/incidents/${id}/investigations`, { method: 'POST' })
}

export function getRun(id: number, runId: number): Promise<{ run_id: number; status: string; investigation_round: number; tool_call_count: number }> {
  return request(`/api/incidents/${id}/runs/${runId}`)
}

export function decideApproval(incidentId: number, approvalId: number, decision: 'approved' | 'rejected', comment: string): Promise<unknown> {
  return request(`/api/incidents/${incidentId}/approvals/${approvalId}/decision`, {
    method: 'POST',
    body: JSON.stringify({ decision, comment }),
  })
}

export function injectScenario(scenario = 'SCN-001'): Promise<unknown> {
  return request(`/api/demo/scenarios/${scenario}/inject`, { method: 'POST' })
}

export function resetScenario(scenario = 'SCN-001'): Promise<unknown> {
  return request(`/api/demo/scenarios/${scenario}/reset`, { method: 'POST' })
}

export function getScenarioStatus(scenario = 'SCN-001'): Promise<ScenarioStatus> {
  return request(`/api/demo/scenarios/${scenario}/status`)
}

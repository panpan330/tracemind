import { onBeforeUnmount, ref, type Ref } from 'vue'
import type { AgentEventItem, IncidentStatus } from '@/api/types'

export interface IncidentStream {
  status: Ref<IncidentStatus | null>
  events: Ref<AgentEventItem[]>
  lastEventId: Ref<number>
  connected: Ref<boolean>
  close: () => void
}

export const EVENT_LABELS: Record<string, string> = {
  INCIDENT_INGESTED: '事件受理',
  HYPOTHESES_GENERATED: '生成假设',
  EVIDENCE_COLLECTION: '证据采集',
  DIAGNOSIS_EVALUATED: '根因评估',
  FIX_PROPOSED: '提出修复',
  APPROVAL_REQUESTED: '等待审批',
  FIX_EXECUTED: '执行修复',
  RECOVERY_VERIFIED: '恢复验证',
  REPORT_GENERATED: '生成报告',
  REFLECTION_EVALUATED: '反思复盘',
  llm_degraded: '能力降级',
  rag_degraded: '能力降级',
}

export function labelFor(type: string): string {
  return EVENT_LABELS[type] ?? type
}

export function useIncidentStream(incidentId: number): IncidentStream {
  const status = ref<IncidentStatus | null>(null)
  const events = ref<AgentEventItem[]>([])
  const lastEventId = ref(0)
  const connected = ref(false)
  let es: EventSource | null = null
  const seen = new Set<number>()

  function handle(ev: MessageEvent) {
    const seq = Number(ev.lastEventId || 0)
    if (seq > 0) {
      if (seen.has(seq)) return
      seen.add(seq)
      if (seq > lastEventId.value) lastEventId.value = seq
    }
    const event = (ev as unknown as { event?: string }).event ?? 'message'
    let data: Record<string, unknown> = {}
    try { data = JSON.parse(String(ev.data)) } catch { /* 忽略非 JSON */ }
    // V1.14:收集全部事件(去重已由 seen 保证,按 sequence 排序)
    events.value = [...events.value, {
      sequence: seq,
      type: event,
      label: labelFor(event),
      status: (data.status as string | undefined),
      occurredAt: new Date().toISOString(),
    }].sort((a, b) => a.sequence - b.sequence)
    if (event === 'status_changed' || event === 'incident_finished') {
      const s = data.status as IncidentStatus | undefined
      if (s) status.value = s
    }
    if (event === 'incident_finished') close()
  }

  function close() {
    es?.close()
    es = null
    connected.value = false
  }

  es = new EventSource(`/api/incidents/${incidentId}/stream`)
  es.onopen = () => { connected.value = true }
  es.onmessage = handle
  es.onerror = () => { connected.value = false } // 浏览器自动重连

  onBeforeUnmount(close)
  return { status, events, lastEventId, connected, close }
}

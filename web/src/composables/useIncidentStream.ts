import { onBeforeUnmount, ref, type Ref } from 'vue'
import type { IncidentStatus } from '@/api/types'

export interface IncidentStream {
  status: Ref<IncidentStatus | null>
  lastEventId: Ref<number>
  connected: Ref<boolean>
  close: () => void
}

export function useIncidentStream(incidentId: number): IncidentStream {
  const status = ref<IncidentStatus | null>(null)
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
  return { status, lastEventId, connected, close }
}

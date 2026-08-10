import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { useIncidentStream } from './useIncidentStream'

type Handler = (ev: MessageEvent) => void

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: Handler | null = null
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  close() { this.closed = true }

  static dispatch(id: number, event: string, data: string) {
    for (const inst of FakeEventSource.instances) {
      const msg = new MessageEvent('message', { data, lastEventId: String(id) })
      ;(msg as unknown as { event: string }).event = event
      inst.onmessage?.(msg)
    }
  }
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useIncidentStream', () => {
  it('按 event.id 去重并更新状态', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'status_changed', JSON.stringify({ status: 'investigating' }))
    FakeEventSource.dispatch(1, 'status_changed', JSON.stringify({ status: 'investigating' }))
    await nextTick()
    expect(stream.status.value).toBe('investigating')
    FakeEventSource.dispatch(2, 'status_changed', JSON.stringify({ status: 'awaiting_approval' }))
    await nextTick()
    expect(stream.status.value).toBe('awaiting_approval')
    expect(FakeEventSource.instances[0].url).toBe('/api/incidents/3/stream')
    stream.close()
  })

  it('incident_finished 自动关闭连接', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'incident_finished', JSON.stringify({ status: 'recovered' }))
    await nextTick()
    expect(stream.status.value).toBe('recovered')
    expect(FakeEventSource.instances[0].closed).toBe(true)
    stream.close()
  })
})

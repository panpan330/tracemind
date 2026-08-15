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

describe('useIncidentStream events', () => {
  it('收集全部事件并按 sequence 排序', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(2, 'FIX_PROPOSED', JSON.stringify({ run_id: 5 }))
    FakeEventSource.dispatch(1, 'HYPOTHESES_GENERATED', JSON.stringify({ run_id: 5 }))
    await nextTick()
    expect(stream.events.value.length).toBe(2)
    expect(stream.events.value[0].sequence).toBe(1)
    expect(stream.events.value[1].sequence).toBe(2)
    stream.close()
  })

  it('映射中文标签,未知类型显示原始名', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'FIX_PROPOSED', JSON.stringify({ run_id: 5 }))
    FakeEventSource.dispatch(2, 'UNKNOWN_NODE', JSON.stringify({}))
    await nextTick()
    expect(stream.events.value[0].label).toBe('提出修复')
    expect(stream.events.value[1].label).toBe('UNKNOWN_NODE')
    stream.close()
  })

  it('status_changed 记录 status', async () => {
    const stream = useIncidentStream(3)
    FakeEventSource.dispatch(1, 'HYPOTHESES_GENERATED', JSON.stringify({ run_id: 5 }))
    FakeEventSource.dispatch(2, 'status_changed', JSON.stringify({ status: 'investigating' }))
    await nextTick()
    const sc = stream.events.value.find(e => e.type === 'status_changed')
    expect(sc?.status).toBe('investigating')
    stream.close()
  })
})

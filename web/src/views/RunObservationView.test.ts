import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunObservationView from './RunObservationView.vue'
import * as client from '@/api/client'

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }))

vi.mock('@/api/client', () => ({ fetchRunObservation: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '1', runId: '9' } }),
  useRouter: () => ({ push: mockPush }),
}))

const mocked = vi.mocked(client)

beforeEach(() => {
  mockPush.mockClear()
  vi.clearAllMocks()
})

describe('RunObservationView', () => {
  it('渲染诊断摘要与异常徽章', async () => {
    mocked.fetchRunObservation.mockResolvedValue({
      run: { runId: 9, status: 'needs_human' },
      timeline: [{ type: 'llm', phase: 'diagnose', startedAt: null, durationMs: 100,
                   detail: { node: 'diagnose', inputTokens: 10, outputTokens: 5 } }],
      diagnosis: { terminationReason: 'no_progress', bottleneckStep: 'diagnose',
                   anomalies: [{ type: 'duplicate_tool_call', stepId: null, detail: 'get_trace x2' }] }
    } as never)
    const w = mount(RunObservationView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(w.text()).toContain('needs_human')
    expect(w.text()).toContain('duplicate_tool_call')
    expect(w.text()).toContain('diagnose')
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchRunObservation } from '../client'

describe('observation api client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('fetchRunObservation 请求正确 URL 并解析', async () => {
    const fake = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        run: { runId: 9, status: 'recovered' },
        timeline: [{ type: 'llm', phase: 'diagnose', startedAt: null, durationMs: 100,
                     detail: { node: 'diagnose' } }],
        diagnosis: { terminationReason: null, bottleneckStep: 'diagnose', anomalies: [] }
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const out = await fetchRunObservation(1, 9)
    expect(fake).toHaveBeenCalledWith('/api/incidents/1/runs/9/observation', expect.anything())
    expect(out.run.runId).toBe(9)
    expect(out.diagnosis.bottleneckStep).toBe('diagnose')
  })
})

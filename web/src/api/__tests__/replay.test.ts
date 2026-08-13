import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fetchReplaySteps, fetchIncidentReplay } from '../client'

describe('replay api client', () => {
  beforeEach(() => { vi.restoreAllMocks() })

  it('fetchReplaySteps 请求正确 URL 并解析', async () => {
    const fake = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ totalSteps: 1, steps: [{
        stepIndex: 0, logicalStepId: 'a', sourceSequenceNos: [1, 2],
        stepState: 'completed', stepOutcome: 'succeeded',
        stateBefore: { facts: {} }, stateAfter: { facts: { F_INDEX_MISSING: true } },
        decisionSummary: { selectedTool: 'get_trace' }, operationSummary: {},
        sourceReferenceSummary: {}, actualDurationMs: 15, displayDurationMs: 3000,
        missingParts: [] }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const out = await fetchReplaySteps(123, 456)
    expect(fake).toHaveBeenCalledWith('/api/incidents/123/replay/runs/456/steps', expect.anything())
    expect(out.totalSteps).toBe(1)
    expect((out.steps[0].stateAfter?.facts as any)?.['F_INDEX_MISSING']).toBe(true)
  })

  it('fetchIncidentReplay 请求 manifest', async () => {
    const fake = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ incidentId: 1, runs: [], defaultRunId: null }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const m = await fetchIncidentReplay(1)
    expect(fake).toHaveBeenCalledWith('/api/incidents/1/replay', expect.anything())
    expect(m.defaultRunId).toBeNull()
  })
})

describe('replay transport 枚举(V1.7)', () => {
  it('tool_calls transport 承载 mcp_streamable_http 且 Replay 解析不崩', async () => {
    const fake = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ totalSteps: 1, steps: [{
        stepIndex: 0, logicalStepId: 'a', sourceSequenceNos: [1],
        stepState: 'completed', stepOutcome: 'succeeded',
        stateBefore: {}, stateAfter: {},
        decisionSummary: { selectedTool: 'get_index_info' },
        operationSummary: { toolCall: { toolName: 'get_index_info', transport: 'mcp_streamable_http' } },
        sourceReferenceSummary: {}, actualDurationMs: 9, displayDurationMs: 1000, missingParts: []
      }] }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const out = await fetchReplaySteps(7, 8)
    const toolCall = (out.steps[0].operationSummary as any)?.toolCall
    expect(toolCall?.transport).toBe('mcp_streamable_http')
    // 旧枚举值仍被接受(向后兼容)
    expect(typeof toolCall?.transport).toBe('string')
    expect(fake).toHaveBeenCalledWith('/api/incidents/7/replay/runs/8/steps', expect.anything())
  })
})

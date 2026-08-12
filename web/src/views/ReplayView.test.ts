import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createWebHistory } from 'vue-router'
import ReplayView from './ReplayView.vue'
import * as api from '../api/client'

const steps = [
  { stepIndex: 0, logicalStepId: 'a', stepState: 'completed', stepOutcome: 'succeeded',
    stepType: 'INCIDENT_INGESTED', stepTitle: '事件接入',
    stateBefore: { facts: {}, hypotheses: [], exclusion_conditions: {} },
    stateAfter: { facts: {}, hypotheses: [], exclusion_conditions: {} },
    decisionSummary: {}, operationSummary: {}, sourceReferenceSummary: {},
    actualDurationMs: 10, displayDurationMs: 1000, missingParts: [], sourceSequenceNos: [1, 2] },
  { stepIndex: 1, logicalStepId: 'b', stepState: 'incomplete', stepOutcome: null,
    stepType: 'FIX_EXECUTED', stepTitle: '修复执行',
    stateBefore: { facts: {} }, stateAfter: null,
    decisionSummary: {}, operationSummary: {}, sourceReferenceSummary: {},
    actualDurationMs: 0, displayDurationMs: 2500, missingParts: ['stateAfter', 'operationResult'],
    sourceSequenceNos: [3] },
]

const router = createRouter({ history: createWebHistory(), routes: [{ path: '/replay', component: ReplayView }] })

describe('ReplayView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.spyOn(api, 'fetchIncidentReplay').mockResolvedValue({
      incidentId: 1, runs: [{ agentRunId: 10, status: 'completed', finishedAt: 'x' }], defaultRunId: 10 } as any)
    vi.spyOn(api, 'fetchRunManifest').mockResolvedValue({
      agentRunId: 10, replayStatus: 'partial', runStatus: 'terminated',
      runOutcome: 'needs_human', terminationReason: 'no_progress',
      asOfSequenceNo: 3, totalSteps: 2, keyStepIndexes: {} } as any)
    vi.spyOn(api, 'fetchReplaySteps').mockResolvedValue({
      replayStatus: 'partial', totalSteps: 2, keyStepIndexes: {}, steps } as any)
    vi.spyOn(api, 'fetchReplayStepDetail').mockResolvedValue({ logicalStepId: 'a' } as any)
  })

  it('渲染只读提示、时间轴与控制条', async () => {
    const wrapper = mount(ReplayView, { props: { incidentId: 1 }, global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('历史回放')
    expect(wrapper.text()).toContain('只读')
    expect(wrapper.find('[data-testid="replay-play"]').exists()).toBe(true)
  })

  it('incomplete 步骤显示缺失标记', async () => {
    const wrapper = mount(ReplayView, { props: { incidentId: 1 }, global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('缺失') // 时间轴 incomplete 节点标记
  })

  it('点击播放开始播放', async () => {
    const wrapper = mount(ReplayView, { props: { incidentId: 1 }, global: { plugins: [router] } })
    await flushPromises()
    await wrapper.find('[data-testid="replay-play"]').trigger('click')
    expect(wrapper.find('[data-testid="replay-play"]').text()).toContain('暂停')
  })
})

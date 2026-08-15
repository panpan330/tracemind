import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EvalDashboardView from './EvalDashboardView.vue'
import * as client from '@/api/client'

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }))

vi.mock('@/api/client', () => ({
  listEvals: vi.fn(),
  getEval: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mockPush }) }))

const mocked = vi.mocked(client)

beforeEach(() => {
  mockPush.mockClear()
  vi.clearAllMocks()
})

describe('EvalDashboardView', () => {
  it('渲染评测列表', async () => {
    mocked.listEvals.mockResolvedValue([
      { id: 1, created_at: '2026-08-14T08:00:00', scenario: 'SCN-001', rounds: 3,
        success_rate: 0.667, avg_duration_ms: 45000, total_cost: 0.02, model_snapshot: 'qwen3.8-max' },
    ])
    const wrapper = mount(EvalDashboardView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('SCN-001')
    expect(wrapper.text()).toContain('67%')
  })

  it('空列表显示统计卡为 0', async () => {
    mocked.listEvals.mockResolvedValue([])
    const wrapper = mount(EvalDashboardView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('评测总数')
  })

  it('点击详情跳转', async () => {
    mocked.listEvals.mockResolvedValue([
      { id: 7, created_at: '2026-08-14T08:00:00', scenario: 'SCN-002', rounds: 1,
        success_rate: 1.0, avg_duration_ms: 30000, total_cost: 0.01, model_snapshot: 'qwen3.7-flash' },
    ])
    const wrapper = mount(EvalDashboardView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    const btn = wrapper.find('[data-testid="eval-detail-btn"]')
    await btn.trigger('click')
    expect(mockPush).toHaveBeenCalledWith('/evals/7')
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EvalDetailView from './EvalDetailView.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({
  listEvals: vi.fn(),
  getEval: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '7' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

const mocked = vi.mocked(client)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('EvalDetailView', () => {
  it('渲染指标卡与轮次明细', async () => {
    mocked.getEval.mockResolvedValue({
      id: 7, created_at: '2026-08-14T08:00:00', scenario: 'SCN-002', rounds: 1,
      success_rate: 1.0, avg_duration_ms: 30000, total_cost: 0.01,
      model_snapshot: 'qwen3.7-flash', summary: '1/1 recovered',
      raw_json: '[{"round":1,"scenario":"SCN-002","status":"recovered","elapsed":30.0}]',
    })
    const wrapper = mount(EvalDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('100%')
    expect(wrapper.text()).toContain('1/1 recovered')
    const rows = wrapper.findAll('[data-testid="rounds-table"] tr')
    expect(rows.length).toBeGreaterThan(1)   // 表头 + 数据行
  })

  it('加载失败显示空态', async () => {
    mocked.getEval.mockRejectedValue(new Error('404'))
    const wrapper = mount(EvalDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('未找到评测记录')
  })
})

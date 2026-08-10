import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ScenarioView from './ScenarioView.vue'
import * as client from '@/api/client'

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }))

vi.mock('@/api/client', () => ({
  getScenarioStatus: vi.fn(),
  injectScenario: vi.fn(),
  resetScenario: vi.fn(),
  createIncident: vi.fn(),
  listIncidents: vi.fn(),
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: mockPush }) }))

const mocked = vi.mocked(client)

beforeEach(() => {
  mockPush.mockClear()
  vi.clearAllMocks()
})

describe('ScenarioView', () => {
  it('渲染场景状态与 Incident 列表', async () => {
    mocked.getScenarioStatus.mockResolvedValue({ indexPresent: true })
    mocked.listIncidents.mockResolvedValue([
      { id: 1, title: '库存慢查询', status: 'awaiting_approval', severity: 'medium', created_at: '2026-08-10 00:00:00' },
    ])
    const wrapper = mount(ScenarioView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('库存慢查询')
    expect(wrapper.text()).toContain('健康')
  })

  it('创建 Incident 后调用 API 并跳转', async () => {
    mocked.getScenarioStatus.mockResolvedValue({ indexPresent: false })
    mocked.listIncidents.mockResolvedValue([])
    mocked.createIncident.mockResolvedValue({ id: 9, status: 'created', title: 't', service_ref: 'inventory-service' })
    const wrapper = mount(ScenarioView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    await wrapper.find('input[data-testid="incident-title"]').setValue('慢SQL 事件')
    await wrapper.find('[data-testid="create-incident"]').trigger('click')
    await flushPromises()
    expect(mocked.createIncident).toHaveBeenCalled()
    expect(mockPush).toHaveBeenCalledWith('/incidents/9')
  })
})

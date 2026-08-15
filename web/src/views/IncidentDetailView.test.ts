import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import IncidentDetailView from './IncidentDetailView.vue'
import * as client from '@/api/client'
import type { IncidentDetail } from '@/api/types'

const { mockRoute, mockPush } = vi.hoisted(() => ({ mockRoute: { params: { id: '7' } }, mockPush: vi.fn() }))
const { mockES } = vi.hoisted(() => ({
  mockES: { onopen: null, onmessage: null, onerror: null, close: vi.fn() },
}))

vi.mock('@/api/client', () => ({
  getIncident: vi.fn(),
  startInvestigation: vi.fn(),
  getRun: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: mockPush }),
}))

const mocked = vi.mocked(client)

function detail(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    id: 7, title: '库存慢查询', status: 'awaiting_approval', severity: 'high',
    service_ref: 'inventory-service', created_at: '2026-08-10 00:00:00', finished_at: null,
    hypotheses: [{ id: 1, description: '缺少联合索引 idx_sku_warehouse 导致慢查询', status: 'confirmed' }],
    evidence: [
      { id: '11', source: 'get_service_metrics', key: 'E1', passed: true, content: { p95Ms: 111 } },
      { id: '15', source: 'get_index_info', key: 'E5', passed: true, content: { indexes: ['PRIMARY'] } },
    ],
    approvals: [{ id: 11, fix_proposal_id: 34, status: 'pending', approver: null, comment: null, expires_at: null }],
    fix_execution: null, recovery: null, report: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('EventSource', vi.fn(() => mockES))
})

describe('IncidentDetailView', () => {
  it('渲染假设、证据与待审批', async () => {
    mocked.getIncident.mockResolvedValue(detail())
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('缺少联合索引')
    expect(wrapper.text()).toContain('E1')
    expect(wrapper.text()).toContain('批准')
  })

  it('created 状态显示开始调查按钮', async () => {
    mocked.getIncident.mockResolvedValue(detail({ status: 'created' }))
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('开始调查')
  })

  it('needs_human 显示人工介入提示', async () => {
    mocked.getIncident.mockResolvedValue(detail({ status: 'needs_human' }))
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('需人工介入')
  })
})

describe('Agent 进度面板', () => {
  function dispatchEvent(seq: number, event: string, data: Record<string, unknown>) {
    const msg = new MessageEvent('message', { data: JSON.stringify(data), lastEventId: String(seq) })
    ;(msg as unknown as { event: string }).event = event
    ;(mockES.onmessage as ((ev: MessageEvent) => void) | null)?.(msg)
  }

  it('渲染节点事件时间线', async () => {
    mocked.getIncident.mockResolvedValue(detail({ status: 'investigating' }))
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    dispatchEvent(1, 'HYPOTHESES_GENERATED', { run_id: 5 })
    await flushPromises()
    expect(wrapper.text()).toContain('生成假设')
  })

  it('未知事件类型显示原始名不崩', async () => {
    mocked.getIncident.mockResolvedValue(detail({ status: 'investigating' }))
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    dispatchEvent(1, 'UNKNOWN_NODE', {})
    await flushPromises()
    expect(wrapper.text()).toContain('UNKNOWN_NODE')
  })

  it('recovered 终态显示完成', async () => {
    mocked.getIncident.mockResolvedValue(detail({ status: 'recovered' }))
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    dispatchEvent(1, 'incident_finished', { status: 'recovered' })
    await flushPromises()
    expect(wrapper.text()).toContain('Agent 进度')
  })

  it('无事件显示等待文案', async () => {
    mocked.getIncident.mockResolvedValue(detail({ status: 'created' }))
    const wrapper = mount(IncidentDetailView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('等待 Agent 启动')
  })
})

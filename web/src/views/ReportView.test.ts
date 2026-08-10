import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import ReportView from './ReportView.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({ getIncident: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '7' } }),
  useRouter: () => ({ push: vi.fn() }),
}))

const report = {
  content: '# 复盘报告\n\n## 根因\n缺少联合索引 idx_sku_warehouse 导致慢查询\n\n## 修复执行\n- 执行状态: succeeded\n\n## 恢复验证\n- 结果: recovered\n- 修复后 P95: 2 ms',
  root_cause: '缺少联合索引',
}

describe('ReportView', () => {
  it('渲染报告各区块', async () => {
    vi.mocked(client.getIncident).mockResolvedValue({
      id: 7, title: '慢查询', status: 'recovered', severity: 'medium', service_ref: 'inventory-service',
      created_at: 'x', finished_at: 'y', hypotheses: [], evidence: [], approvals: [],
      fix_execution: null, recovery: null, report,
    })
    const wrapper = mount(ReportView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('复盘报告')
    expect(wrapper.text()).toContain('缺少联合索引')
    expect(wrapper.text()).toContain('succeeded')
  })

  it('报告为空时提示未生成', async () => {
    vi.mocked(client.getIncident).mockResolvedValue({
      id: 7, title: '慢查询', status: 'investigating', severity: 'medium', service_ref: 'inventory-service',
      created_at: 'x', finished_at: null, hypotheses: [], evidence: [], approvals: [],
      fix_execution: null, recovery: null, report: null,
    })
    const wrapper = mount(ReportView, { global: { plugins: [ElementPlus] } })
    await flushPromises()
    expect(wrapper.text()).toContain('尚未生成')
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { describe, expect, it, vi } from 'vitest'
import ApprovalPanel from './ApprovalPanel.vue'
import * as client from '@/api/client'

vi.mock('@/api/client', () => ({ decideApproval: vi.fn() }))

const props = {
  incidentId: 5,
  approvalId: 11,
  status: 'pending' as const,
  incidentStatus: 'awaiting_approval' as const,
}

describe('ApprovalPanel', () => {
  it('pending 且 awaiting_approval 时显示批准/拒绝按钮', () => {
    const wrapper = mount(ApprovalPanel, { props, global: { plugins: [ElementPlus] } })
    expect(wrapper.text()).toContain('批准')
    expect(wrapper.text()).toContain('拒绝')
  })

  it('非 pending 审批不显示操作按钮', () => {
    const wrapper = mount(ApprovalPanel, { props: { ...props, status: 'approved' }, global: { plugins: [ElementPlus] } })
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('提交后禁用按钮防止重复审批', async () => {
    vi.mocked(client.decideApproval).mockResolvedValue({})
    const wrapper = mount(ApprovalPanel, { props, global: { plugins: [ElementPlus] } })
    await wrapper.find('button[data-testid="approve"]').trigger('click')
    await flushPromises()
    expect(client.decideApproval).toHaveBeenCalledWith(5, 11, 'approved', '')
    expect(wrapper.find('button[data-testid="approve"]').attributes('disabled')).toBeDefined()
  })
})

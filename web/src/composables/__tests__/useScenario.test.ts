import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/client', () => ({
  getScenarioStatus: vi.fn(),
  injectScenario: vi.fn(),
  resetScenario: vi.fn(),
}))

import { getScenarioStatus, injectScenario, resetScenario } from '../../api/client'
import { useScenario } from '../useScenario'

describe('useScenario', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('inject 成功后状态 INJECTED 且场景切换', async () => {
    (injectScenario as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'FAULTY' })
    const { scenario, status, inject } = useScenario()
    await inject('SCN-002')
    expect(injectScenario).toHaveBeenCalledWith('SCN-002')
    expect(scenario.value).toBe('SCN-002')
    expect(status.value).toBe('INJECTED')
  })

  it('inject 失败回 READY', async () => {
    (injectScenario as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('409'))
    const { status, inject } = useScenario()
    await expect(inject('SCN-002')).rejects.toThrow('409')
    expect(status.value).toBe('READY')
  })

  it('reset 后回 READY', async () => {
    (resetScenario as ReturnType<typeof vi.fn>).mockResolvedValue({ status: 'HEALTHY' })
    const { scenario, status, reset } = useScenario()
    scenario.value = 'SCN-002'
    await reset()
    expect(resetScenario).toHaveBeenCalledWith('SCN-002')
    expect(status.value).toBe('READY')
  })

  it('refreshStatus 从后端恢复真实状态', async () => {
    (getScenarioStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      indexPresent: true, lockHeld: true, activeScenario: 'SCN-002',
    })
    const { scenario, status, refreshStatus } = useScenario()
    await refreshStatus()
    expect(scenario.value).toBe('SCN-002')
    expect(status.value).toBe('INJECTED')
  })
})

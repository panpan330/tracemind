import { describe, expect, it } from 'vitest'
import { STATUS_META, isTerminal } from './status'

describe('status 元数据', () => {
  it('覆盖全部 9 个状态', () => {
    expect(Object.keys(STATUS_META).sort()).toEqual(
      ['awaiting_approval', 'created', 'executing', 'failed', 'investigating', 'needs_human', 'recovered', 'rejected', 'verifying'].sort(),
    )
  })
  it('终态判定', () => {
    expect(isTerminal('recovered')).toBe(true)
    expect(isTerminal('needs_human')).toBe(true)
    expect(isTerminal('rejected')).toBe(true)
    expect(isTerminal('failed')).toBe(true)
    expect(isTerminal('awaiting_approval')).toBe(false)
    expect(isTerminal('created')).toBe(false)
  })
})

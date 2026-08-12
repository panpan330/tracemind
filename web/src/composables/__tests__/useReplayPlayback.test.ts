import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'
import { useReplayPlayback } from '../useReplayPlayback'

const steps = () => ([
  { stepIndex: 0, displayDurationMs: 1000 } as any,
  { stepIndex: 1, displayDurationMs: 2000 } as any,
  { stepIndex: 2, displayDurationMs: 500 } as any,
])

describe('useReplayPlayback', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('初始 IDLE, position=0', () => {
    const { position, playbackState } = useReplayPlayback(ref(steps()))
    expect(position.value).toBe(0)
    expect(playbackState.value).toBe('IDLE')
  })

  it('播放后按 displayDurationMs 推进 position', () => {
    const { position, playbackState, play } = useReplayPlayback(ref(steps()))
    play()
    expect(playbackState.value).toBe('PLAYING')
    vi.advanceTimersByTime(1000)
    expect(position.value).toBe(1)
    vi.advanceTimersByTime(2000)
    expect(position.value).toBe(2)
    vi.advanceTimersByTime(500)
    expect(position.value).toBe(3)
    expect(playbackState.value).toBe('COMPLETED')
  })

  it('手动跳转后自动暂停', () => {
    const { position, playbackState, play, seekTo } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    seekTo(2)
    expect(position.value).toBe(2)
    expect(playbackState.value).toBe('PAUSED')
  })

  it('切换倍速不改变当前步骤且重新计时', () => {
    const { position, speed, play, setSpeed } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    setSpeed(2)
    expect(speed.value).toBe(2)
    expect(position.value).toBe(1)
    vi.advanceTimersByTime(1000)  // 2000/2 = 1000ms 进入下一步
    expect(position.value).toBe(2)
  })

  it('上一步/下一步后进入 PAUSED', () => {
    const { position, playbackState, play, next, prev } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    next()
    expect(position.value).toBe(2)
    expect(playbackState.value).toBe('PAUSED')
    prev()
    expect(position.value).toBe(1)
  })

  it('restart 回到 position=0', () => {
    const { position, play, restart, playbackState } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(1000)
    restart()
    expect(position.value).toBe(0)
    expect(playbackState.value).toBe('IDLE')
  })

  it('只存在一个计时器(seek 取消旧 Timer)', () => {
    const { play, seekTo } = useReplayPlayback(ref(steps()))
    play()
    vi.advanceTimersByTime(500)
    seekTo(1)
    vi.advanceTimersByTime(1000)
    expect(vi.getTimerCount()).toBeLessThanOrEqual(1)
  })
})

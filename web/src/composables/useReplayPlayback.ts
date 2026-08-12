import { computed, onUnmounted, ref } from 'vue'
import type { ReplayStep } from '../api/types'

export type PlaybackState = 'IDLE' | 'PLAYING' | 'PAUSED' | 'COMPLETED' | 'ERROR'
export type Speed = 1 | 2 | 4

/**
 * 回放播放引擎(V1.5):
 * - position 为"状态位置":position=0 显示 steps[0].stateBefore;position=N 显示 steps[N-1].stateAfter
 * - 单次 setTimeout(每步 displayDurationMs 不同,不用 setInterval);切速/跳转/暂停等均先取消现有 Timer
 * - 手动跳转后自动暂停;页面隐藏自动暂停;同一时间仅一个 Timer
 */
export function useReplayPlayback(steps: { value: ReplayStep[] }) {
  const position = ref(0)
  const playbackState = ref<PlaybackState>('IDLE')
  const speed = ref<Speed>(1)
  let timer: ReturnType<typeof setTimeout> | null = null

  const currentStep = computed(() =>
    position.value >= 1 ? steps.value[position.value - 1] ?? null : null)

  const displayStep = computed(() => {
    if (position.value === 0) {
      return { before: steps.value[0]?.stateBefore ?? null, after: null }
    }
    const st = steps.value[position.value - 1]
    return { before: st?.stateBefore ?? null, after: st?.stateAfter ?? null }
  })

  function clearTimer() {
    if (timer !== null) { clearTimeout(timer); timer = null }
  }

  function scheduleNext() {
    clearTimer()
    if (position.value >= steps.value.length) {
      playbackState.value = 'COMPLETED'
      return
    }
    const st = steps.value[position.value]
    const ms = Math.max(50, Math.round((st?.displayDurationMs ?? 2000) / speed.value))
    timer = setTimeout(() => {
      position.value += 1
      if (position.value >= steps.value.length) {
        playbackState.value = 'COMPLETED'
      } else {
        scheduleNext()
      }
    }, ms)
  }

  function play() {
    if (playbackState.value === 'COMPLETED') restart()
    if (position.value >= steps.value.length) return
    playbackState.value = 'PLAYING'
    scheduleNext()
  }

  function pause() {
    clearTimer()
    if (playbackState.value === 'PLAYING') playbackState.value = 'PAUSED'
  }

  function toggle() { playbackState.value === 'PLAYING' ? pause() : play() }

  function seekTo(target: number) {
    clearTimer()
    position.value = Math.max(0, Math.min(steps.value.length, target))
    playbackState.value = 'PAUSED'
  }

  function next() { if (position.value < steps.value.length) seekTo(position.value + 1) }
  function prev() { if (position.value > 0) seekTo(position.value - 1) }

  function jumpTo(stepIndex: number | undefined) {
    if (stepIndex !== undefined) seekTo(stepIndex + 1)  // keyStepIndexes 是步骤下标,状态位置 = 下标+1
  }

  function setSpeed(s: Speed) {
    speed.value = s
    if (playbackState.value === 'PLAYING') scheduleNext()  // 切速后按新速度重新计时
  }

  function restart() { clearTimer(); position.value = 0; playbackState.value = 'IDLE' }

  function onVisibilityHidden() { pause() }

  onUnmounted(clearTimer)

  return {
    position, playbackState, speed, currentStep, displayStep,
    play, pause, toggle, next, prev, seekTo, jumpTo, setSpeed, restart, onVisibilityHidden,
  }
}

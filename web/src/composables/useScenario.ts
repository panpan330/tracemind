import { ref } from 'vue'
import { getScenarioStatus, injectScenario, resetScenario } from '../api/client'

export type ScenarioId = 'SCN-001' | 'SCN-002'

export type ScenarioUiState = 'READY' | 'INJECTING' | 'INJECTED' | 'RESETTING'

/** 场景状态机(仅演示控制,设计 V1.3 §10.1):READY→INJECTING→INJECTED→RESETTING→READY */
export function useScenario() {
  const scenario = ref<ScenarioId>('SCN-001')
  const status = ref<ScenarioUiState>('READY')

  async function refreshStatus() {
    const s = await getScenarioStatus(scenario.value)
    if (s.activeScenario) scenario.value = s.activeScenario
    status.value = (s.indexPresent || s.lockHeld) ? 'INJECTED' : 'READY'
  }

  async function inject(target: ScenarioId) {
    status.value = 'INJECTING'
    try {
      await injectScenario(target)
      scenario.value = target
      status.value = 'INJECTED'
    } catch (e) {
      status.value = 'READY'
      throw e
    }
  }

  async function reset() {
    status.value = 'RESETTING'
    try {
      await resetScenario(scenario.value)
      status.value = 'READY'
    } catch (e) {
      status.value = 'READY'
      throw e
    }
  }

  return { scenario, status, inject, reset, refreshStatus }
}

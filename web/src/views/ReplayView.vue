<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchIncidentReplay, fetchRunManifest, fetchReplaySteps, fetchReplayStepDetail } from '../api/client'
import type { IncidentReplayManifest, ReplayRunManifest, ReplayStep, ReplayStepsResponse } from '../api/types'
import { useReplayPlayback } from '../composables/useReplayPlayback'

const props = defineProps<{ incidentId?: number; runId?: number; position?: number }>()

const manifest = ref<IncidentReplayManifest | null>(null)
const runManifest = ref<ReplayRunManifest | null>(null)
const steps = ref<ReplayStep[]>([])
const activeRunId = ref(props.runId ?? 0)
const techDetail = ref<Record<string, unknown> | null>(null)
const showTech = ref(false)
const error = ref<string | null>(null)

const route = useRoute()
const router = useRouter()

const { position, playbackState, speed, displayStep, currentStep,
        toggle, next, prev, seekTo, jumpTo, setSpeed, restart } =
  useReplayPlayback(steps as any)

async function load() {
  error.value = null
  try {
    manifest.value = await fetchIncidentReplay(props.incidentId ?? Number(route.query.incidentId))
    activeRunId.value = props.runId ?? manifest.value.defaultRunId ?? 0
    if (activeRunId.value) {
      runManifest.value = await fetchRunManifest(manifest.value.incidentId, activeRunId.value)
      const resp: ReplayStepsResponse = await fetchReplaySteps(manifest.value.incidentId, activeRunId.value)
      steps.value = resp.steps
      const initial = props.position ?? Number(route.query.position ?? 0)
      seekTo(initial)
      syncUrl()
    }
  } catch (e: any) {
    error.value = e.message ?? String(e)
  }
}

function syncUrl() {
  if (!activeRunId.value) return
  router.replace({ path: '/replay', query: { incidentId: manifest.value?.incidentId, runId: activeRunId.value, position: String(position.value) } })
}

function goObservation() {
  if (!activeRunId.value || !manifest.value) return
  router.push(`/incidents/${manifest.value.incidentId}/runs/${activeRunId.value}/observation`)
}

async function loadStepDetail(step: ReplayStep) {
  showTech.value = true
  if (!manifest.value) return
  techDetail.value = await fetchReplayStepDetail(manifest.value.incidentId, activeRunId.value, step.logicalStepId)
}

onMounted(load)
</script>

<template>
  <div class="replay-view" data-testid="replay-view">
    <div class="replay-banner">历史回放 · 只读 · 不会执行任何系统操作</div>

    <div v-if="error" class="error">{{ error }}</div>

    <template v-else>
      <div class="replay-status" v-if="runManifest">
        <span>回放状态: {{ runManifest.replayStatus }}</span>
        <span v-if="runManifest.runOutcome">· 调查结果: {{ runManifest.runOutcome }}</span>
        <span v-if="runManifest.replayStatus === 'partial'" class="missing-flag">部分审计记录缺失</span>
        <span v-if="runManifest.replayStatus === 'in_progress'" class="stale">调查尚未结束</span>
        <el-button v-if="activeRunId" size="small" @click="goObservation">运行观测</el-button>
      </div>

      <!-- 顶部时间轴 -->
      <div class="timeline" v-if="steps.length">
        <button v-for="st in steps" :key="st.logicalStepId"
                :class="['tl-node', { active: currentStep?.logicalStepId === st.logicalStepId,
                                      incomplete: st.stepState === 'incomplete' }]"
                :data-testid="`tl-${st.logicalStepId}`"
                @click="jumpTo(st.stepIndex)">
          {{ st.stepTitle || st.stepType }}
          <span v-if="st.stepState === 'incomplete'" class="missing-flag">缺失</span>
        </button>
      </div>
      <div v-else class="empty">无回放数据</div>

      <div class="replay-body" v-if="steps.length">
        <!-- 左侧:状态快照 -->
        <aside class="state-panel">
          <section>
            <h4>假设</h4>
            <ul>
              <li v-for="h in (displayStep.after?.hypotheses ?? displayStep.before?.hypotheses ?? [])"
                  :key="String((h as any).id)">
                <span :class="['state-chip', String((h as any).status ?? 'unknown')]">
                  {{ (h as any).status ?? 'unknown' }}</span>{{ (h as any).description }}
              </li>
            </ul>
          </section>
          <section>
            <h4>共享 Facts</h4>
            <ul>
              <li v-for="(v, k) in (displayStep.after?.facts ?? displayStep.before?.facts ?? {})" :key="k">
                <span :class="['state-chip', String(v)]">{{ v }}</span>{{ k }}
              </li>
            </ul>
          </section>
          <section>
            <h4>排他条件</h4>
            <ul>
              <li v-for="(v, k) in (displayStep.after?.exclusion_conditions
                                    ?? displayStep.before?.exclusion_conditions ?? {})" :key="k">
                <span :class="['state-chip', String(v)]">{{ v }}</span>{{ k }}
              </li>
            </ul>
          </section>
        </aside>

        <!-- 右侧:本步详情 -->
        <main class="detail-panel">
          <div class="step-summary">
            <h3>{{ currentStep?.stepTitle || '初始状态' }}</h3>
            <p v-if="currentStep?.missingParts?.length" class="missing-flag">
              工具调用结果缺失({{ currentStep.missingParts.join(', ') }})</p>
            <pre class="decision">{{ JSON.stringify(currentStep?.decisionSummary ?? {}, null, 2) }}</pre>
          </div>

          <div class="controls">
            <button data-testid="replay-play" @click="toggle">
              {{ playbackState === 'PLAYING' ? '暂停' : '播放' }}</button>
            <button @click="prev">上一步</button>
            <button @click="next">下一步</button>
            <button v-for="s in [1, 2, 4]" :key="s" :class="{ active: speed === s }" @click="setSpeed(s as any)">
              {{ s }}×</button>
            <button @click="restart">重新播放</button>
            <button v-if="currentStep" @click="loadStepDetail(currentStep)">技术详情</button>
          </div>

          <details v-if="showTech && techDetail" class="tech-detail" open>
            <summary>技术详情</summary>
            <pre>{{ JSON.stringify(techDetail, null, 2) }}</pre>
          </details>
        </main>
      </div>
    </template>
  </div>
</template>

<style scoped>
.replay-view { padding: 16px; font-size: 14px; }
.replay-banner { background: #eef; border-radius: 6px; padding: 8px 12px; margin-bottom: 10px; font-weight: 600; }
.replay-status { display: flex; gap: 12px; margin-bottom: 10px; }
.error { color: #c00; }
.timeline { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.tl-node { border: 1px solid #bbb; border-radius: 14px; padding: 4px 10px; font-size: 12px; cursor: pointer; background: #fff; }
.tl-node.active { border-color: #0a5c8a; background: #d1f0ff; font-weight: 600; }
.tl-node.incomplete { border-color: #c00; border-style: dashed; }
.replay-body { display: grid; grid-template-columns: 320px 1fr; gap: 14px; }
.state-panel section { margin-bottom: 12px; }
.state-panel h4 { margin: 0 0 6px; font-size: 13px; color: #555; }
.state-panel ul { list-style: none; margin: 0; padding: 0; }
.state-panel li { margin-bottom: 4px; }
.state-chip { padding: 1px 6px; border-radius: 8px; font-size: 12px; margin-right: 6px; display: inline-block; min-width: 52px; text-align: center; }
.state-chip.true, .state-chip.supported, .state-chip.confirmed { background: #d1f0ff; color: #0a5c8a; }
.state-chip.false, .state-chip.refuted { background: #ffe3e3; color: #a13; text-decoration: line-through; }
.state-chip.unknown, .state-chip.proposed { background: #eee; color: #666; }
.state-chip.stale { background: #fff2d9; color: #9a6a00; }
.state-chip.conflict { background: #ffd9d9; color: #c00; }
.missing-flag { color: #c00; font-weight: 600; }
.stale { color: #9a6a00; font-weight: 600; }
.empty { color: #888; padding: 24px; text-align: center; }
.decision { background: #f6f6f6; border-radius: 6px; padding: 8px; font-size: 12px; overflow: auto; max-height: 220px; }
.controls { display: flex; gap: 8px; margin: 12px 0; flex-wrap: wrap; }
.controls button { border: 1px solid #bbb; border-radius: 6px; padding: 4px 10px; cursor: pointer; background: #fff; }
.controls button.active { background: #0a5c8a; color: #fff; }
.tech-detail pre { background: #f6f6f6; padding: 8px; font-size: 12px; overflow: auto; max-height: 320px; }
</style>

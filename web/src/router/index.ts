import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'scenario', component: () => import('@/views/ScenarioView.vue') },
    { path: '/incidents/:id', name: 'incident-detail', component: () => import('@/views/IncidentDetailView.vue') },
    { path: '/incidents/:id/report', name: 'incident-report', component: () => import('@/views/ReportView.vue') },
    { path: '/incidents/:id/runs/:runId/observation', name: 'run-observation', component: () => import('@/views/RunObservationView.vue') },
    { path: '/replay', name: 'replay', component: () => import('@/views/ReplayView.vue'),
      props: (route: { query: { incidentId?: string; runId?: string; position?: string } }) => ({
        incidentId: route.query.incidentId ? Number(route.query.incidentId) : undefined,
        runId: route.query.runId ? Number(route.query.runId) : undefined,
        position: route.query.position ? Number(route.query.position) : 0,
      }) },
    { path: '/evals', name: 'evals', component: () => import('@/views/EvalDashboardView.vue') },
    { path: '/evals/:id', name: 'eval-detail', component: () => import('@/views/EvalDetailView.vue') },
  ],
})

export default router

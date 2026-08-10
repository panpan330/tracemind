import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'scenario', component: () => import('@/views/ScenarioView.vue') },
    { path: '/incidents/:id', name: 'incident-detail', component: () => import('@/views/IncidentDetailView.vue') },
    { path: '/incidents/:id/report', name: 'incident-report', component: () => import('@/views/ReportView.vue') },
  ],
})

export default router

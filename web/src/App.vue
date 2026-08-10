<template>
  <el-container class="app-shell">
    <el-header class="app-header">
      <router-link to="/" class="brand">TraceMind 工作台</router-link>
      <span class="subtitle">AI 故障诊断与安全处置平台</span>
    </el-header>
    <el-main><router-view /></el-main>
  </el-container>
</template>

<style scoped>
.app-shell { min-height: 100vh; }
.app-header { display: flex; align-items: baseline; gap: 12px; border-bottom: 1px solid var(--el-border-color); }
.brand { font-size: 18px; font-weight: 600; text-decoration: none; color: var(--el-color-primary); }
.subtitle { color: var(--el-text-color-secondary); font-size: 13px; }
</style>

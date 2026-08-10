import { defineConfig } from '@playwright/test'

// E2E 冒烟:Docker Compose 部署的前端工作台(baseURL 默认 VM,可 env 覆盖)
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: process.env.WEB_BASE_URL || 'http://192.168.88.10:8080',
    headless: true,
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'retain-on-failure',
  },
})

import { expect, test } from '@playwright/test'

// 完整演示闭环冒烟:重置 → 健康负载 → 注入 → 创建 → 调查(+故障负载) → 审批 → 恢复 → 报告
// 需要后端全栈已运行(docker compose up -d,全部 healthy);负载经 order-service HTTP 直打

const ORDER_URL = process.env.ORDER_URL || 'http://192.168.88.10:8081'

async function runLoad(seconds = 8, qps = 15) {
  const deadline = Date.now() + seconds * 1000
  while (Date.now() < deadline) {
    const sku = Math.floor(Math.random() * 20000)
    const wh = Math.floor(Math.random() * 50)
    fetch(`${ORDER_URL}/api/orders/1/check-stock`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skuId: sku, warehouseId: wh, quantity: 1 }),
    }).catch(() => {})
    await new Promise((r) => setTimeout(r, 1000 / qps))
  }
}

test('演示闭环:注入故障到复盘报告', async ({ page }) => {
  test.setTimeout(300_000)

  // 1) 重置环境(ScenarioView 挂在根路径 /)
  await page.goto('/')
  await page.getByTestId('reset-scenario').click()
  await expect(page.getByTestId('scenario-tag')).toContainText(/健康|故障/, { timeout: 20_000 })

  // 2) 健康负载:创建 incident 前让观测窗口有健康数据(基线采集有值)
  await runLoad(8, 15)

  // 3) 注入故障
  await page.getByTestId('inject-fault').click()

  // 4) 创建 Incident
  await page.getByTestId('incident-title').fill('E2E 冒烟:库存查询变慢')
  await page.getByTestId('create-incident').click()
  await page.waitForURL(/\/incidents\/\d+/, { timeout: 20_000 })

  // 5) 开始调查
  await page.getByTestId('start-investigation').click()

  // 6) 故障负载:Agent 证据收集期间产生故障态观测数据与 digest 增量
  await runLoad(8, 15)

  // 7) 等待到达待审批(Agent 多轮重试 + SSE/轮询,最长 120s);StatusTag 显示中文
  await expect(page.getByText('待审批', { exact: true }).first()).toBeVisible({ timeout: 120_000 })

  // 8) 批准
  await page.getByTestId('approve').click()

  // 9) 等待恢复(StatusTag 中文)
  await expect(page.getByText('已恢复', { exact: true }).first()).toBeVisible({ timeout: 90_000 })

  // 10) 复盘报告
  await page.getByRole('button', { name: '查看复盘报告' }).click()
  await expect(page.getByText('复盘报告', { exact: false }).first()).toBeVisible({ timeout: 20_000 })
})

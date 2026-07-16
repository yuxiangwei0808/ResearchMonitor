import { defineConfig, devices } from '@playwright/test'
import { randomUUID } from 'node:crypto'

const port = 8876
const runId = randomUUID()
const testHome = `/tmp/research-monitor-playwright-${runId}`
const fixtureRoot = `/tmp/research-monitor-playwright-fixtures-${runId}`

export default defineConfig({
  testDir: './e2e',
  // The lifecycle and proposal journeys deliberately exercise long workflows
  // in serial tests. Individual UI assertions retain Playwright's shorter
  // default expectation budget; this only prevents whole journeys from being
  // truncated on slower release runners.
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: 'line',
  metadata: { testHome, fixtureRoot },
  outputDir: `/tmp/research-monitor-playwright-output-${runId}`,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    // Build once, then exercise the compiled Vite application served by the
    // real FastAPI process.  No Vite proxy or mocked transport is involved.
    command: `npm run build && uv --project .. run research-monitor serve --port ${port}`,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      ...process.env,
      RESEARCH_MONITOR_HOME: testHome,
      RESEARCH_MONITOR_ALLOWED_ROOTS: '/tmp',
    },
  },
})

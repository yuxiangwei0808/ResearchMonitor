import { expect, test as base, type BrowserContext } from '@playwright/test'

export type E2EMetadata = {
  testHome: string
  fixtureRoot: string
}

function metadata(value: unknown): E2EMetadata {
  const raw = value as Partial<E2EMetadata>
  if (!raw.testHome || !raw.fixtureRoot) throw new Error('Playwright E2E metadata is missing')
  return raw as E2EMetadata
}

type WorkerFixtures = { authenticatedContext: BrowserContext }

export const test = base.extend<Record<string, never>, WorkerFixtures>({
  authenticatedContext: [async ({ browser }, use) => {
    const context = await browser.newContext()
    const bootstrapPage = await context.newPage()
    // Mirror VS Code opening its automatically forwarded bare dashboard URL.
    // Health probes do not carry the complete direct-navigation header tuple.
    const response = await bootstrapPage.goto('/')
    expect(response?.status()).toBe(200)
    await expect(bootstrapPage).toHaveURL(/\/$/)
    await expect(bootstrapPage.getByRole('heading', { name: 'Portfolio' })).toBeVisible()
    await bootstrapPage.close()
    await use(context)
    await context.close()
  }, { scope: 'worker' }],
  page: async ({ authenticatedContext }, use) => {
    const page = await authenticatedContext.newPage()
    await use(page)
    await page.close()
  },
})

export { expect, metadata }

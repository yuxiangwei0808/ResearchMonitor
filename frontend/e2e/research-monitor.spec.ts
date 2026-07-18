import { randomUUID } from 'node:crypto'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { request as playwrightRequest, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { expect, metadata, test, type E2EMetadata } from './fixtures'

type ProjectSnapshot = {
  project: { id: string; semantic_revision: number }
  pipelines: Array<{ id: string; title: string; version: number }>
  tasks: Array<{ id: string; title: string; status: string; readiness: string }>
}

type Proposal = { id: string; operations: Array<{ id: string }> }

const projectIdFromUrl = (page: Page) => new URL(page.url()).pathname.split('/')[2]

async function openPortfolio(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Portfolio' })).toBeVisible()
}

async function expectNoA11yViolations(page: Page, surface: string) {
  const result = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22a', 'wcag22aa'])
    .analyze()
  expect(result.violations, `${surface} WCAG A/AA violations:\n${JSON.stringify(result.violations, null, 2)}`).toEqual([])
}

async function enroll(page: Page, name: string, root: string) {
  await openPortfolio(page)
  await page.locator('.portfolio-heading').getByRole('button', { name: 'Add project', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Add a research project' })
  await dialog.getByLabel('Project name').fill(name)
  await dialog.getByLabel('Project folder').fill(root)
  await dialog.getByLabel('Research goal').fill('Keep the research plan, progress, and evidence synchronized.')
  await dialog.getByRole('button', { name: 'Add project', exact: true }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('heading', { name })).toBeVisible()
  return projectIdFromUrl(page)
}

async function openView(page: Page, name: 'Outline' | 'Graph' | 'Artifacts' | 'Proposals' | 'Settings') {
  // ProposalCount is loaded asynchronously and becomes part of the link's
  // accessible name (for example, "Proposals 1"). Scope to the view tabs and
  // accept that optional badge so route tests do not race the count query.
  await page.locator('.view-tabs').getByRole('link', { name: new RegExp(`^${name}(?:\\s*\\d+)?$`) }).click()
  const headings = { Outline: 'Research outline', Graph: 'Task graph', Artifacts: 'Artifacts', Proposals: 'Codex proposals', Settings: 'Project settings' }
  await expect(page.getByRole('heading', { name: headings[name] })).toBeVisible()
}

async function createPipeline(page: Page, title: string, flow: 'Sequential' | 'Freeform' = 'Freeform') {
  await page.getByRole('button', { name: 'New pipeline' }).click()
  const dialog = page.getByRole('dialog', { name: 'New pipeline' })
  await dialog.getByLabel('Title').fill(title)
  await dialog.getByLabel('Description').fill('A browser-created research workstream.')
  await dialog.getByLabel('Task flow').selectOption(flow.toLowerCase())
  await dialog.getByRole('button', { name: 'Save pipeline' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
}

async function createTask(page: Page, title: string, key: string, parentTitle?: string) {
  if (parentTitle) {
    await taskRow(page, parentTitle).getByRole('button', { name: 'Add subtask' }).click()
  } else {
    await page.getByRole('button', { name: 'New task' }).click()
  }
  const dialog = page.getByRole('dialog', { name: parentTitle ? 'New subtask' : 'New task' })
  await dialog.getByLabel('Key').fill(key)
  await dialog.getByLabel('Task title').fill(title)
  await dialog.getByLabel('Description').fill(`Browser journey task: ${title}`)
  await dialog.getByRole('button', { name: 'Create task' }).click()
  await expect(dialog).toBeHidden()
  await expect(taskRow(page, title)).toBeVisible()
}

async function issueGuidedPrompt(page: Page, modeName: string) {
  await page.getByRole('button', { name: 'Ask Codex', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Ask Codex' })
  await dialog.getByRole('radio', { name: new RegExp(modeName, 'i') }).check()
  const scopeType = dialog.getByLabel('Scope type')
  if (await scopeType.inputValue() !== 'project') await dialog.locator('select[required]').selectOption({ index: 1 })
  if (modeName === 'Record an update') await dialog.getByLabel('Update to record').fill('Recorded from the compiled-browser guided workflow check.')
  if (modeName === 'Link artifacts') {
    await dialog.getByRole('button', { name: 'Add locator' }).click()
    await dialog.getByRole('textbox', { name: 'Artifact locator', exact: true }).fill('results/browser-check.json')
  }
  await dialog.getByRole('button', { name: 'Generate prompt' }).click()
  await expect(dialog.getByText('Bound request', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Close', exact: true }).click()
  await expect(dialog).toBeHidden()
}

function taskRow(page: Page, title: string) {
  return page.locator('.task-row').filter({ has: page.getByRole('strong').filter({ hasText: title }) })
}

async function editTask(page: Page, title: string, values: {
  status?: string
  blocker?: string
  completion?: string
  journal?: string
  journalType?: string
}) {
  await taskRow(page, title).locator('.task-title-cell').click()
  const dialog = page.getByRole('dialog', { name: 'Task details' })
  if (values.status) await dialog.getByLabel('Status').selectOption(values.status)
  if (values.blocker) await dialog.getByLabel('Blocker explanation').fill(values.blocker)
  if (values.completion) await dialog.getByLabel('Completion summary').fill(values.completion)
  if (values.journal) {
    await dialog.locator('.editor-journal select').selectOption(values.journalType ?? 'progress')
    await dialog.locator('.editor-journal textarea').fill(values.journal)
  }
  await dialog.getByRole('button', { name: 'Save changes' }).click()
  await expect(dialog).toBeHidden()
}

async function agentClient(testInfo: TestInfo): Promise<APIRequestContext> {
  const { testHome } = metadata(testInfo.config.metadata)
  const token = (await fs.readFile(path.join(testHome, 'cli-token'), 'utf8')).trim()
  return playwrightRequest.newContext({
    baseURL: String(testInfo.project.use.baseURL),
    extraHTTPHeaders: {
      Authorization: `Bearer ${token}`,
      'User-Agent': 'research-monitor-cli-playwright',
    },
  })
}

async function snapshot(client: APIRequestContext, projectId: string): Promise<ProjectSnapshot> {
  const response = await client.get(`/api/v1/projects/${projectId}/snapshot`)
  expect(response.ok(), await response.text()).toBeTruthy()
  return response.json() as Promise<ProjectSnapshot>
}

function agentOperation(type: string, data: Record<string, unknown>, entityId?: string, expectedVersion?: number) {
  return {
    id: randomUUID(),
    type,
    data,
    ...(entityId ? { entity_id: entityId } : {}),
    ...(expectedVersion ? { expected_version: expectedVersion } : {}),
    rationale: 'PLAN.md explicitly records this monitor update.',
    confidence: 0.94,
    evidence: [{ kind: 'document', locator: 'PLAN.md', description: 'Reviewed project plan' }],
    source_references: [{ path: 'PLAN.md', anchor: 'Browser reconciliation' }],
  }
}

async function createProposal(
  client: APIRequestContext,
  projectId: string,
  baseRevision: number,
  summary: string,
  operations: ReturnType<typeof agentOperation>[],
): Promise<Proposal> {
  const response = await client.post(`/api/v1/projects/${projectId}/proposals`, {
    data: {
      api_version: '1',
      schema_version: '1',
      request_id: randomUUID(),
      project_id: projectId,
      base_semantic_revision: baseRevision,
      summary,
      rationale: 'A deterministic Playwright agent reconciliation.',
      actor_label: 'Codex browser journey',
      operations,
    },
  })
  expect(response.status(), await response.text()).toBe(201)
  return response.json() as Promise<Proposal>
}

test.describe.serial('Research Monitor production browser journeys', () => {
  test('authenticates repeatable direct VS Code launches', async ({ browser }, testInfo) => {
    const baseURL = String(testInfo.project.use.baseURL)
    const first = await browser.newContext({ baseURL })
    const second = await browser.newContext({ baseURL })
    const deepLink = await browser.newContext({ baseURL })
    try {
      const firstPage = await first.newPage()
      await firstPage.goto('/')
      await expect(firstPage.getByRole('heading', { name: 'Portfolio' })).toBeVisible()
      const firstCookies = await first.cookies(baseURL)
      const firstSession = firstCookies.find((cookie) => cookie.name === 'research_monitor_session')
      const firstCsrf = firstCookies.find((cookie) => cookie.name === 'research_monitor_csrf')
      expect(firstSession).toMatchObject({ httpOnly: true, sameSite: 'Strict' })
      expect(firstCsrf).toMatchObject({ httpOnly: false, sameSite: 'Strict' })

      const secondPage = await second.newPage()
      await secondPage.goto('/')
      await expect(secondPage.getByRole('heading', { name: 'Portfolio' })).toBeVisible()
      await firstPage.reload()
      await expect(firstPage.getByRole('heading', { name: 'Portfolio' })).toBeVisible()

      await first.clearCookies()
      await firstPage.goto('/')
      await expect(firstPage.getByRole('heading', { name: 'Portfolio' })).toBeVisible()
      const refreshedSession = (await first.cookies(baseURL))
        .find((cookie) => cookie.name === 'research_monitor_session')
      expect(refreshedSession?.value).not.toBe(firstSession?.value)

      const { fixtureRoot } = metadata(testInfo.config.metadata)
      const deepRoot = path.join(fixtureRoot, 'direct-deep-link-project')
      await fs.mkdir(deepRoot, { recursive: true })
      const projectId = await enroll(firstPage, 'Direct deep-link study', deepRoot)

      const deepPage = await deepLink.newPage()
      const deepPath = `/projects/${projectId}/graph?launch=direct&status=ready`
      await deepPage.goto(deepPath)
      await expect(deepPage).toHaveURL(new RegExp(`${deepPath.replace('?', '\\?')}$`))
      await expect(deepPage.getByRole('heading', { name: 'No active tasks to graph' })).toBeVisible()
      await expect(deepPage.getByText('Authenticate through the browser bootstrap URL')).toHaveCount(0)
      const deepCookies = await deepLink.cookies(baseURL)
      expect(deepCookies.find((cookie) => cookie.name === 'research_monitor_session'))
        .toMatchObject({ httpOnly: true, sameSite: 'Strict' })
    } finally {
      await Promise.all([first.close(), second.close(), deepLink.close()])
    }
  })

  test('validates enrollment in the compiled dashboard', async ({ page }) => {
    await openPortfolio(page)
    await expect(page).toHaveTitle('Research Monitor')
    await expectNoA11yViolations(page, 'Portfolio')
    await page.locator('.portfolio-heading').getByRole('button', { name: 'Add project', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: 'Add a research project' })
    await dialog.getByLabel('Project name').fill('Browser validation project')
    await dialog.getByLabel('Project folder').fill('relative/project')
    await dialog.getByRole('button', { name: 'Add project', exact: true }).click()
    await expect(dialog.getByText('Use an absolute Linux path.')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(dialog).toBeHidden()
  })

  test('issues a server-bound guided prompt without scanning or changing project semantics', async ({ page }, testInfo) => {
    const { fixtureRoot } = metadata(testInfo.config.metadata)
    const root = path.join(fixtureRoot, 'guided-intent-project')
    await fs.mkdir(root, { recursive: true })
    const projectId = await enroll(page, 'Guided intent study', root)
    const client = await agentClient(testInfo)
    try {
      const before = await snapshot(client, projectId)
      await page.getByRole('button', { name: 'Ask Codex' }).click()
      const dialog = page.getByRole('dialog', { name: 'Ask Codex' })
      await expect(dialog.getByRole('radio', { name: /Initialize structure/i })).toBeChecked()
      await expect(dialog.getByText(/Companion skill:/i)).toBeVisible()
      await dialog.getByRole('button', { name: 'Generate prompt' }).click()
      await expect(dialog.getByText('Bound request', { exact: true })).toBeVisible()
      await expect(dialog.getByLabel('Complete generated Codex prompt')).toHaveValue(new RegExp(`agent context --project ${projectId} --intent [0-9a-f-]+ --json`))
      await expect(dialog.getByText(/Copying sends nothing/i)).toBeVisible()
      await expectNoA11yViolations(page, 'Ask Codex')
      const after = await snapshot(client, projectId)
      expect(after.project.semantic_revision).toBe(before.project.semantic_revision)

      await dialog.getByRole('button', { name: 'Close', exact: true }).click()
      await openView(page, 'Outline')
      await createPipeline(page, 'Guided browser workflow')
      await createTask(page, 'Guided browser task', 'GUIDED-01')
      for (const modeName of ['Expand a task', 'Reconcile progress', 'Suggest next work', 'Record an update', 'Link artifacts']) {
        await issueGuidedPrompt(page, modeName)
      }
    } finally {
      await client.dispose()
    }
  })

  test('records a complete manual research lifecycle and preserves it across recovery', async ({ page }, testInfo) => {
    const { fixtureRoot } = metadata(testInfo.config.metadata) as E2EMetadata
    const root = path.join(fixtureRoot, 'manual-project')
    const resultPath = path.join(root, 'results', 'summary.md')
    await fs.mkdir(path.dirname(resultPath), { recursive: true })
    await fs.writeFile(resultPath, '# Browser result\n\nAccuracy improved to 91%.\n', 'utf8')

    const projectId = await enroll(page, 'Manual lifecycle study', root)
    await openView(page, 'Outline')
    await createPipeline(page, 'Research workflow')
    await createTask(page, 'Collect data', 'DATA-01')
    await createTask(page, 'Download dataset', 'DATA-02', 'Collect data')
    await createTask(page, 'Verify checksum', 'DATA-03', 'Download dataset')
    await createTask(page, 'Analyze results', 'ANALYSIS-01')
    await expectNoA11yViolations(page, 'Outline')

    await taskRow(page, 'Analyze results').getByRole('button', { name: 'Actions for Analyze results' }).click()
    let taskMenu = page.getByRole('menu', { name: 'Actions for Analyze results' })
    await expect(taskMenu.getByRole('menuitem', { name: 'Add subtask' })).toBeVisible()
    await taskMenu.getByRole('menuitem', { name: 'Edit task' }).click()
    let taskDialog = page.getByRole('dialog', { name: 'Task details' })
    await expect(taskDialog.getByLabel('Task timestamps')).toContainText('Created')
    await expect(taskDialog.getByLabel('Task timestamps')).toContainText('Last updated')
    await taskDialog.getByLabel('Target date').fill('2026-08-15')
    await taskDialog.getByRole('button', { name: 'Save changes' }).click()
    await expect(taskDialog).toBeHidden()
    await expect(taskRow(page, 'Analyze results').locator('time[datetime="2026-08-15"]')).toBeVisible()

    await taskRow(page, 'Collect data').click({ button: 'right' })
    taskMenu = page.getByRole('menu', { name: 'Actions for Collect data' })
    await expect(taskMenu.getByRole('menuitem', { name: 'Edit task' })).toBeVisible()
    await expect(taskMenu.getByRole('menuitem', { name: 'Add subtask' })).toBeVisible()
    await expect(taskMenu.getByRole('menuitem', { name: 'Delete task' })).toBeVisible()
    await page.keyboard.press('Escape')

    await openView(page, 'Graph')
    const graphPipeline = page.getByLabel('Graph pipeline')
    await expect(graphPipeline.locator('option:checked')).toHaveText('Research workflow')
    const collectNode = page.getByRole('button', { name: 'Select Collect data' })
    await expect(page.getByRole('button', { name: 'View 1 subtask for Collect data' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Select Analyze results' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Select Download dataset' })).toHaveCount(0)
    await expectNoA11yViolations(page, 'Graph')

    await collectNode.hover()
    const subtaskPreview = page.getByRole('dialog', { name: 'Subtasks for Collect data' })
    await expect(subtaskPreview).toBeVisible()
    await expect(subtaskPreview.getByText('Freeform', { exact: true })).toBeVisible()
    await expect(subtaskPreview.getByText('Download dataset', { exact: true })).toBeVisible()
    await expect(subtaskPreview.getByText('1 subtask', { exact: true })).toBeVisible()
    await expect(subtaskPreview.getByText(/no automatic order/i)).toBeVisible()
    await page.locator('.graph-toolbar').hover()
    await expect(subtaskPreview).toBeHidden()

    await collectNode.click()
    await expect(collectNode).toHaveAttribute('aria-pressed', 'true')
    await expect(page.getByRole('button', { name: 'Select Download dataset' })).toHaveCount(0)
    await expect(page.getByRole('dialog', { name: 'Task details' })).toHaveCount(0)
    await collectNode.dblclick()
    const downloadNode = page.getByRole('button', { name: 'Select Download dataset' })
    await expect(downloadNode).toBeVisible()
    await expect(page.getByRole('button', { name: 'Select Analyze results' })).toHaveCount(0)

    await downloadNode.press('Enter')
    const checksumNode = page.getByRole('button', { name: 'Select Verify checksum' })
    await expect(checksumNode).toBeVisible()
    await expect(page.locator('.graph-breadcrumbs').getByRole('button', { name: 'Download dataset' })).toBeVisible()
    await checksumNode.click()
    await expect(checksumNode).toHaveAttribute('aria-pressed', 'true')
    await checksumNode.dblclick()
    await expect(page.getByRole('dialog', { name: 'Task details' })).toHaveCount(0)

    await page.locator('.graph-breadcrumbs').getByRole('button', { name: 'Research workflow' }).click()
    await page.getByRole('button', { name: 'View 1 subtask for Collect data' }).click()
    await expect(page.getByRole('button', { name: 'Select Download dataset' })).toBeVisible()
    await page.locator('.graph-breadcrumbs').getByRole('button', { name: 'Research workflow' }).click()

    await page.getByRole('button', { name: 'Select Collect data' }).click({ button: 'right' })
    taskMenu = page.getByRole('menu', { name: 'Actions for Collect data' })
    await expect(taskMenu.getByRole('menuitem', { name: 'Edit task' })).toBeVisible()
    await expect(taskMenu.getByRole('menuitem', { name: 'View 1 subtask' })).toBeVisible()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Actions for Collect data' }).click()
    taskMenu = page.getByRole('menu', { name: 'Actions for Collect data' })
    await expect(taskMenu.getByRole('menuitem', { name: 'Add subtask' })).toBeVisible()
    await page.keyboard.press('Escape')

    const relationships = page.locator('details.relationship-panel')
    await relationships.locator('summary').click()
    const collectDataLabel = 'Research workflow › DATA-01 · Collect data'
    const analyzeResultsLabel = 'Research workflow › ANALYSIS-01 · Analyze results'
    await relationships.getByLabel('Source / prerequisite task').selectOption({ label: collectDataLabel })
    await relationships.getByLabel('Target / dependent task').selectOption({ label: analyzeResultsLabel })
    await relationships.getByRole('button', { name: 'Add relationship' }).click()
    await expect(relationships.getByText(`${collectDataLabel} → ${analyzeResultsLabel}`)).toBeVisible()

    await openView(page, 'Outline')
    await expect(taskRow(page, 'Analyze results').getByText('Waiting', { exact: true })).toBeVisible()
    await editTask(page, 'Download dataset', {
      status: 'blocked',
      blocker: 'Dataset approval is pending.',
      journal: 'Requested access from the data owner.',
      journalType: 'blocker',
    })
    await expect(taskRow(page, 'Download dataset').getByRole('combobox', { name: 'Status for Download dataset' })).toHaveValue('blocked')

    await editTask(page, 'Verify checksum', {
      status: 'done',
      completion: 'Verified the dataset checksum and recorded the result.',
      journal: 'Checksum verification completed successfully.',
      journalType: 'completion',
    })
    await editTask(page, 'Download dataset', {
      status: 'done',
      completion: 'Downloaded and checksum-verified the approved dataset.',
      journal: 'Access arrived and the checksum matched.',
      journalType: 'completion',
    })
    await editTask(page, 'Collect data', {
      status: 'done',
      completion: 'The complete input dataset is ready for analysis.',
      journal: 'Closed data collection after validating the child task.',
      journalType: 'completion',
    })
    await expect(taskRow(page, 'Analyze results').getByText('Ready', { exact: true })).toBeVisible()

    await openView(page, 'Artifacts')
    await page.getByRole('button', { name: 'Link artifact' }).click()
    const artifactDialog = page.getByRole('dialog', { name: 'Link an artifact' })
    await artifactDialog.getByLabel('Label').fill('Analysis summary')
    await artifactDialog.getByLabel('Relative path').fill('results/summary.md')
    await artifactDialog.getByLabel('Add task association').selectOption({ label: analyzeResultsLabel })
    await artifactDialog.getByLabel('Association role').selectOption('result')
    await artifactDialog.getByRole('button', { name: 'Link artifact' }).click()
    await expect(artifactDialog).toBeHidden()
    const artifactRow = page.getByRole('row').filter({ hasText: 'Analysis summary' })
    await artifactRow.getByRole('button', { name: 'Refresh Analysis summary metadata' }).click()
    await expect(artifactRow.getByText('Available', { exact: true })).toBeVisible()
    await artifactRow.getByRole('button', { name: 'Preview' }).click()
    const preview = page.getByRole('dialog', { name: 'Analysis summary' })
    await expect(preview.locator('iframe')).toBeVisible()
    await expect(preview.locator('iframe').contentFrame().getByText('Accuracy improved to 91%.')).toBeVisible()
    await preview.getByRole('button', { name: 'Close', exact: true }).click()

    await openView(page, 'Outline')
    await taskRow(page, 'Analyze results').getByRole('button', { name: 'Actions for Analyze results' }).click()
    taskMenu = page.getByRole('menu', { name: 'Actions for Analyze results' })
    page.once('dialog', (dialog) => dialog.accept())
    await taskMenu.getByRole('menuitem', { name: 'Delete task' }).click()
    const deleted = page.locator('.settings-section').filter({ has: page.getByRole('heading', { name: 'Deleted items' }) })
    await expect(deleted.getByText('Analyze results')).toBeVisible()
    await deleted.getByRole('button', { name: 'Restore subtree' }).click()
    await expect(taskRow(page, 'Analyze results')).toBeVisible()

    await openView(page, 'Settings')
    await expectNoA11yViolations(page, 'Settings')
    const lifecycle = page.locator('.settings-section').filter({ has: page.getByRole('heading', { name: 'Project lifecycle' }) })
    await lifecycle.getByRole('button', { name: 'Archive' }).click()
    await page.goto('/?show=archived')
    await expect(page.getByRole('heading', { name: 'Archived projects' })).toBeVisible()
    await page.getByRole('link', { name: /Manual lifecycle study/ }).click()
    await openView(page, 'Settings')
    await page.getByRole('button', { name: 'Restore' }).click()
    await expect(page.getByRole('heading', { name: 'Manual lifecycle study' })).toBeVisible()

    await openView(page, 'Settings')
    page.once('dialog', (dialog) => dialog.accept())
    await page.getByRole('button', { name: 'Move to trash' }).click()
    await expect(page).toHaveURL(/show=trash/)
    await expect(page.getByRole('heading', { name: 'Recoverable trash' })).toBeVisible()
    await page.getByRole('link', { name: /Manual lifecycle study/ }).click()
    await page.getByRole('button', { name: 'Restore' }).click()
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/overview`))

    await page.reload()
    await openView(page, 'Outline')
    await page.getByRole('button', { name: 'Done', exact: true }).click()
    await expect(taskRow(page, 'Collect data').getByRole('combobox', { name: 'Status for Collect data' })).toHaveValue('done')
    await page.getByRole('button', { name: 'All', exact: true }).click()
    await expect(taskRow(page, 'Analyze results').getByText('Ready', { exact: true })).toBeVisible()
    await openView(page, 'Artifacts')
    await expect(page.getByRole('row').filter({ hasText: 'Analysis summary' })).toContainText('Analyze results')
    expect(await fs.readFile(resultPath, 'utf8')).toBe('# Browser result\n\nAccuracy improved to 91%.\n')
  })

  test('reviews, applies, conflicts, and regenerates real agent proposals', async ({ page }, testInfo) => {
    const { fixtureRoot } = metadata(testInfo.config.metadata)
    const root = path.join(fixtureRoot, 'proposal-project')
    await fs.mkdir(root, { recursive: true })
    await fs.writeFile(path.join(root, 'PLAN.md'), '# Plan\nCreate the reviewed task.\n', 'utf8')
    const projectId = await enroll(page, 'Proposal reconciliation study', root)
    await openView(page, 'Outline')
    await createPipeline(page, 'Experiments')

    const client = await agentClient(testInfo)
    try {
      let current = await snapshot(client, projectId)
      let pipeline = current.pipelines.find((item) => item.title === 'Experiments')!
      const taskId = randomUUID()
      const created = await createProposal(client, projectId, current.project.semantic_revision, 'Draft the documented experiment task', [
        agentOperation('task.create', { id: taskId, pipeline_id: pipeline.id, title: 'Agent drafted task', user_key: 'AGENT-01', position: 0 }, taskId),
      ])

      await openView(page, 'Proposals')
      let card = page.locator('article.proposal-card').filter({ hasText: 'Draft the documented experiment task' })
      await expect(card).toContainText('Codex browser journey')
      await card.getByRole('button', { name: 'Review proposal' }).click()
      await expect(card.getByText('Experiments', { exact: true })).toBeVisible()
      await expect(card.getByRole('button', { name: 'Edit task Agent drafted task' })).toBeVisible()
      await expectNoA11yViolations(page, 'Proposal review')

      await card.getByRole('tab', { name: 'Operation audit' }).click()
      const initialApproval = card.getByRole('checkbox', { name: 'Select Agent drafted task (Task Create)' })
      await expect(initialApproval).not.toBeChecked()
      await expect(card.getByRole('button', { name: 'Apply 0 selected' })).toBeDisabled()
      await card.getByRole('button', { name: /Agent drafted task/ }).click()
      await expect(card.getByText('Reviewed project plan')).toBeVisible()

      await card.getByRole('tab', { name: 'Proposed outline' }).click()
      await card.getByRole('button', { name: 'Split Agent drafted task into subtasks' }).click()
      const splitDialog = page.getByRole('dialog', { name: 'Split proposed task' })
      await splitDialog.getByLabel('Subtask titles').fill('Prepare inputs\nRun evaluation')
      await splitDialog.getByRole('button', { name: 'Split task' }).click()
      await expect(splitDialog).toBeHidden()
      await expect(card.getByText('Prepare inputs', { exact: true })).toBeVisible()
      await expect(card.getByText('Run evaluation', { exact: true })).toBeVisible()
      await expect(card.getByText('You have unsaved staging edits.')).toBeVisible()
      await expect(card.getByRole('button', { name: 'Save draft before applying' })).toBeDisabled()
      await card.getByRole('button', { name: 'Save reviewed draft' }).click()
      await page.getByRole('button', { name: 'Review proposal' }).click()

      card = page.locator('article.proposal-card').filter({ has: page.getByText('Human-reviewed replacement draft.', { exact: true }) })
      await expect(card).toContainText(created.id)
      await page.getByRole('button', { name: 'View details' }).click()
      const supersededCard = page.locator('article.proposal-card').filter({ has: page.getByText('This draft was superseded without changing its recorded operations.', { exact: true }) })
      await expect(supersededCard.getByText('Superseded', { exact: true })).toBeVisible()

      await card.getByRole('tab', { name: 'Operation audit' }).click()
      const selectReplacement = card.getByLabel('Select all staged operations')
      await expect(selectReplacement).not.toBeChecked()
      await expect(card.getByRole('button', { name: 'Apply 0 selected' })).toBeDisabled()
      await selectReplacement.check()
      const reviewedApply = card.getByRole('button', { name: /Apply \d+ selected/ })
      await expect(reviewedApply).toBeEnabled()
      await reviewedApply.click()
      let applyDialog = page.getByRole('dialog', { name: 'Apply selected proposal changes' })
      await expect(applyDialog.getByText('Application is atomic.')).toBeVisible()
      await applyDialog.getByRole('button', { name: /Apply \d+ selected/ }).click()
      card = page.locator('article.proposal-card').filter({ has: page.getByText('Applied', { exact: true }) })
      await expect(card.getByText('Applied', { exact: true })).toBeVisible()
      await openView(page, 'Outline')
      await expect(taskRow(page, 'Agent drafted task')).toBeVisible()
      await expect(taskRow(page, 'Prepare inputs')).toBeVisible()
      await expect(taskRow(page, 'Run evaluation')).toBeVisible()

      current = await snapshot(client, projectId)
      pipeline = current.pipelines.find((item) => item.id === pipeline.id)!
      const stale = await createProposal(client, projectId, current.project.semantic_revision, 'Rename the experiment pipeline from stale evidence', [
        agentOperation('pipeline.update', { title: 'Agent stale title' }, pipeline.id, pipeline.version),
      ])

      await openView(page, 'Outline')
      await page.getByRole('button', { name: 'Edit Experiments' }).click()
      const pipelineDialog = page.getByRole('dialog', { name: 'Edit pipeline' })
      await pipelineDialog.getByLabel('Title').fill('Experiments — manually reviewed')
      await pipelineDialog.getByRole('button', { name: 'Save pipeline' }).click()
      await expect(pipelineDialog).toBeHidden()

      await openView(page, 'Proposals')
      card = page.locator('article.proposal-card').filter({ hasText: 'Rename the experiment pipeline from stale evidence' })
      await card.getByRole('button', { name: 'Review proposal' }).click()
      await expect(card.getByText('This proposal is stale and cannot be applied.')).toBeVisible()
      await expect(card.getByRole('button', { name: 'Regeneration required' })).toBeDisabled()
      const staleApply = await client.post(`/api/v1/projects/${projectId}/proposals/${stale.id}/apply`, {
        data: { request_id: randomUUID(), selected_operation_ids: stale.operations.map((operation) => operation.id) },
      })
      expect(staleApply.status(), await staleApply.text()).toBe(409)
      await page.reload()
      card = page.locator('article.proposal-card').filter({ hasText: 'Rename the experiment pipeline from stale evidence' })
      await expect(card.getByText('Conflict', { exact: true })).toBeVisible()

      current = await snapshot(client, projectId)
      pipeline = current.pipelines.find((item) => item.id === pipeline.id)!
      const regenerated = await createProposal(client, projectId, current.project.semantic_revision, 'Regenerate the pipeline update against current monitor state', [
        agentOperation('pipeline.update', { title: 'Experiments — agent reconciled' }, pipeline.id, pipeline.version),
      ])
      await page.reload()
      card = page.locator('article.proposal-card').filter({ hasText: 'Regenerate the pipeline update against current monitor state' })
      await card.getByRole('button', { name: 'Review proposal' }).click()
      await expect(card.getByText('This proposal is stale and cannot be applied.')).toHaveCount(0)
      await card.getByRole('tab', { name: 'Operation audit' }).click()
      const regeneratedApproval = card.getByRole('checkbox', { name: 'Select Experiments — agent reconciled (Pipeline Update)' })
      await expect(regeneratedApproval).not.toBeChecked()
      await expect(card.getByRole('button', { name: 'Apply 0 selected' })).toBeDisabled()
      await regeneratedApproval.check()
      const regeneratedApply = card.getByRole('button', { name: `Apply ${regenerated.operations.length} selected` })
      await expect(regeneratedApply).toBeEnabled()
      await regeneratedApply.click()
      applyDialog = page.getByRole('dialog', { name: 'Apply selected proposal changes' })
      await applyDialog.getByRole('button', { name: `Apply ${regenerated.operations.length} selected` }).click()
      await expect(card.getByText('Applied', { exact: true })).toBeVisible()
      await openView(page, 'Outline')
      await expect(page.getByRole('heading', { name: 'Experiments — agent reconciled' })).toBeVisible()
    } finally {
      await client.dispose()
    }
  })

  test('preserves a dirty graphical draft when an authenticated client closes it remotely', async ({ page }, testInfo) => {
    const { fixtureRoot } = metadata(testInfo.config.metadata)
    const root = path.join(fixtureRoot, 'proposal-concurrency-project')
    await fs.mkdir(root, { recursive: true })
    await fs.writeFile(path.join(root, 'PLAN.md'), '# Concurrent plan\nCreate the recovery task.\n', 'utf8')

    const projectId = await enroll(page, 'Proposal concurrency study', root)
    await openView(page, 'Outline')
    await createPipeline(page, 'Concurrent review')

    const client = await agentClient(testInfo)
    try {
      const current = await snapshot(client, projectId)
      const pipeline = current.pipelines.find((item) => item.title === 'Concurrent review')!
      const taskId = randomUUID()
      const draft = await createProposal(client, projectId, current.project.semantic_revision, 'Draft for concurrent graphical review', [
        agentOperation('task.create', {
          id: taskId,
          pipeline_id: pipeline.id,
          title: 'Agent concurrency task',
          user_key: 'CONCURRENT-01',
          position: 0,
        }, taskId),
      ])

      await openView(page, 'Proposals')
      let card = page.locator('article.proposal-card').filter({ hasText: 'Draft for concurrent graphical review' })
      await card.getByRole('button', { name: 'Review proposal' }).click()
      await card.getByRole('button', { name: 'Edit task Agent concurrency task' }).click()
      const editor = page.getByRole('dialog', { name: 'Edit proposed task' })
      await editor.getByLabel('Task title').fill('Page A recovered title')
      await editor.getByRole('button', { name: 'Save task changes' }).click()
      await expect(editor).toBeHidden()
      await expect(card.getByText('You have unsaved staging edits.')).toBeVisible()
      await expect(card.getByRole('button', { name: 'Reject proposal' })).toBeDisabled()

      // A route transition unmounts the proposal card. Returning must restore
      // the unsaved graphical draft before any remote state change occurs.
      await openView(page, 'Outline')
      await openView(page, 'Proposals')
      card = page.locator('article.proposal-card').filter({ hasText: 'Draft for concurrent graphical review' })
      await card.getByRole('button', { name: 'Review proposal' }).click()
      await expect(card.getByRole('button', { name: 'Edit task Page A recovered title' })).toBeVisible()
      await expect(card.getByText('You have unsaved staging edits.')).toBeVisible()

      const rejectedOutboxReplay = page.waitForResponse(async (response) => {
        if (!response.url().includes('/api/v1/events?after=') || response.status() !== 200) return false
        try {
          const payload = await response.json() as { events?: Array<{ event_type?: string; project_id?: string }> }
          return Boolean(payload.events?.some((event) => (
            event.event_type === 'proposal.rejected' && event.project_id === projectId
          )))
        } catch {
          return false
        }
      }, { timeout: 15_000 })
      const rejected = await client.post(`/api/v1/projects/${projectId}/proposals/${draft.id}/reject`, {
        data: { request_id: randomUUID(), reason: 'Closed by a concurrent reviewer.' },
      })
      expect(rejected.ok(), await rejected.text()).toBeTruthy()
      await rejectedOutboxReplay

      // No reload: the persistent outbox invalidates the live proposal query.
      card = page.locator('article.proposal-card').filter({ hasText: 'Draft for concurrent graphical review' })
      await card.getByRole('button', { name: 'View details' }).click()
      await expect(card.getByText('Recovered staged edits conflict with a closed proposal.')).toBeVisible()
      await expect(card.getByText('Recovered edits · read-only')).toBeVisible()
      await card.getByRole('tab', { name: 'Proposed outline' }).click()
      const recoveredEdit = card.getByRole('button', { name: 'Edit task Page A recovered title' })
      await expect(recoveredEdit).toBeVisible()
      await expect(recoveredEdit).toBeDisabled()
      await expect(card.getByRole('button', { name: 'Reject proposal' })).toBeDisabled()
      await expect(card.getByRole('button', { name: 'Save reviewed draft' })).toBeDisabled()
      await expect(card.getByRole('button', { name: 'Draft closed — cannot apply' })).toBeDisabled()
      await expect(card.getByRole('button', { name: 'Copy staged JSON' })).toBeEnabled()

      await card.getByRole('button', { name: 'Discard recovered edits' }).click()
      await expect(card.getByText('Recovered staged edits conflict with a closed proposal.')).toHaveCount(0)
      await expect(card.getByText('Page A recovered title', { exact: true })).toHaveCount(0)
      await expect(card.getByText('Rejected', { exact: true })).toBeVisible()
    } finally {
      await client.dispose()
    }
  })
})

/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../lib/api'
import type { ProjectSnapshot, Proposal } from '../types'
import { ProposalsView } from './ProposalsView'


const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const operationId = '33333333-3333-4333-8333-333333333333'
const taskId = '66666666-6666-4666-8666-666666666666'

const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Proposal diff test',
    root_path: '/tmp/proposal-diff-test',
    color: '#5c6e48',
    archived: false,
    semantic_revision: 2,
    layout_revision: 0,
  },
  scan_policy: {
    preferred_sources: [],
    include_globs: [],
    exclude_globs: [],
    max_text_file_size: 2_097_152,
    allow_git_metadata: false,
    git_history_limit: 0,
    sensitive_patterns: [],
    allow_outside_sources: false,
    follow_symlinks: false,
  },
  artifact_roots: [],
  pipelines: [{
    id: pipelineId,
    project_id: projectId,
    title: 'Changed later',
    flow_mode: 'sequential',
    position: 0,
    archived: false,
    version: 2,
  }],
  tasks: [],
  edges: [],
  journals: [],
  artifacts: [],
  task_artifacts: [],
  layouts: [],
  progress: { leaf_total: 0, leaf_done: 0, ready: 0, waiting: 0, blocked: 0, review: 0 },
}

function proposal(operation: Proposal['operations'][number]): Proposal {
  return {
    id: '44444444-4444-4444-8444-444444444444',
    project_id: projectId,
    summary: 'Stored proposal history',
    status: 'applied',
    base_semantic_revision: 1,
    created_at: '2026-07-15T00:00:00Z',
    operations: [operation],
  }
}

function draftProposal(operation: Proposal['operations'][number], overrides: Partial<Proposal> = {}): Proposal {
  return {
    ...proposal(operation),
    summary: 'Draft the documented experiment task',
    rationale: 'PLAN.md describes a task that needs graphical review.',
    status: 'draft',
    base_semantic_revision: 2,
    actor_label: 'Codex staging test',
    ...overrides,
  }
}

function taskCreateOperation(title = 'Agent drafted task'): Proposal['operations'][number] {
  return {
    id: operationId,
    type: 'task.create',
    entity_id: taskId,
    data: {
      id: taskId,
      pipeline_id: pipelineId,
      title,
      user_key: 'AGENT-01',
      kind: 'task',
      status: 'planned',
      priority: 'required',
      child_flow_mode: 'freeform',
      position: 0,
    },
    rationale: 'PLAN.md records the task.',
    confidence: 0.92,
    evidence: [{ kind: 'source_text', summary: 'The plan names this work.', locator: 'PLAN.md#task' }],
    source_references: [{ path: 'PLAN.md', anchor: 'Task', opaque_key: 'AGENT-01' }],
    prerequisite_operation_ids: [],
    disposition: 'pending',
  }
}

function renderView(value: Proposal | Proposal[]) {
  const getProposals = vi.spyOn(api, 'getProposals').mockResolvedValue(Array.isArray(value) ? value : [value])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <ProposalsView snapshot={snapshot} />
    </QueryClientProvider>,
  )
  return { client, getProposals, ...view }
}

afterEach(() => {
  cleanup()
  window.sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('ProposalsView immutable diffs', () => {
  it('provides roving Arrow, Home, and End keyboard behavior for review tabs', async () => {
    const original = draftProposal(taskCreateOperation())
    renderView(original)

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    const outlineTab = within(card).getByRole('tab', { name: 'Proposed outline' }) as HTMLButtonElement
    const auditTab = within(card).getByRole('tab', { name: 'Operation audit' }) as HTMLButtonElement
    expect(outlineTab.tabIndex).toBe(0)
    expect(auditTab.tabIndex).toBe(-1)
    expect(outlineTab.getAttribute('aria-selected')).toBe('true')
    expect(document.getElementById(outlineTab.getAttribute('aria-controls')!)?.getAttribute('aria-labelledby')).toBe(outlineTab.id)

    outlineTab.focus()
    fireEvent.keyDown(outlineTab, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(auditTab)
    expect(auditTab.tabIndex).toBe(0)
    expect(auditTab.getAttribute('aria-selected')).toBe('true')
    expect(document.getElementById(auditTab.getAttribute('aria-controls')!)?.getAttribute('aria-labelledby')).toBe(auditTab.id)

    fireEvent.keyDown(auditTab, { key: 'Home' })
    expect(document.activeElement).toBe(outlineTab)
    expect(outlineTab.getAttribute('aria-selected')).toBe('true')

    fireEvent.keyDown(outlineTab, { key: 'End' })
    expect(document.activeElement).toBe(auditTab)
    fireEvent.keyDown(auditTab, { key: 'ArrowRight' })
    expect(document.activeElement).toBe(outlineTab)
    expect(outlineTab.getAttribute('aria-selected')).toBe('true')
  })

  it('starts approval empty, stages a named outline edit, and saves the full reviewed draft', async () => {
    const original = draftProposal(taskCreateOperation())
    const replacement = draftProposal(taskCreateOperation('Human reviewed task'), {
      id: '77777777-7777-4777-8777-777777777777',
      supersedes_proposal_id: original.id,
    })
    const apply = vi.spyOn(api, 'applyProposal').mockResolvedValue({
      request_id: '55555555-5555-4555-8555-555555555555', project_id: projectId,
      semantic_revision: 3, layout_revision: 0, results: [],
    })
    const revise = vi.spyOn(api, 'reviseProposal').mockResolvedValue(replacement)
    renderView(original)

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    expect(within(card).getByText('Changed later')).not.toBeNull()
    expect(within(card).getByRole('button', { name: 'Edit task Agent drafted task' })).not.toBeNull()

    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    const approval = within(card).getByRole('checkbox', { name: 'Select Agent drafted task (Task Create)' }) as HTMLInputElement
    expect(approval.checked).toBe(false)
    expect((within(card).getByRole('button', { name: 'Apply 0 selected' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(within(card).getByRole('tab', { name: 'Proposed outline' }))

    fireEvent.click(within(card).getByRole('button', { name: 'Edit task Agent drafted task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Human reviewed task' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))

    expect(await within(card).findByText('You have unsaved staging edits.')).not.toBeNull()
    const blockedApply = within(card).getByRole('button', { name: 'Save draft before applying' }) as HTMLButtonElement
    expect(blockedApply.disabled).toBe(true)
    fireEvent.click(blockedApply)
    expect(apply).not.toHaveBeenCalled()

    fireEvent.click(within(card).getByRole('button', { name: 'Save reviewed draft' }))
    await waitFor(() => expect(revise).toHaveBeenCalledTimes(1))
    const call = revise.mock.calls[0]
    expect(call.slice(0, 5)).toEqual([
      projectId,
      original.id,
      original.base_semantic_revision,
      original.summary,
      original.rationale,
    ])
    expect(call[6]).toEqual(expect.any(String))
    expect(call[5]).toHaveLength(1)
    expect(call[5][0]).toMatchObject({
      type: 'task.create',
      entity_id: taskId,
      data: expect.objectContaining({
        id: taskId,
        pipeline_id: pipelineId,
        title: 'Human reviewed task',
        user_key: 'AGENT-01',
      }),
    })
  })

  it('reuses the same apply request ID when retrying the exact selected-operation payload', async () => {
    const original = draftProposal(taskCreateOperation())
    const apply = vi.spyOn(api, 'applyProposal').mockRejectedValue(new Error('Apply response was lost'))
    renderView(original)

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    fireEvent.click(within(card).getByRole('checkbox', { name: 'Select Agent drafted task (Task Create)' }))
    const applyButton = within(card).getByRole('button', { name: 'Apply 1 selected' }) as HTMLButtonElement

    fireEvent.click(applyButton)
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(applyButton.disabled).toBe(false))
    fireEvent.click(applyButton)
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(2))

    expect(apply.mock.calls[0].slice(0, 4)).toEqual([projectId, original.id, [operationId], []])
    expect(apply.mock.calls[0][4]).toEqual(expect.any(String))
    expect(apply.mock.calls[1][4]).toBe(apply.mock.calls[0][4])
  })

  it('invalidates the project-search prefix after applying a proposal', async () => {
    const original = draftProposal(taskCreateOperation())
    vi.spyOn(api, 'applyProposal').mockResolvedValue({
      request_id: '55555555-5555-4555-8555-555555555555',
      project_id: projectId,
      semantic_revision: 3,
      layout_revision: 0,
      results: [],
    })
    const { client } = renderView(original)
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    fireEvent.click(within(card).getByRole('checkbox', { name: 'Select Agent drafted task (Task Create)' }))
    fireEvent.click(within(card).getByRole('button', { name: 'Apply 1 selected' }))

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['project-search', projectId],
    }))
  })

  it('reuses the same reject request ID when retrying the exact reason payload', async () => {
    const original = draftProposal(taskCreateOperation())
    const reject = vi.spyOn(api, 'rejectProposal').mockRejectedValue(new Error('Reject response was lost'))
    vi.spyOn(window, 'prompt').mockReturnValue('Duplicate task')
    renderView(original)

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    const rejectButton = within(card).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement
    fireEvent.click(rejectButton)
    await waitFor(() => expect(reject).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(rejectButton.disabled).toBe(false))
    fireEvent.click(rejectButton)
    await waitFor(() => expect(reject).toHaveBeenCalledTimes(2))

    expect(reject.mock.calls[0].slice(0, 3)).toEqual([projectId, original.id, 'Duplicate task'])
    expect(reject.mock.calls[0][3]).toEqual(expect.any(String))
    expect(reject.mock.calls[1][3]).toBe(reject.mock.calls[0][3])
  })

  it('blocks a zero-operation replacement and gives an explicit path back to rejection', async () => {
    const original = draftProposal(taskCreateOperation())
    const revise = vi.spyOn(api, 'reviseProposal').mockResolvedValue(original)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderView(original)

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('button', { name: 'Remove proposed task Agent drafted task' }))

    expect(await within(card).findByText('No operations remain in this staged draft.')).not.toBeNull()
    const saveButton = within(card).getByRole('button', { name: 'No operations to save' }) as HTMLButtonElement
    const rejectButton = within(card).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement
    expect(saveButton.disabled).toBe(true)
    expect(rejectButton.disabled).toBe(true)
    fireEvent.click(saveButton)
    expect(revise).not.toHaveBeenCalled()

    fireEvent.click(within(card).getByRole('button', { name: 'Restore original operations' }))
    await waitFor(() => expect(within(card).queryByText('No operations remain in this staged draft.')).toBeNull())
    expect(rejectButton.disabled).toBe(false)
    expect(revise).not.toHaveBeenCalled()
  })

  it('shows immutable supersession lineage on both the replacement and original draft', async () => {
    const originalId = '44444444-4444-4444-8444-444444444444'
    const replacementId = '77777777-7777-4777-8777-777777777777'
    const original = draftProposal(taskCreateOperation(), {
      id: originalId,
      summary: 'Original agent draft',
      status: 'superseded',
      superseded_by_proposal_id: replacementId,
    })
    const replacement = draftProposal(taskCreateOperation('Human reviewed task'), {
      id: replacementId,
      summary: 'Human-reviewed replacement',
      supersedes_proposal_id: originalId,
    })
    renderView([replacement, original])

    const replacementCard = (await screen.findByRole('heading', { name: 'Human-reviewed replacement' })).closest('article')!
    expect(within(replacementCard).getByText('Human-reviewed replacement draft.')).not.toBeNull()
    expect(within(replacementCard).getByText(new RegExp(originalId))).not.toBeNull()

    const originalCard = screen.getByRole('heading', { name: 'Original agent draft' }).closest('article')!
    expect(within(originalCard).getByText('Superseded', { exact: true })).not.toBeNull()
    expect(within(originalCard).getByText('This draft was superseded without changing its recorded operations.')).not.toBeNull()
    expect(within(originalCard).getByText(new RegExp(replacementId))).not.toBeNull()
  })

  it('recovers dirty staged operations after a route-style unmount and keeps reject and apply blocked', async () => {
    const original = draftProposal(taskCreateOperation())
    const apply = vi.spyOn(api, 'applyProposal').mockResolvedValue({
      request_id: '55555555-5555-4555-8555-555555555555',
      project_id: projectId,
      semantic_revision: 3,
      layout_revision: 0,
      results: [],
    })
    const revise = vi.spyOn(api, 'reviseProposal').mockResolvedValue(original)
    const reject = vi.spyOn(api, 'rejectProposal').mockResolvedValue()
    const storageWrite = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage unavailable', 'QuotaExceededError')
    })
    const first = renderView(original)

    let card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('button', { name: 'Edit task Agent drafted task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Recovered route edit' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))

    expect(await within(card).findByText('You have unsaved staging edits.')).not.toBeNull()
    expect(window.sessionStorage.length).toBe(0)
    expect((within(card).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement).disabled).toBe(true)
    first.unmount()
    storageWrite.mockRestore()

    renderView(original)
    card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    expect(within(card).getByRole('button', { name: 'Edit task Recovered route edit' })).not.toBeNull()
    expect(within(card).getByText('You have unsaved staging edits.')).not.toBeNull()
    const rejectButton = within(card).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement
    const applyButton = within(card).getByRole('button', { name: 'Save draft before applying' }) as HTMLButtonElement
    expect(rejectButton.disabled).toBe(true)
    expect(applyButton.disabled).toBe(true)
    fireEvent.click(rejectButton)
    fireEvent.click(applyButton)
    expect(reject).not.toHaveBeenCalled()
    expect(apply).not.toHaveBeenCalled()
    expect(revise).not.toHaveBeenCalled()
    fireEvent.click(within(card).getByRole('button', { name: 'Discard edits' }))
    await waitFor(() => expect(screen.queryByText('You have unsaved staging edits.')).toBeNull())
  })

  it('recovers a dirty edit when the server returns the same operations in a different row order', async () => {
    const firstOperation = taskCreateOperation()
    const secondTaskId = '88888888-8888-4888-8888-888888888888'
    const secondOperation = {
      ...taskCreateOperation('Second agent task'),
      id: '99999999-9999-4999-8999-999999999999',
      entity_id: secondTaskId,
      data: {
        ...taskCreateOperation('Second agent task').data,
        id: secondTaskId,
        title: 'Second agent task',
        user_key: 'AGENT-02',
        position: 1,
      },
    }
    const original = draftProposal(firstOperation, { operations: [firstOperation, secondOperation] })
    const first = renderView(original)

    let card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('button', { name: 'Edit task Agent drafted task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Recovered after server reorder' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))
    expect(await within(card).findByText('You have unsaved staging edits.')).not.toBeNull()
    await waitFor(() => expect(window.sessionStorage.length).toBe(1))
    first.unmount()

    renderView({ ...original, operations: [secondOperation, firstOperation] })
    card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    expect(within(card).getByRole('button', { name: 'Edit task Recovered after server reorder' })).not.toBeNull()
    expect(within(card).getByText('You have unsaved staging edits.')).not.toBeNull()

    fireEvent.click(within(card).getByRole('button', { name: 'Discard edits' }))
    await waitFor(() => expect(window.sessionStorage.length).toBe(0))
  })

  it('ignores malformed recovery JSON and records from a different immutable baseline', async () => {
    const original = draftProposal(taskCreateOperation())
    const key = `research-monitor:proposal-staging:v1:${projectId}:${original.id}`
    window.sessionStorage.setItem(key, '{malformed')
    const malformed = renderView(original)

    let card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    expect(within(card).getByRole('button', { name: 'Edit task Agent drafted task' })).not.toBeNull()
    expect(within(card).queryByText('You have unsaved staging edits.')).toBeNull()
    expect(window.sessionStorage.getItem(key)).toBeNull()
    malformed.unmount()

    window.sessionStorage.setItem(key, JSON.stringify({
      version: 1,
      project_id: projectId,
      proposal_id: original.id,
      original_signature: 'different-server-baseline',
      operations: [taskCreateOperation('Stale recovered title')],
    }))
    renderView(original)

    card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    expect(within(card).getByRole('button', { name: 'Edit task Agent drafted task' })).not.toBeNull()
    expect(within(card).queryByText('Stale recovered title')).toBeNull()
    expect(within(card).queryByText('You have unsaved staging edits.')).toBeNull()
    expect(window.sessionStorage.getItem(key)).toBeNull()
  })

  it('preserves a dirty outline read-only when a query refresh supersedes its server draft', async () => {
    const original = draftProposal(taskCreateOperation())
    const closed: Proposal = {
      ...original,
      status: 'superseded',
      superseded_by_proposal_id: '77777777-7777-4777-8777-777777777777',
    }
    const apply = vi.spyOn(api, 'applyProposal').mockResolvedValue({
      request_id: '55555555-5555-4555-8555-555555555555',
      project_id: projectId,
      semantic_revision: 3,
      layout_revision: 0,
      results: [],
    })
    const revise = vi.spyOn(api, 'reviseProposal').mockResolvedValue(original)
    const reject = vi.spyOn(api, 'rejectProposal').mockResolvedValue()
    const { client, getProposals } = renderView(original)

    let card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('button', { name: 'Edit task Agent drafted task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Recovered superseded edit' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))
    await waitFor(() => expect(window.sessionStorage.length).toBe(1))

    getProposals.mockResolvedValue([closed])
    await client.invalidateQueries({ queryKey: ['proposals', projectId] })
    expect(await screen.findByText('Recovered staged edits conflict with a closed proposal.')).not.toBeNull()

    card = screen.getByRole('heading', { name: original.summary }).closest('article')!
    expect(within(card).getByText('Recovered edits · read-only')).not.toBeNull()
    fireEvent.click(within(card).getByRole('tab', { name: 'Proposed outline' }))
    expect(within(card).getByText('Recovered superseded edit', { exact: true })).not.toBeNull()
    expect((within(card).getByRole('button', { name: 'Edit task Recovered superseded edit' }) as HTMLButtonElement).disabled).toBe(true)

    const rejectButton = within(card).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement
    const saveButton = within(card).getByRole('button', { name: 'Save reviewed draft' }) as HTMLButtonElement
    const applyButton = within(card).getByRole('button', { name: 'Draft closed — cannot apply' }) as HTMLButtonElement
    expect(rejectButton.disabled).toBe(true)
    expect(saveButton.disabled).toBe(true)
    expect(applyButton.disabled).toBe(true)
    expect(within(card).getByRole('button', { name: 'Copy staged JSON' })).not.toBeNull()
    fireEvent.click(rejectButton)
    fireEvent.click(saveButton)
    fireEvent.click(applyButton)
    expect(reject).not.toHaveBeenCalled()
    expect(revise).not.toHaveBeenCalled()
    expect(apply).not.toHaveBeenCalled()

    fireEvent.click(within(card).getByRole('button', { name: 'Discard recovered edits' }))
    await waitFor(() => expect(window.sessionStorage.length).toBe(0))
    expect(screen.queryByText('Recovered staged edits conflict with a closed proposal.')).toBeNull()
  })

  it('shows stored proposal-time state in closed history instead of live state', async () => {
    renderView(proposal({
      id: operationId,
      type: 'pipeline.update',
      entity_id: pipelineId,
      expected_version: 1,
      data: { title: 'Agent title' },
      rationale: 'PLAN.md records the title',
      confidence: 0.9,
      disposition: 'applied',
      before: { id: pipelineId, title: 'Proposal-time title', version: 1 },
      after: { id: pipelineId, title: 'Agent title', version: 2 },
    }))

    fireEvent.click(await screen.findByRole('button', { name: /Agent title/i }))
    expect(screen.getByText('Before (proposal time)')).not.toBeNull()
    expect(screen.getByText('After (proposed)')).not.toBeNull()
    const values = screen.getAllByText((_text, element) => element?.tagName === 'PRE')
    expect(values.some((element) => element.textContent?.includes('Proposal-time title'))).toBe(true)
    expect(values.some((element) => element.textContent?.includes('Agent title'))).toBe(true)
    expect(values.some((element) => element.textContent?.includes('Changed later'))).toBe(false)
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('labels live-current rendering as a fallback for legacy bare rows', async () => {
    renderView(proposal({
      id: operationId,
      type: 'pipeline.update',
      entity_id: pipelineId,
      expected_version: 1,
      data: { title: 'Legacy agent title' },
      rationale: 'Legacy proposal',
      confidence: 0.8,
      disposition: 'applied',
    }))

    fireEvent.click(await screen.findByRole('button', { name: /Legacy agent title/i }))
    expect(screen.getByText('Current data (legacy fallback)')).not.toBeNull()
    expect(screen.getByText(/no proposal-time snapshot was stored/i)).not.toBeNull()
    const values = screen.getAllByText((_text, element) => element?.tagName === 'PRE')
    expect(values.some((element) => element.textContent?.includes('Changed later'))).toBe(true)
  })

  it("shows structured evidence summaries and resolved source anchors", async () => {
    renderView(proposal({
      id: operationId,
      type: "pipeline.update",
      entity_id: pipelineId,
      expected_version: 1,
      data: { title: "Evidence-aware title" },
      rationale: "The project tracker supports this change",
      confidence: 0.9,
      disposition: "applied",
      evidence: [{ kind: "completion_text", summary: "The tracker explicitly reports completion.", locator: "TRACKER.md#done" }],
      source_references: [{ path: "TRACKER.md", anchor: "Done", opaque_key: "T-1" }],
    }))

    fireEvent.click(await screen.findByRole("button", { name: /Evidence-aware title/i }))
    expect(screen.getByText("The tracker explicitly reports completion.")).not.toBeNull()
    expect(screen.getByText(/Completion Text · TRACKER.md#done/)).not.toBeNull()
    expect(screen.getByText(/source · TRACKER.md#Done · key T-1/)).not.toBeNull()
  })

  it("renders permissive evidence safely and resolves source identity aliases", async () => {
    renderView(proposal({
      id: operationId,
      type: "pipeline.update",
      entity_id: pipelineId,
      expected_version: 1,
      data: { title: "Permissive evidence title" },
      rationale: "Exercise the permissive wire contract",
      confidence: 0.7,
      disposition: "applied",
      evidence: ["Direct user instruction", { kind: { unexpected: true }, summary: { nested: true }, locator: "RESULTS.md#summary" }],
      source_references: [{ source_path: "RESULTS.md", anchor: "Summary", monitor_reference_id: "ref-7", fingerprint: "sha256:abc" }],
    }))

    fireEvent.click(await screen.findByRole("button", { name: /Permissive evidence title/i }))
    expect(screen.getByText("Direct user instruction")).not.toBeNull()
    expect(screen.getByText("RESULTS.md#summary")).not.toBeNull()
    expect(screen.getByText(/source · RESULTS.md#Summary · reference ref-7 · fingerprint sha256:abc/)).not.toBeNull()
  })

  it('makes a stale draft non-actionable and gives each operation a specific accessible label', async () => {
    const apply = vi.spyOn(api, 'applyProposal').mockResolvedValue({
      request_id: '55555555-5555-4555-8555-555555555555', project_id: projectId,
      semantic_revision: 2, layout_revision: 0, results: [],
    })
    renderView({
      ...proposal({
        id: operationId,
        type: 'pipeline.update',
        entity_id: pipelineId,
        expected_version: 1,
        data: { title: 'Agent title' },
        rationale: 'PLAN.md records the title',
        confidence: 0.9,
        disposition: 'pending',
      }),
      status: 'draft',
      base_semantic_revision: 1,
    })

    const checkbox = await screen.findByRole('checkbox', { name: 'Select Agent title (Pipeline Update)' })
    expect((checkbox as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Regeneration required' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Regeneration required' }))
    expect(apply).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Copy regeneration prompt' })).not.toBeNull()
  })
})

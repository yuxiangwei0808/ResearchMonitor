/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../lib/api'
import type { ProjectSnapshot, Proposal, ProposalOperation } from '../types'
import type { GuidedRequestSeed } from '../components/AskCodexDialog'
import { isHighRiskOperation, ProposalsView } from './ProposalsView'


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
    risk: 'normal',
    default_selected: false,
  }
}

function renderView(
  value: Proposal | Proposal[],
  onAskCodex?: (seed: GuidedRequestSeed) => void,
  lazyDetail?: Proposal | Proposal[],
) {
  let current = Array.isArray(value) ? value : [value]
  const details = lazyDetail == null ? null : Array.isArray(lazyDetail) ? lazyDetail : [lazyDetail]
  const getProposalPage = vi.spyOn(api, 'getProposalPage').mockImplementation(async (_projectId, options = {}) => {
    const filtered = current.filter((item) => {
      if (options.status === 'open' && item.status !== 'draft') return false
      if (options.status === 'closed' && item.status === 'draft') return false
      if (options.workflowMode && (item.workflow_mode ?? 'legacy_custom') !== options.workflowMode) return false
      if (options.scopeType && item.scope_type !== options.scopeType) return false
      return true
    })
    return { proposals: filtered, next_cursor: null, total: filtered.length, draft_count: current.filter((item) => item.status === 'draft').length }
  })
  vi.spyOn(api, 'getProposal').mockImplementation(async (_projectId, proposalId) => {
    const found = (details ?? current).find((item) => item.id === proposalId)
    if (!found) throw new Error('Proposal not found')
    return found
  })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
      <ProposalsView snapshot={snapshot} onAskCodex={onAskCodex} />
    </QueryClientProvider>,
  )
  return {
    client,
    getProposalPage,
    setProposals: (next: Proposal | Proposal[]) => { current = Array.isArray(next) ? next : [next] },
    ...view,
  }
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

  it('honors server-selected safe v2 operations while legacy drafts remain unselected', async () => {
    const operation = { ...taskCreateOperation(), default_selected: true, basis: 'source_evidence' as const }
    const guided = draftProposal(operation, {
      proposal_contract_version: '2', workflow_mode: 'suggest_next_work', scope_type: 'project', result_kind: 'changes',
    })
    renderView(guided)
    const card = (await screen.findByRole('heading', { name: guided.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    expect((within(card).getByRole('checkbox', { name: 'Select Agent drafted task (Task Create)' }) as HTMLInputElement).checked).toBe(true)
    expect((within(card).getByRole('button', { name: 'Apply 1 selected' }) as HTMLButtonElement).disabled).toBe(false)
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
    expect(call[5][0]).not.toHaveProperty('risk')
    expect(call[5][0]).not.toHaveProperty('default_selected')
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
    const confirmation = await screen.findByRole('dialog', { name: 'Apply selected proposal changes' })
    const confirmApply = within(confirmation).getByRole('button', { name: 'Apply 1 selected' }) as HTMLButtonElement
    fireEvent.click(confirmApply)
    await waitFor(() => expect(apply).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(confirmApply.disabled).toBe(false))
    fireEvent.click(confirmApply)
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
    const confirmation = await screen.findByRole('dialog', { name: 'Apply selected proposal changes' })
    fireEvent.click(within(confirmation).getByRole('button', { name: 'Apply 1 selected' }))

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['project-search', projectId],
    }))
  })

  it('reuses the same reject request ID when retrying the exact reason payload', async () => {
    const original = draftProposal(taskCreateOperation())
    const reject = vi.spyOn(api, 'rejectProposal').mockRejectedValue(new Error('Reject response was lost'))
    renderView(original)

    const card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    const rejectButton = within(card).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement
    fireEvent.click(rejectButton)
    const rejection = await screen.findByRole('dialog', { name: 'Reject proposal' })
    fireEvent.change(within(rejection).getByRole('textbox', { name: 'Reason (optional)' }), { target: { value: 'Duplicate task' } })
    const confirmReject = within(rejection).getByRole('button', { name: 'Reject proposal' }) as HTMLButtonElement
    fireEvent.click(confirmReject)
    await waitFor(() => expect(reject).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(confirmReject.disabled).toBe(false))
    fireEvent.click(confirmReject)
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
    const summary: Proposal = {
      ...original,
      operations: [],
      operation_count: 1,
      detail_loaded: false,
    }
    const closed: Proposal = {
      ...summary,
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
    const { client, setProposals } = renderView(summary, undefined, original)

    let card = (await screen.findByRole('heading', { name: original.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('button', { name: 'Review proposal' }))
    const editTask = await screen.findByRole('button', { name: 'Edit task Agent drafted task' })
    card = editTask.closest('article')!
    fireEvent.click(editTask)
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Recovered superseded edit' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))
    await waitFor(() => expect(window.sessionStorage.length).toBe(1))

    setProposals([closed])
    await client.invalidateQueries({ queryKey: ['proposals', projectId] })
    const viewDetails = await screen.findByRole('button', { name: 'View details' })
    card = viewDetails.closest('article')!
    fireEvent.click(viewDetails)
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

  it('regenerates an intent-bound conflict from the immutable stored intent claims', async () => {
    const intentId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const conflicted = draftProposal({ ...taskCreateOperation(), basis: 'source_evidence' }, {
      status: 'conflict',
      proposal_contract_version: '2',
      workflow_mode: 'reconcile_progress',
      scope_type: 'project',
      scope_id: null,
      intent_id: intentId,
      result_kind: 'changes',
    })
    const inspectIntent = vi.spyOn(api, 'getAgentPrompt').mockResolvedValue({
      intent_id: intentId,
      expires_at: '2026-07-18T00:00:00Z',
      workflow_mode: 'reconcile_progress',
      scope_type: 'project',
      scope_id: null,
      allow_completion: false,
      instructions: 'Reconcile only the bounded documented progress.',
      artifact_locators: [],
      prompt: 'Stored prompt',
    })
    const onAskCodex = vi.fn<(seed: GuidedRequestSeed) => void>()
    renderView(conflicted, onAskCodex)

    expect(await screen.findByText('This guided proposal conflicted and was not applied.')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate guided request' }))
    await waitFor(() => expect(inspectIntent).toHaveBeenCalledWith(projectId, intentId))
    expect(onAskCodex).toHaveBeenCalledWith(expect.objectContaining({
      mode: 'reconcile_progress',
      scopeType: 'project',
      instructions: 'Reconcile only the bounded documented progress.',
      allowCompletion: false,
      regenerateProposalId: conflicted.id,
    }))
  })

  it('loads summary cards first and fetches complete operations only when review is expanded', async () => {
    const full = draftProposal({ ...taskCreateOperation(), basis: 'source_evidence' }, {
      proposal_contract_version: '2',
      workflow_mode: 'suggest_next_work',
      scope_type: 'project',
      result_kind: 'changes',
    })
    const summary: Proposal = {
      ...full,
      operations: [],
      operation_count: 1,
      detail_loaded: false,
      basis_counts: { source_evidence: 1 },
      risk_counts: { normal: 0, high: 1 },
      evidence_count: 2,
    }
    vi.spyOn(api, 'getProposalPage').mockImplementation(async (_projectId, options = {}) => ({
      proposals: options.status === 'open' ? [summary] : [],
      next_cursor: null,
      total: options.status === 'open' ? 1 : 0,
    }))
    const inspect = vi.spyOn(api, 'getProposal').mockResolvedValue(full)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ProposalsView snapshot={snapshot} /></QueryClientProvider>)

    const card = (await screen.findByRole('heading', { name: full.summary })).closest('article')!
    expect(inspect).not.toHaveBeenCalled()
    expect(within(card).queryByRole('tab', { name: 'Operation audit' })).toBeNull()
    expect(within(card).getByText('Source evidence · 1')).not.toBeNull()
    expect(within(card).getByText('High risk · 1')).not.toBeNull()
    expect(within(card).getByText('2 top-level evidence items')).not.toBeNull()

    fireEvent.click(within(card).getByRole('button', { name: 'Review proposal' }))
    await waitFor(() => expect(inspect).toHaveBeenCalledWith(projectId, full.id))
    expect(await screen.findByRole('tab', { name: 'Operation audit' })).not.toBeNull()
  })

  it('automatically retrieves every open-draft summary page and sends workflow and scope filters to the server', async () => {
    const first = { ...draftProposal(taskCreateOperation(), { id: 'open-first', summary: 'First open draft', workflow_mode: 'reconcile_progress', scope_type: 'pipeline', scope_id: pipelineId }), operations: [], operation_count: 1, detail_loaded: false }
    const second = { ...first, id: 'open-second', summary: 'Second open draft' }
    const getPage = vi.spyOn(api, 'getProposalPage').mockImplementation(async (_projectId, options = {}) => {
      if (options.status === 'closed') return { proposals: [], next_cursor: null, total: 0 }
      if (options.cursor === 'open-next') return { proposals: [second], next_cursor: null, total: 2 }
      return { proposals: [first], next_cursor: 'open-next', total: 2 }
    })
    vi.spyOn(api, 'getProposal').mockRejectedValue(new Error('Details should remain lazy'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ProposalsView snapshot={snapshot} /></QueryClientProvider>)

    expect(await screen.findByRole('heading', { name: 'Second open draft' })).not.toBeNull()
    expect(getPage).toHaveBeenCalledWith(projectId, expect.objectContaining({ status: 'open', cursor: 'open-next', limit: 100, summary: true }))

    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: 'reconcile_progress' } })
    fireEvent.change(await screen.findByLabelText('Scope'), { target: { value: 'pipeline' } })
    await waitFor(() => expect(getPage).toHaveBeenCalledWith(projectId, expect.objectContaining({ workflowMode: 'reconcile_progress', scopeType: 'pipeline' })))
  })

  it('shows only the newest history page initially and loads older summaries by cursor without inspecting details', async () => {
    const closedSummary = (index: number): Proposal => ({
      ...proposal(taskCreateOperation()),
      id: `closed-${index}`,
      summary: `Closed result ${index}`,
      operations: [],
      operation_count: 1,
      detail_loaded: false,
      workflow_mode: 'legacy_custom',
      scope_type: 'project',
    })
    const newest = Array.from({ length: 20 }, (_, index) => closedSummary(index + 1))
    const older = closedSummary(21)
    const getPage = vi.spyOn(api, 'getProposalPage').mockImplementation(async (_projectId, options = {}) => {
      if (options.status === 'open') return { proposals: [], next_cursor: null, total: 0 }
      if (options.cursor === 'older-page') return { proposals: [older], next_cursor: null, total: 21 }
      return { proposals: newest, next_cursor: 'older-page', total: 21 }
    })
    const inspect = vi.spyOn(api, 'getProposal').mockRejectedValue(new Error('Details should remain lazy'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ProposalsView snapshot={snapshot} /></QueryClientProvider>)

    expect(await screen.findByRole('heading', { name: 'Closed result 20' })).not.toBeNull()
    expect(screen.queryByRole('heading', { name: 'Closed result 21' })).toBeNull()
    expect(screen.getByText('20 of 21 history results loaded')).not.toBeNull()
    expect(inspect).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Load 20 older results' }))
    expect(await screen.findByRole('heading', { name: 'Closed result 21' })).not.toBeNull()
    expect(getPage).toHaveBeenCalledWith(projectId, expect.objectContaining({ status: 'closed', cursor: 'older-page', limit: 20, summary: true }))
    expect(inspect).not.toHaveBeenCalled()
  })

  it('renders no-change top-level evidence and source references as safe text', async () => {
    const report: Proposal = {
      ...proposal(taskCreateOperation()),
      id: '90909090-9090-4090-8090-909090909090',
      summary: 'Bounded scan found no monitor changes',
      status: 'no_changes',
      result_kind: 'no_changes',
      no_change_reason: 'up_to_date',
      workflow_mode: 'reconcile_progress',
      scope_type: 'project',
      proposal_contract_version: '2',
      operations: [],
      operation_count: 0,
      detail_loaded: true,
      scan_summary: { files_scanned: 4, text_bytes: 1024 },
      top_level_evidence: [{
        kind: 'source_text',
        summary: '<img src=x onerror=alert(1)> remains literal evidence text',
        locator: 'PROGRESS.md#Results',
        content_hash: 'sha256:safe',
      }],
      source_references: [{ path: 'PROGRESS.md', anchor: 'Results', fingerprint: 'sha256:safe' }],
    }
    renderView(report)

    const evidence = await screen.findByRole('region', { name: 'No-change evidence' })
    expect(within(evidence).getByText('<img src=x onerror=alert(1)> remains literal evidence text')).not.toBeNull()
    expect(within(evidence).getByText(/Source Text · PROGRESS.md#Results/)).not.toBeNull()
    expect(within(evidence).getByText(/source · PROGRESS.md#Results · fingerprint sha256:safe/)).not.toBeNull()
    expect(evidence.querySelector('img')).toBeNull()
  })

  it('loads real-shaped no-change summary evidence lazily and retries a failed detail request', async () => {
    const full: Proposal = {
      ...proposal(taskCreateOperation()),
      id: '91919191-9191-4191-8191-919191919191',
      summary: 'No changes after bounded review',
      status: 'no_changes',
      result_kind: 'no_changes',
      no_change_reason: 'up_to_date',
      workflow_mode: 'reconcile_progress',
      scope_type: 'project',
      proposal_contract_version: '2',
      operations: [],
      operation_count: 0,
      detail_loaded: true,
      scan_summary: { files_scanned: 2, text_bytes: 800 },
      top_level_evidence: [{ kind: 'source_text', summary: 'The tracker matches the monitor.', locator: 'PROGRESS.md#Current', content_hash: 'sha256:current' }],
      source_references: [{ path: 'PROGRESS.md', anchor: 'Current', fingerprint: 'sha256:current' }],
    }
    const summary: Proposal = {
      ...full,
      detail_loaded: undefined,
      top_level_evidence: undefined,
      source_references: undefined,
      evidence_count: 1,
      source_reference_count: 1,
    }
    vi.spyOn(api, 'getProposalPage').mockImplementation(async (_projectId, options = {}) => ({
      proposals: options.status === 'closed' ? [summary] : [], next_cursor: null, total: options.status === 'closed' ? 1 : 0,
    }))
    const inspect = vi.spyOn(api, 'getProposal').mockRejectedValueOnce(new Error('Temporary detail failure')).mockResolvedValue(full)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><ProposalsView snapshot={snapshot} /></QueryClientProvider>)

    const card = (await screen.findByRole('heading', { name: summary.summary })).closest('article')!
    expect(within(card).queryByRole('region', { name: 'No-change evidence' })).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'View report' }))
    expect(await within(card).findByText('Unable to load proposal details.')).not.toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Try again' }))
    const evidence = await screen.findByRole('region', { name: 'No-change evidence' })
    expect(within(evidence).getByText('The tracker matches the monitor.')).not.toBeNull()
    expect(inspect).toHaveBeenCalledTimes(2)
  })

  it('preserves canonical high risk for badges and selected-operation confirmation counts', async () => {
    const highRisk: Proposal['operations'][number] = {
      id: 'high-risk-pipeline-update',
      type: 'pipeline.update',
      entity_id: pipelineId,
      expected_version: 2,
      data: { title: 'Canonical high-risk edit' },
      rationale: 'Server classified this shared planning edit as high risk.',
      confidence: 0.9,
      prerequisite_operation_ids: [],
      disposition: 'pending',
      basis: 'source_evidence',
      risk: 'high',
      default_selected: true,
    }
    const guided = draftProposal(highRisk, {
      proposal_contract_version: '2', workflow_mode: 'suggest_next_work', scope_type: 'project', result_kind: 'changes',
    })
    renderView(guided)
    const card = (await screen.findByRole('heading', { name: guided.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    fireEvent.click(within(card).getByRole('button', { name: /Canonical high-risk edit/ }))
    fireEvent.click(within(card).getByRole('button', { name: 'Edit' }))
    const editor = await screen.findByRole('dialog', { name: 'Edit Pipeline Update' })
    fireEvent.change(within(editor).getByLabelText('Operation data (JSON)'), { target: { value: JSON.stringify({ title: 'Edited high-risk pipeline' }) } })
    fireEvent.click(within(editor).getByRole('button', { name: 'Save operation edit' }))
    expect(within(card).getByText('High risk · 1', { exact: true })).not.toBeNull()
    expect(within(card).getByText('1 of 1 selected · 1 high risk', { exact: true })).not.toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Discard edits' }))
    fireEvent.click(within(card).getByRole('checkbox', { name: 'Select Canonical high-risk edit (Pipeline Update)' }))
    fireEvent.click(within(card).getByRole('button', { name: 'Apply 1 selected' }))
    const confirmation = await screen.findByRole('dialog', { name: 'Apply selected proposal changes' })
    expect(within(confirmation).getByText('High-risk operations').nextElementSibling?.textContent).toBe('1')
  })

  it('reclassifies a normal server operation when an unsaved task edit becomes high risk', async () => {
    const normalUpdate: Proposal['operations'][number] = {
      id: 'normal-task-update',
      type: 'task.update',
      entity_id: taskId,
      expected_version: 1,
      data: { title: 'Initially normal task edit' },
      rationale: 'The initial title-only edit is normal risk.',
      confidence: 0.9,
      prerequisite_operation_ids: [],
      disposition: 'pending',
      basis: 'source_evidence',
      risk: 'normal',
      default_selected: true,
    }
    const guided = draftProposal(normalUpdate, {
      proposal_contract_version: '2', workflow_mode: 'reconcile_progress', scope_type: 'task', scope_id: taskId, result_kind: 'changes',
    })
    renderView(guided)
    const card = (await screen.findByRole('heading', { name: guided.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    expect(within(card).queryByText('High risk · 1', { exact: true })).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: /Initially normal task edit/ }))
    fireEvent.click(within(card).getByRole('button', { name: 'Edit' }))
    const editor = await screen.findByRole('dialog', { name: 'Edit Task Update' })
    fireEvent.change(within(editor).getByLabelText('Operation data (JSON)'), { target: { value: JSON.stringify({ title: 'Now records an outcome', outcome: 'negative' }) } })
    fireEvent.click(within(editor).getByRole('button', { name: 'Save operation edit' }))
    expect(within(card).getByText('High risk · 1', { exact: true })).not.toBeNull()
    expect(within(card).getByText('1 of 1 selected · 1 high risk', { exact: true })).not.toBeNull()
    expect((within(card).getByRole('button', { name: 'Save draft before applying' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('mirrors canonical high-risk rules for unsaved task creates, structural updates, and waived edges', () => {
    const operation = (type: string, data: Record<string, unknown>): ProposalOperation => ({
      id: `${type}-${JSON.stringify(data)}`,
      type,
      data,
      rationale: 'Classifier parity fixture.',
      confidence: 0.9,
      prerequisite_operation_ids: [],
      disposition: 'pending',
    })
    const highRisk = [
      operation('task.create', { status: 'done' }),
      operation('task.create', { status: 'dropped' }),
      operation('task.create', { outcome: 'negative' }),
      operation('task.create', { completion_summary: 'completed' }),
      operation('task.create', { completion_provenance: 'agent' }),
      operation('task.update', { pipeline_id: pipelineId }),
      operation('task.update', { parent_id: taskId }),
      operation('task.update', { position: 3 }),
      operation('task.update', { child_flow_mode: 'sequential' }),
      operation('task.update', { completion_provenance: 'manual' }),
      operation('edge.create', { disabled: true }),
      operation('edge.create', { waiver_reason: 'Human-approved waiver' }),
    ]
    highRisk.forEach((candidate) => expect(isHighRiskOperation(candidate)).toBe(true))

    expect(isHighRiskOperation(operation('task.create', {
      status: 'planned',
      outcome: 'not_applicable',
      pipeline_id: pipelineId,
      parent_id: taskId,
      position: 1,
      child_flow_mode: 'sequential',
    }))).toBe(false)
    expect(isHighRiskOperation(operation('edge.create', { disabled: false, waiver_reason: '' }))).toBe(false)
  })

  it('separates explicit selections from prerequisite and atomic closure and counts operation types', async () => {
    const prerequisite: Proposal['operations'][number] = {
      id: 'op-prerequisite-pipeline',
      type: 'pipeline.create',
      entity_id: '10101010-1010-4010-8010-101010101010',
      data: { id: '10101010-1010-4010-8010-101010101010', title: 'Prerequisite pipeline', flow_mode: 'freeform', position: 1 },
      prerequisite_operation_ids: [],
      disposition: 'pending',
      default_selected: false,
    }
    const artifact: Proposal['operations'][number] = {
      id: 'op-atomic-artifact',
      type: 'artifact.create',
      entity_id: '20202020-2020-4020-8020-202020202020',
      data: { id: '20202020-2020-4020-8020-202020202020', title: 'Result artifact' },
      atomic_group_id: 'artifact-link-group',
      prerequisite_operation_ids: [],
      disposition: 'pending',
      default_selected: false,
    }
    const link: Proposal['operations'][number] = {
      id: 'op-explicit-link',
      type: 'task_artifact.create',
      entity_id: '30303030-3030-4030-8030-303030303030',
      data: { id: '30303030-3030-4030-8030-303030303030', title: 'Attach result', task_id: taskId, artifact_id: artifact.entity_id, role: 'result' },
      atomic_group_id: 'artifact-link-group',
      prerequisite_operation_ids: [prerequisite.id],
      disposition: 'pending',
      default_selected: false,
    }
    const guided = draftProposal(link, {
      operations: [prerequisite, artifact, link],
      proposal_contract_version: '2',
      workflow_mode: 'link_artifacts',
      scope_type: 'task',
      scope_id: taskId,
      result_kind: 'changes',
    })
    renderView(guided)

    const card = (await screen.findByRole('heading', { name: guided.summary })).closest('article')!
    fireEvent.click(within(card).getByRole('tab', { name: 'Operation audit' }))
    fireEvent.click(within(card).getByRole('checkbox', { name: 'Select Attach result (Task Artifact Create)' }))
    expect(within(card).getByRole('button', { name: 'Apply 3 selected' })).not.toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: 'Apply 3 selected' }))

    const confirmation = await screen.findByRole('dialog', { name: 'Apply selected proposal changes' })
    expect(within(confirmation).getByText('Explicit selections').nextElementSibling?.textContent).toBe('1')
    expect(within(confirmation).getByText('Automatically included').nextElementSibling?.textContent).toContain('2')
    expect(within(confirmation).getByText(/1 prerequisite closure/)).not.toBeNull()
    expect(within(confirmation).getByText(/1 atomic-group closure/)).not.toBeNull()
    expect(within(confirmation).getByText('Selected by operation type').nextElementSibling?.textContent).toBe('Artifact Create: 1 · Pipeline Create: 1 · Task Artifact Create: 1')

    fireEvent.click(within(confirmation).getByRole('button', { name: 'Cancel' }))
    fireEvent.click(within(card).getByRole('checkbox', { name: 'Select Prerequisite pipeline (Pipeline Create)' }))
    expect(within(card).getByRole('button', { name: 'Apply 0 selected' })).not.toBeNull()
  })

  it('renders immutable regeneration lineage separately from graphical revision lineage', async () => {
    const priorId = 'abababab-abab-4bab-8bab-abababababab'
    const regenerated = draftProposal(taskCreateOperation(), {
      proposal_contract_version: '2',
      workflow_mode: 'suggest_next_work',
      scope_type: 'project',
      result_kind: 'changes',
      regenerates_proposal_id: priorId,
    })
    renderView(regenerated)

    const card = (await screen.findByRole('heading', { name: regenerated.summary })).closest('article')!
    expect(within(card).getByText((_content, element) => element?.tagName === 'STRONG' && element.textContent === `Regenerated from ${priorId}.`)).not.toBeNull()
    expect(within(card).getByText(priorId, { selector: 'code' })).not.toBeNull()
    expect(within(card).queryByText('Human-reviewed replacement draft.')).toBeNull()
  })
})

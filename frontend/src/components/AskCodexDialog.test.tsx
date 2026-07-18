/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectSnapshot } from '../types'
import { api } from '../lib/api'
import { AskCodexDialog, modeEligibility, readGuidedIntentDraft, type GuidedRequestSeed } from './AskCodexDialog'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const taskId = '33333333-3333-4333-8333-333333333333'
const childTaskId = '66666666-6666-4666-8666-666666666666'
const rootId = '77777777-7777-4777-8777-777777777777'

const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Guided workflow test',
    root_path: '/tmp/guided-workflow-test',
    color: '#5c6e48',
    archived: false,
    semantic_revision: 4,
    layout_revision: 1,
  },
  scan_policy: {
    preferred_sources: ['PLAN.md'],
    include_globs: ['**/*.md'],
    exclude_globs: ['data/**'],
    max_text_file_size: 2_097_152,
    max_files_per_scan: 500,
    max_total_text_bytes: 10_485_760,
    readable_source_root_ids: [],
    allow_git_metadata: false,
    git_history_limit: 0,
    sensitive_patterns: ['.env*'],
    allow_outside_sources: false,
    follow_symlinks: false,
  },
  artifact_roots: [],
  pipelines: [{
    id: pipelineId,
    project_id: projectId,
    title: 'Experiments',
    flow_mode: 'sequential',
    position: 0,
    archived: false,
    version: 1,
  }],
  tasks: [{
    id: taskId,
    project_id: projectId,
    pipeline_id: pipelineId,
    parent_id: null,
    user_key: 'EXP-1',
    kind: 'task',
    title: 'Run baseline',
    status: 'in_progress',
    outcome: null,
    priority: 'required',
    labels: [],
    position: 0,
    child_flow_mode: 'freeform',
    readiness: 'ready',
    unsatisfied_predecessor_ids: [],
    version: 2,
  }],
  edges: [],
  journals: [],
  artifacts: [],
  task_artifacts: [],
  layouts: [],
  progress: { leaf_total: 1, leaf_done: 0, ready: 1, waiting: 0, blocked: 0, review: 0 },
  planning_profile: {
    task_granularity: 'balanced',
    max_nesting_depth: 3,
    planning_horizon: 'current_milestone',
    inference_policy: 'cautious_gaps',
    max_new_tasks_per_proposal: 30,
    preferred_pipeline_names: [],
    terminology_notes: '',
    additional_instructions: '',
    protected_pipeline_ids: [],
    protected_task_ids: [],
    version: 1,
  },
}

function renderDialog(seed: GuidedRequestSeed = {}, value: ProjectSnapshot = snapshot) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><AskCodexDialog open onClose={() => undefined} snapshot={value} seed={seed} /></QueryClientProvider>)
  return {
    ...view,
    rerenderDialog: (nextSeed: GuidedRequestSeed, nextValue: ProjectSnapshot) => view.rerender(
      <QueryClientProvider client={client}><AskCodexDialog open onClose={() => undefined} snapshot={nextValue} seed={nextSeed} /></QueryClientProvider>,
    ),
  }
}

beforeEach(() => {
  vi.spyOn(api, 'getProposalPage').mockResolvedValue({ proposals: [], next_cursor: null, total: 0, draft_count: 0 })
})

afterEach(() => {
  vi.useRealTimers()
  cleanup()
  vi.restoreAllMocks()
  window.localStorage.clear()
})

describe('AskCodexDialog', () => {
  it('explains why initialization is unavailable once active monitor structure exists', () => {
    expect(modeEligibility(snapshot, 'initialize_structure')).toMatch(/only when the monitor has no active/i)
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current' })
    renderDialog()
    expect((screen.getByRole('radio', { name: /Initialize structure/i }) as HTMLInputElement).disabled).toBe(true)
    expect(screen.getAllByRole('radio')).toHaveLength(6)
  })

  it('creates a task-bound record-update intent and exposes the complete server prompt', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current', label: 'Installed and current' })
    const create = vi.spyOn(api, 'createAgentPrompt').mockResolvedValue({
      intent_id: '44444444-4444-4444-8444-444444444444',
      proposal_request_id: '55555555-5555-4555-8555-555555555555',
      expires_at: '2099-07-17T00:00:00Z',
      workflow_mode: 'record_update',
      scope_type: 'task',
      scope_id: taskId,
      prompt: 'Use $research-monitor and submit exactly one review-only proposal.',
      context_command: `research-monitor agent context --project ${projectId} --intent 44444444-4444-4444-8444-444444444444 --json`,
      disclosure: 'Running this prompt may send permitted project text to OpenAI.',
    })
    renderDialog({ mode: 'record_update', scopeType: 'task', scopeId: taskId })

    expect((screen.getByRole('radio', { name: /Record an update/i }) as HTMLInputElement).checked).toBe(true)
    fireEvent.change(await screen.findByLabelText(/Update to record/i), { target: { value: 'Baseline finished with a negative result.' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Allow this request to propose completion/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Generate prompt' }))

    await waitFor(() => expect(create).toHaveBeenCalledWith(projectId, expect.objectContaining({
      mode: 'record_update',
      scope_type: 'task',
      scope_id: taskId,
      instructions: 'Baseline finished with a negative result.',
      allow_completion: true,
    })))
    expect(await screen.findByDisplayValue(/submit exactly one review-only proposal/i)).not.toBeNull()
    expect(screen.getByText('Bound request')).not.toBeNull()
    expect(screen.getByText(/Running this prompt may send permitted project text/i)).not.toBeNull()
    expect(readGuidedIntentDraft('44444444-4444-4444-8444-444444444444')).toEqual(expect.objectContaining({
      instructions: 'Baseline finished with a negative result.',
      allowCompletion: true,
    }))
  })

  it('blocks a descendant scope inherited from a protected parent task', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current' })
    const child = { ...snapshot.tasks[0], id: childTaskId, parent_id: taskId, user_key: 'EXP-1.1', title: 'Analyze baseline' }
    const protectedSnapshot: ProjectSnapshot = {
      ...snapshot,
      tasks: [...snapshot.tasks, child],
      planning_profile: { ...snapshot.planning_profile!, protected_task_ids: [taskId] },
    }
    renderDialog({ mode: 'record_update', scopeType: 'task', scopeId: childTaskId }, protectedSnapshot)
    fireEvent.change(await screen.findByLabelText(/Update to record/i), { target: { value: 'Analysis started.' } })

    expect(screen.getByText(/outside protected pipelines and task subtrees/i)).not.toBeNull()
    expect((screen.getByRole('button', { name: 'Generate prompt' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('allows updates on completed tasks while keeping terminal expansion unavailable', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current' })
    const completed: ProjectSnapshot = { ...snapshot, tasks: [{ ...snapshot.tasks[0], status: 'done' }] }
    expect(modeEligibility(completed, 'record_update')).toBeNull()
    expect(modeEligibility(completed, 'expand_task')).toMatch(/active, unprotected task/i)
    renderDialog({ mode: 'record_update', scopeType: 'task', scopeId: taskId }, completed)
    fireEvent.change(await screen.findByLabelText(/Update to record/i), { target: { value: 'Add a post-completion interpretation.' } })
    expect((screen.getByRole('button', { name: 'Generate prompt' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('uses the approved project-root UUID and exact backend artifact-locator fields', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current' })
    const create = vi.spyOn(api, 'createAgentPrompt').mockResolvedValue({
      intent_id: '88888888-8888-4888-8888-888888888888',
      expires_at: '2099-07-17T00:00:00Z',
      workflow_mode: 'link_artifacts',
      scope_type: 'task',
      scope_id: taskId,
      prompt: 'Submit an artifact-link proposal.',
    })
    const rootedSnapshot: ProjectSnapshot = {
      ...snapshot,
      artifact_roots: [{ id: rootId, project_id: projectId, name: 'Project root', canonical_path: snapshot.project.root_path, is_project_root: true, version: 1 }],
    }
    renderDialog({ mode: 'link_artifacts', scopeType: 'task', scopeId: taskId }, rootedSnapshot)
    fireEvent.click(await screen.findByRole('button', { name: /Add locator/i }))
    fireEvent.change(screen.getByLabelText('Artifact locator'), { target: { value: 'results/summary.json' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate prompt' }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    const locator = create.mock.calls[0][1].artifact_locators?.[0]
    expect(locator).toEqual(expect.objectContaining({ kind: 'local', locator: 'results/summary.json', artifact_root_id: rootId }))
    expect(locator).not.toHaveProperty('role')
  })

  it('clears a generated prompt and project-specific form state when the project changes', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current' })
    vi.spyOn(api, 'createAgentPrompt').mockResolvedValue({
      intent_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      project_id: projectId,
      issued_semantic_revision: 4,
      planning_profile_version: 1,
      expires_at: '2099-07-17T00:00:00Z',
      workflow_mode: 'record_update',
      scope_type: 'task',
      scope_id: taskId,
      prompt: 'Project A private generated prompt.',
    })
    const seed: GuidedRequestSeed = { mode: 'record_update', scopeType: 'task', scopeId: taskId }
    const view = renderDialog(seed)
    fireEvent.change(await screen.findByLabelText(/Update to record/i), { target: { value: 'Project A private update.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate prompt' }))
    expect(await screen.findByDisplayValue('Project A private generated prompt.')).not.toBeNull()

    const otherProjectId = '99999999-9999-4999-8999-999999999999'
    const otherProject: ProjectSnapshot = {
      ...snapshot,
      project: { ...snapshot.project, id: otherProjectId, name: 'Project B', root_path: '/tmp/project-b', semantic_revision: 1 },
      pipelines: snapshot.pipelines.map((pipeline) => ({ ...pipeline, project_id: otherProjectId })),
      tasks: snapshot.tasks.map((task) => ({ ...task, project_id: otherProjectId })),
    }
    view.rerenderDialog(seed, otherProject)

    await waitFor(() => expect((screen.getByLabelText('Complete generated Codex prompt') as HTMLTextAreaElement).value).toBe(''))
    expect((screen.getByRole('radio', { name: /Reconcile progress/i }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/Instructions for Codex/i) as HTMLTextAreaElement).value).toBe('')
    expect((screen.getByRole('button', { name: 'Copy prompt' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('invalidates prompts across semantic and planning-profile revisions while preserving unsaved instructions', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'current' })
    vi.spyOn(api, 'createAgentPrompt').mockResolvedValue({
      intent_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      project_id: projectId,
      expires_at: '2099-07-17T00:00:00Z',
      workflow_mode: 'record_update',
      scope_type: 'task',
      scope_id: taskId,
      prompt: 'Revision-bound prompt.',
    })
    const seed: GuidedRequestSeed = { mode: 'record_update', scopeType: 'task', scopeId: taskId }
    const view = renderDialog(seed)
    const note = 'Preserve this unsaved research note.'
    fireEvent.change(await screen.findByLabelText(/Update to record/i), { target: { value: note } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate prompt' }))
    expect(await screen.findByDisplayValue('Revision-bound prompt.')).not.toBeNull()

    const semanticUpdate: ProjectSnapshot = { ...snapshot, project: { ...snapshot.project, semantic_revision: 5 } }
    view.rerenderDialog(seed, semanticUpdate)
    await waitFor(() => expect((screen.getByLabelText('Complete generated Codex prompt') as HTMLTextAreaElement).value).toBe(''))
    expect((screen.getByLabelText(/Update to record/i) as HTMLTextAreaElement).value).toBe(note)

    fireEvent.click(screen.getByRole('button', { name: 'Generate prompt' }))
    expect(await screen.findByDisplayValue('Revision-bound prompt.')).not.toBeNull()
    view.rerenderDialog(seed, {
      ...semanticUpdate,
      planning_profile: { ...semanticUpdate.planning_profile!, version: 2 },
    })
    await waitFor(() => expect((screen.getByLabelText('Complete generated Codex prompt') as HTMLTextAreaElement).value).toBe(''))
    expect((screen.getByLabelText(/Update to record/i) as HTMLTextAreaElement).value).toBe(note)
  })

  it.each(['missing', 'modified', 'outdated', 'blocked'] as const)('warns for an optional %s skill without disabling prompt generation', async (status) => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({
      status,
      optional: true,
      ...(status === 'blocked' ? {} : { setup_command: status === 'missing' ? 'research-monitor skill install' : 'research-monitor skill update' }),
      ...(status === 'blocked' ? { blocking_reason: 'Choose a CODEX_HOME outside enrolled roots.' } : {}),
    })
    renderDialog()

    expect(await screen.findByText(new RegExp(`Optional companion skill: ${status}`, 'i'))).not.toBeNull()
    expect(screen.getByText(/Manual monitoring is unaffected/i)).not.toBeNull()
    expect((screen.getByRole('button', { name: 'Generate prompt' }) as HTMLButtonElement).disabled).toBe(false)
    if (status === 'blocked') expect(screen.getByText('CODEX_HOME=/safe/codex-home research-monitor skill install')).not.toBeNull()
  })

  it('expires a generated request on its server deadline and requires a fresh request', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2035-01-01T00:00:00Z'))
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'missing', optional: true, setup_command: 'research-monitor skill install' })
    vi.spyOn(api, 'createAgentPrompt').mockResolvedValue({
      intent_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      expires_at: '2035-01-01T00:00:01Z',
      workflow_mode: 'reconcile_progress',
      scope_type: 'project',
      scope_id: null,
      prompt: 'Short-lived generated prompt.',
    })
    renderDialog()
    fireEvent.click(screen.getByRole('button', { name: 'Generate prompt' }))
    await act(async () => { await Promise.resolve(); await Promise.resolve() })
    expect(screen.getByText('Bound request')).not.toBeNull()

    await act(async () => { await vi.advanceTimersByTimeAsync(1_020) })
    expect(screen.queryByText('Bound request')).toBeNull()
    expect(screen.getByText('Expired', { exact: true })).not.toBeNull()
    expect((screen.getByRole('button', { name: 'Copy prompt' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Select all' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getAllByRole('button', { name: 'Create fresh request' }).length).toBeGreaterThan(0)
  })
})

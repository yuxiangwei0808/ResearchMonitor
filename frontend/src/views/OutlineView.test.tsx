/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { ProjectSnapshot } from '../types'
import { formatCalendarDate, formatDate } from '../lib/format'
import { OutlineView } from './OutlineView'

const projectId = '11111111-1111-4111-8111-111111111111'
const taskId = '33333333-3333-4333-8333-333333333333'

const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Evidence test',
    root_path: '/tmp/evidence-test',
    color: '#5c6e48',
    archived: false,
    semantic_revision: 1,
    layout_revision: 1,
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
  artifact_roots: [{
    id: '44444444-4444-4444-8444-444444444444',
    project_id: projectId,
    name: 'Project root',
    canonical_path: '/tmp/evidence-test',
    is_project_root: true,
  }],
  pipelines: [{
    id: '22222222-2222-4222-8222-222222222222',
    project_id: projectId,
    title: 'Pipeline',
    flow_mode: 'freeform',
    position: 0,
    archived: false,
    version: 1,
  }],
  tasks: [{
    id: taskId,
    project_id: projectId,
    pipeline_id: '22222222-2222-4222-8222-222222222222',
    title: 'Completed task',
    kind: 'task',
    status: 'done',
    outcome: 'successful',
    priority: 'required',
    labels: [],
    position: 0,
    completion_summary: 'The expected result was reproduced.',
    completion_actor: 'Research Monitor UI',
    completion_source: 'manual_confirmation',
    completion_provenance: 'manual',
    target_date: '2026-07-30',
    created_at: '2026-07-13T08:30:00Z',
    updated_at: '2026-07-14T09:45:00Z',
    completed_at: '2026-07-15T00:00:00Z',
    child_flow_mode: 'freeform',
    readiness: 'ready',
    unsatisfied_predecessor_ids: [],
    version: 1,
  }],
  edges: [],
  journals: [],
  artifacts: [{
    id: '55555555-5555-4555-8555-555555555555',
    project_id: projectId,
    locator: 'https://wandb.ai/example/run',
    kind: 'url',
    provider: 'W&B',
    label: 'Training run',
    version: 1,
  }, {
    id: '66666666-6666-4666-8666-666666666666',
    project_id: projectId,
    artifact_root_id: '44444444-4444-4444-8444-444444444444',
    locator: 'results/final.json',
    kind: 'local',
    label: 'Final result',
    version: 1,
  }],
  task_artifacts: [{
    id: '77777777-7777-4777-8777-777777777777',
    task_id: taskId,
    artifact_id: '55555555-5555-4555-8555-555555555555',
    role: 'external_run',
  }, {
    id: '88888888-8888-4888-8888-888888888888',
    task_id: taskId,
    artifact_id: '66666666-6666-4666-8666-666666666666',
    role: 'evidence',
  }],
  layouts: [],
  progress: { leaf_total: 1, leaf_done: 1, ready: 0, waiting: 0, blocked: 0, review: 0 },
}

afterEach(cleanup)

describe('OutlineView completion evidence', () => {
  it('shows completion provenance and links external and local evidence safely', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={[`/projects/${projectId}/outline?view=done`]}>
        <QueryClientProvider client={client}>
          <OutlineView snapshot={snapshot} />
        </QueryClientProvider>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Completed task' }))

    const panel = screen.getByRole('region', { name: 'Completion record and linked evidence' })
    expect(within(panel).getByText('Research Monitor UI')).not.toBeNull()
    expect(within(panel).getByText(/Manual confirmation/i)).not.toBeNull()
    expect(within(panel).getByText('Manual entry')).not.toBeNull()

    const external = within(panel).getByRole('link', { name: /Training run/ })
    expect(external.getAttribute('href')).toBe('https://wandb.ai/example/run')
    expect(external.getAttribute('target')).toBe('_blank')
    expect(external.getAttribute('rel')).toBe('noopener noreferrer')

    const local = within(panel).getByRole('link', { name: /Final result/ })
    expect(local.getAttribute('href')).toBe(`/projects/${projectId}/artifacts#artifact-66666666-6666-4666-8666-666666666666`)
  })

  it('shows the editable target date and read-only lifecycle timestamps', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter initialEntries={[`/projects/${projectId}/outline?view=done`]}><QueryClientProvider client={client}><OutlineView snapshot={snapshot} /></QueryClientProvider></MemoryRouter>)

    const target = screen.getByText(`Target ${formatCalendarDate('2026-07-30')}`)
    expect(target.tagName).toBe('TIME')
    expect(target.getAttribute('datetime')).toBe('2026-07-30')

    fireEvent.click(screen.getByRole('button', { name: 'Completed task' }))
    const timestamps = screen.getByLabelText('Task timestamps')
    expect(within(timestamps).getByText('Created')).not.toBeNull()
    expect(within(timestamps).getByText('Last updated')).not.toBeNull()
    expect(within(timestamps).getByText('Completed')).not.toBeNull()
    expect(within(timestamps).getByText(formatDate('2026-07-13T08:30:00Z', true))).not.toBeNull()
    expect(within(timestamps).getByText(formatDate('2026-07-14T09:45:00Z', true))).not.toBeNull()
    expect(within(timestamps).getByText(formatDate('2026-07-15T00:00:00Z', true))).not.toBeNull()
  })

  it('opens the editor from the visible task actions menu', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter initialEntries={[`/projects/${projectId}/outline?view=done`]}><QueryClientProvider client={client}><OutlineView snapshot={snapshot} /></QueryClientProvider></MemoryRouter>)

    fireEvent.click(screen.getByRole('button', { name: 'Actions for Completed task' }))
    const menu = await screen.findByRole('menu', { name: 'Actions for Completed task' })
    expect(within(menu).getByRole('menuitem', { name: 'Add subtask' })).not.toBeNull()
    expect(within(menu).getByRole('menuitem', { name: 'Delete task' })).not.toBeNull()
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Edit task' }))

    expect(await screen.findByRole('dialog', { name: 'Task details' })).not.toBeNull()
  })

  it('offers the same task actions on right-click', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter initialEntries={[`/projects/${projectId}/outline?view=done`]}><QueryClientProvider client={client}><OutlineView snapshot={snapshot} /></QueryClientProvider></MemoryRouter>)

    const row = screen.getByRole('button', { name: 'Completed task' }).closest('.task-row')
    expect(row).not.toBeNull()
    fireEvent.contextMenu(row!, { clientX: 120, clientY: 80 })
    const menu = await screen.findByRole('menu', { name: 'Actions for Completed task' })
    expect(within(menu).getByRole('menuitem', { name: 'Edit task' })).not.toBeNull()
    expect(within(menu).getByRole('menuitem', { name: 'Delete task' })).not.toBeNull()
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Add subtask' }))

    expect(await screen.findByRole('dialog', { name: 'New subtask' })).not.toBeNull()
  })

  it('preserves a dirty task draft when a refreshed snapshot changes the same task', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = (value: ProjectSnapshot) => <MemoryRouter initialEntries={[`/projects/${projectId}/outline?view=done`]}><QueryClientProvider client={client}><OutlineView snapshot={value} /></QueryClientProvider></MemoryRouter>
    const rendered = render(view(snapshot))
    fireEvent.click(screen.getByRole('button', { name: 'Completed task' }))
    const title = screen.getByLabelText('Task title') as HTMLInputElement
    fireEvent.change(title, { target: { value: 'My unsaved title' } })

    rendered.rerender(view({ ...snapshot, project: { ...snapshot.project, semantic_revision: 2 }, tasks: [{ ...snapshot.tasks[0], title: 'Changed elsewhere', version: 2 }] }))

    expect(title.value).toBe('My unsaved title')
    expect(await screen.findByText(/This task changed in another UI or CLI action/)).not.toBeNull()
  })

  it('shows true non-dropped descendant-leaf progress and named collapse controls', () => {
    const parent = { ...snapshot.tasks[0], id: '90000000-0000-4000-8000-000000000001', title: 'Parent program', status: 'planned' as const, outcome: 'not_applicable' as const, parent_id: null, position: 0 }
    const branch = { ...parent, id: '90000000-0000-4000-8000-000000000002', title: 'Analysis branch', parent_id: parent.id, position: 0 }
    const nestedDone = { ...parent, id: '90000000-0000-4000-8000-000000000003', title: 'Nested done', status: 'done' as const, parent_id: branch.id, position: 0 }
    const nestedDropped = { ...parent, id: '90000000-0000-4000-8000-000000000004', title: 'Nested dropped', status: 'dropped' as const, parent_id: branch.id, position: 1 }
    const directDone = { ...parent, id: '90000000-0000-4000-8000-000000000005', title: 'Direct done', status: 'done' as const, parent_id: parent.id, position: 1 }
    const value = { ...snapshot, tasks: [parent, branch, nestedDone, nestedDropped, directDone], artifacts: [], task_artifacts: [] }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={client}><OutlineView snapshot={value} /></QueryClientProvider></MemoryRouter>)

    expect(screen.getByText('2/2 descendant leaves done')).not.toBeNull()
    const collapse = screen.getByRole('button', { name: 'Collapse Parent program' })
    expect(collapse.getAttribute('aria-expanded')).toBe('true')
  })
})

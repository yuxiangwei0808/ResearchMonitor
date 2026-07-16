/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectSnapshot, Task } from '../types'
import { api } from '../lib/api'
import { TaskEditor } from './OutlineView'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'

function task(id: string, title: string, parentId: string | null = null, userKey?: string): Task {
  return {
    id,
    project_id: projectId,
    pipeline_id: pipelineId,
    parent_id: parentId,
    title,
    user_key: userKey,
    kind: 'task',
    status: 'planned',
    priority: 'required',
    labels: [],
    position: 0,
    child_flow_mode: 'freeform',
    readiness: 'ready',
    unsatisfied_predecessor_ids: [],
    version: 1,
  }
}

const editedTask = task('33333333-3333-4333-8333-333333333333', 'Edited task')
const root = task('44444444-4444-4444-8444-444444444444', 'Data preparation', null, 'ROOT')
const nested = task('55555555-5555-4555-8555-555555555555', 'Validate manifest', root.id, 'QC')
const snapshot: ProjectSnapshot = {
  project: { id: projectId, name: 'Task editor test', root_path: '/tmp/task-editor', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0 },
  scan_policy: { preferred_sources: [], include_globs: [], exclude_globs: [], max_text_file_size: 2_097_152, allow_git_metadata: false, git_history_limit: 0, sensitive_patterns: [], allow_outside_sources: false, follow_symlinks: false },
  artifact_roots: [],
  pipelines: [{ id: pipelineId, project_id: projectId, title: 'Evaluation', flow_mode: 'freeform', position: 0, archived: false, version: 1 }],
  tasks: [editedTask, root, nested],
  edges: [], journals: [], artifacts: [], task_artifacts: [], layouts: [],
  progress: { leaf_total: 2, leaf_done: 0, ready: 2, waiting: 0, blocked: 0, review: 0 },
}

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Harness() {
    const [open, setOpen] = useState(true)
    return <TaskEditor snapshot={snapshot} state={{ open, task: editedTask }} onClose={() => setOpen(false)} />
  }
  return render(<MemoryRouter><QueryClientProvider client={client}><Harness /></QueryClientProvider></MemoryRouter>)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('TaskEditor close safety and task labels', () => {
  it('guards Cancel, Escape, close, and backdrop dismissal when the draft is dirty', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    renderEditor()
    const dialog = screen.getByRole('dialog', { name: 'Task details' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Unsaved title' } })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(confirm).toHaveBeenLastCalledWith('Discard your unsaved changes?')
    expect(screen.getByRole('dialog', { name: 'Task details' })).not.toBeNull()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: 'Task details' })).not.toBeNull()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }))
    expect(screen.getByRole('dialog', { name: 'Task details' })).not.toBeNull()

    const beforeBackdrop = confirm.mock.calls.length
    const backdrop = document.querySelector('.dialog-backdrop')
    expect(backdrop).not.toBeNull()
    fireEvent.pointerDown(backdrop!)
    fireEvent.pointerUp(backdrop!)
    fireEvent.click(backdrop!)
    expect(confirm.mock.calls.length).toBeGreaterThan(beforeBackdrop)
    expect(screen.getByRole('dialog', { name: 'Task details' })).not.toBeNull()

    confirm.mockReturnValue(true)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Task details' })).toBeNull()
  })

  it('closes after a successful save without asking to discard the dirty draft', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: '66666666-6666-4666-8666-666666666666',
      project_id: projectId,
      semantic_revision: 2,
      layout_revision: 0,
      results: [],
    })
    renderEditor()
    const dialog = screen.getByRole('dialog', { name: 'Task details' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Saved title' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Task details' })).toBeNull())
    expect(confirm).not.toHaveBeenCalled()
  })

  it('closes after a successful delete without a second discard prompt', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: '77777777-7777-4777-8777-777777777777',
      project_id: projectId,
      semantic_revision: 2,
      layout_revision: 0,
      results: [],
    })
    renderEditor()
    const dialog = screen.getByRole('dialog', { name: 'Task details' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Dirty before delete' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete task' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Task details' })).toBeNull())
    expect(confirm).toHaveBeenCalledTimes(1)
    expect(confirm).toHaveBeenCalledWith('Move “Edited task” and all subtasks to trash?')
  })

  it('disambiguates parent choices with pipeline and complete ancestry', () => {
    renderEditor()
    const parent = screen.getByRole('combobox', { name: /Parent task/ }) as HTMLSelectElement
    expect([...parent.options].map((option) => option.text)).toContain('Evaluation › ROOT · Data preparation › QC · Validate manifest')
  })
})

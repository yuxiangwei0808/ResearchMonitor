/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectSnapshot, ProposalOperation, Task } from '../../types'
import { ProposedOutline } from './ProposedOutline'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const taskId = '33333333-3333-4333-8333-333333333333'
const task: Task = {
  id: taskId,
  project_id: projectId,
  pipeline_id: pipelineId,
  parent_id: null,
  title: 'Proposed task',
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
const snapshot: ProjectSnapshot = {
  project: { id: projectId, name: 'Proposal close test', root_path: '/tmp/proposal-close', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0 },
  scan_policy: { preferred_sources: [], include_globs: [], exclude_globs: [], max_text_file_size: 2_097_152, allow_git_metadata: false, git_history_limit: 0, sensitive_patterns: [], allow_outside_sources: false, follow_symlinks: false },
  artifact_roots: [],
  pipelines: [{ id: pipelineId, project_id: projectId, title: 'Evaluation', flow_mode: 'freeform', position: 0, archived: false, version: 1 }],
  tasks: [task], edges: [], journals: [], artifacts: [], task_artifacts: [], layouts: [],
  progress: { leaf_total: 1, leaf_done: 0, ready: 1, waiting: 0, blocked: 0, review: 0 },
}
const operations: ProposalOperation[] = [{
  id: '44444444-4444-4444-8444-444444444444',
  type: 'task.update',
  entity_id: taskId,
  expected_version: 1,
  data: { priority: 'recommended' },
  rationale: 'Review the task.',
  confidence: 0.8,
  evidence: [],
  source_references: [],
  prerequisite_operation_ids: [],
}]

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('proposed task editor close safety', () => {
  it('guards Cancel, Escape, close, and backdrop dismissal when the staged task form is dirty', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<ProposedOutline snapshot={snapshot} operations={operations} onChange={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit task Proposed task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Unsaved staged title' } })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(confirm).toHaveBeenLastCalledWith('Discard your unsaved changes?')
    expect(screen.getByRole('dialog', { name: 'Edit proposed task' })).not.toBeNull()

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('dialog', { name: 'Edit proposed task' })).not.toBeNull()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Close dialog' }))
    expect(screen.getByRole('dialog', { name: 'Edit proposed task' })).not.toBeNull()

    const beforeBackdrop = confirm.mock.calls.length
    const backdrop = document.querySelector('.dialog-backdrop')
    expect(backdrop).not.toBeNull()
    fireEvent.pointerDown(backdrop!)
    fireEvent.pointerUp(backdrop!)
    fireEvent.click(backdrop!)
    expect(confirm.mock.calls.length).toBeGreaterThan(beforeBackdrop)
    expect(screen.getByRole('dialog', { name: 'Edit proposed task' })).not.toBeNull()

    confirm.mockReturnValue(true)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Edit proposed task' })).toBeNull()
  })

  it('closes after a successful staged save without a discard prompt', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={snapshot} operations={operations} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit task Proposed task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Task title'), { target: { value: 'Saved staged title' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('dialog', { name: 'Edit proposed task' })).toBeNull()
    expect(confirm).not.toHaveBeenCalled()
  })
})

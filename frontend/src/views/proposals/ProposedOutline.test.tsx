/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ProjectSnapshot, ProposalOperation, Task } from '../../types'
import { ProposedOutline } from './ProposedOutline'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const rootId = '33333333-3333-4333-8333-333333333333'
const childId = '44444444-4444-4444-8444-444444444444'

function task(id: string, title: string, position: number, parentId: string | null = null): Task {
  return {
    id,
    project_id: projectId,
    pipeline_id: pipelineId,
    parent_id: parentId,
    title,
    kind: 'task',
    status: 'planned',
    priority: 'required',
    labels: [],
    position,
    child_flow_mode: 'freeform',
    readiness: 'ready',
    unsatisfied_predecessor_ids: [],
    version: 1,
  }
}

const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Outline semantics',
    root_path: '/tmp/outline-semantics',
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
    title: 'Existing pipeline',
    flow_mode: 'freeform',
    position: 0,
    archived: false,
    version: 2,
  }],
  tasks: [
    task(rootId, 'Parent task', 0),
    task(childId, 'Child task', 0, rootId),
  ],
  edges: [],
  journals: [],
  artifacts: [],
  task_artifacts: [],
  layouts: [],
  progress: { leaf_total: 1, leaf_done: 0, ready: 1, waiting: 0, blocked: 0, review: 0 },
}

function operation(id: string, type: string, data: Record<string, unknown>, entityId?: string): ProposalOperation {
  return {
    id,
    type,
    entity_id: entityId,
    expected_version: entityId ? 1 : undefined,
    data,
    rationale: 'Test proposal.',
    confidence: 0.8,
    evidence: [],
    source_references: [],
    prerequisite_operation_ids: [],
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ProposedOutline accessibility and pipeline controls', () => {
  it('uses nested list semantics and exposes expansion state on named buttons', () => {
    const operations = [operation('op-child', 'task.update', { priority: 'optional' }, childId)]
    const { container } = render(<ProposedOutline snapshot={snapshot} operations={operations} onChange={vi.fn()} />)

    const outline = screen.getByLabelText('Affected proposal pipelines and tasks')
    expect(outline.tagName).toBe('UL')
    expect(outline.firstElementChild?.tagName).toBe('LI')
    expect(container.querySelector('[role="tree"]')).toBeNull()
    expect(container.querySelector('[role="treeitem"]')).toBeNull()

    const pipelineToggle = screen.getByRole('button', { name: 'Collapse Existing pipeline' })
    expect(pipelineToggle.getAttribute('aria-expanded')).toBe('true')
    const pipelineTasks = document.getElementById(pipelineToggle.getAttribute('aria-controls')!)
    expect(pipelineTasks?.tagName).toBe('UL')

    const taskToggle = screen.getByRole('button', { name: 'Collapse Parent task' })
    expect(taskToggle.getAttribute('aria-expanded')).toBe('true')
    const childList = document.getElementById(taskToggle.getAttribute('aria-controls')!)
    expect(childList?.tagName).toBe('UL')
    expect(childList?.firstElementChild?.tagName).toBe('LI')

    fireEvent.click(taskToggle)
    expect(screen.getByRole('button', { name: 'Expand Parent task' }).getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('button', { name: 'Edit task Child task' })).toBeNull()
  })

  it('removes a new pipeline with its staged subtree from the graphical control', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const proposedPipeline = '55555555-5555-4555-8555-555555555555'
    const proposedTask = '66666666-6666-4666-8666-666666666666'
    const operations = [
      operation('op-pipeline', 'pipeline.create', { id: proposedPipeline, title: 'Proposed pipeline', position: 1 }),
      {
        ...operation('op-task', 'task.create', {
          id: proposedTask,
          pipeline_id: proposedPipeline,
          parent_id: null,
          title: 'Proposed task',
          position: 0,
        }),
        prerequisite_operation_ids: ['op-pipeline'],
      },
    ]
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={snapshot} operations={operations} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Remove proposed pipeline Proposed pipeline' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual([])
  })

  it('reverts an existing pipeline update while preserving independent task work', () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const taskUpdate = operation('op-child', 'task.update', { priority: 'optional' }, childId)
    const operations = [
      operation('op-pipeline', 'pipeline.update', { title: 'Reviewed pipeline' }, pipelineId),
      taskUpdate,
    ]
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={snapshot} operations={operations} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Revert proposed changes for pipeline Reviewed pipeline' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual([taskUpdate])
  })

  it('shows a clear guard instead of generating a stale descendant edit after a subtree pipeline move', () => {
    const destinationPipelineId = '77777777-7777-4777-8777-777777777777'
    const movedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        ...snapshot.pipelines,
        {
          ...snapshot.pipelines[0],
          id: destinationPipelineId,
          title: 'Destination pipeline',
          position: 1,
          version: 1,
        },
      ],
    }
    const operations = [operation('op-parent-move', 'task.update', {
      pipeline_id: destinationPipelineId,
      parent_id: null,
    }, rootId)]
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={movedSnapshot} operations={operations} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Edit task Child task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Priority'), { target: { value: 'optional' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))

    expect(within(dialog).getByText(/Apply, revert, or reject that subtree move before editing its existing descendants/)).not.toBeNull()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('shows a clear guard when a cross-pipeline parent move would precede a descendant edit', () => {
    const destinationPipelineId = '77777777-7777-4777-8777-777777777777'
    const movedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        ...snapshot.pipelines,
        {
          ...snapshot.pipelines[0],
          id: destinationPipelineId,
          title: 'Destination pipeline',
          position: 1,
          version: 1,
        },
      ],
    }
    const operations = [
      operation('op-parent-move', 'task.update', {
        pipeline_id: destinationPipelineId,
        parent_id: null,
      }, rootId),
      operation('op-child-edit', 'task.update', { priority: 'optional' }, childId),
    ]
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={movedSnapshot} operations={operations} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Edit task Parent task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(within(dialog).getByLabelText('Priority'), { target: { value: 'optional' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save task changes' }))

    expect(within(dialog).getByText(/Apply, revert, or reject the descendant change before staging this subtree move/)).not.toBeNull()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('reorders a sparse affected pipeline one place in the full canonical project order', () => {
    const pipelineOne = '88888888-8888-4888-8888-888888888881'
    const pipelineTwo = '88888888-8888-4888-8888-888888888882'
    const pipelineThree = '88888888-8888-4888-8888-888888888883'
    const orderedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        snapshot.pipelines[0],
        { ...snapshot.pipelines[0], id: pipelineOne, title: 'Pipeline one', position: 1 },
        { ...snapshot.pipelines[0], id: pipelineTwo, title: 'Pipeline two', position: 2 },
        { ...snapshot.pipelines[0], id: pipelineThree, title: 'Pipeline three', position: 3 },
      ],
    }
    const operations = [
      operation('op-first-visible', 'pipeline.update', { title: 'First visible' }, pipelineId),
      operation('op-last-visible', 'pipeline.update', { title: 'Last visible' }, pipelineThree),
    ]
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={orderedSnapshot} operations={operations} onChange={onChange} />)

    expect(screen.getByText(/project order 4 of 4/)).not.toBeNull()
    const moveUp = screen.getByRole('button', { name: 'Move pipeline Last visible up' }) as HTMLButtonElement
    expect(moveUp.disabled).toBe(false)
    fireEvent.click(moveUp)

    const changed = onChange.mock.calls[0][0] as ProposalOperation[]
    expect(changed.find((item) => item.id === 'op-last-visible')?.data.position).toBe(1.5)
  })

  it('appends a graphical pipeline after hidden canonical pipelines', () => {
    const pipelineOne = '99999999-9999-4999-8999-999999999991'
    const pipelineTwo = '99999999-9999-4999-8999-999999999992'
    const orderedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        snapshot.pipelines[0],
        { ...snapshot.pipelines[0], id: pipelineOne, title: 'Pipeline one', position: 1 },
        { ...snapshot.pipelines[0], id: pipelineTwo, title: 'Pipeline two', position: 2 },
      ],
    }
    const operations = [operation('op-artifact', 'artifact.create', {
      id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      kind: 'url',
      locator: 'https://example.test/run',
    })]
    const onChange = vi.fn()
    render(<ProposedOutline snapshot={orderedSnapshot} operations={operations} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add pipeline' }))
    const dialog = screen.getByRole('dialog', { name: 'Add proposed pipeline' })
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Appended pipeline' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add pipeline' }))

    const changed = onChange.mock.calls[0][0] as ProposalOperation[]
    const created = changed.find((item) => item.type === 'pipeline.create')
    expect(created?.data).toMatchObject({ title: 'Appended pipeline', position: 3 })
  })

  it('guards Blocked and Done edits and preserves a failed research outcome', () => {
    const onChange = vi.fn()
    const operations = [operation('op-parent', 'task.update', { title: 'Reviewed parent task' }, rootId)]
    render(<ProposedOutline snapshot={snapshot} operations={operations} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Edit task Reviewed parent task' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit proposed task' })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'blocked' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save task changes' }))
    expect(screen.getByText('Blocked tasks require a blocker explanation.')).not.toBeNull()
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'done' } })
    fireEvent.change(screen.getByLabelText('Research outcome'), { target: { value: 'failed' } })
    fireEvent.change(screen.getByLabelText('Completion summary'), { target: { value: 'Completed the analysis; the approach did not work.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save task changes' }))
    expect(screen.getByText('Record why this parent can be done while descendants remain incomplete.')).not.toBeNull()
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.change(screen.getByLabelText('Completion override rationale'), { target: { value: 'The failed approach is closed; the child follow-up remains separately planned.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save task changes' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual([
      expect.objectContaining({
        id: 'op-parent',
        type: 'task.update',
        data: expect.objectContaining({
          status: 'done',
          outcome: 'failed',
          completion_summary: 'Completed the analysis; the approach did not work.',
          completion_override_reason: 'The failed approach is closed; the child follow-up remains separately planned.',
        }),
      }),
    ])
    expect(dialog.isConnected).toBe(false)
  })

  it('bounds a 2,000-task pipeline to affected and adjacent rows and selects only visible tasks', () => {
    const tasks = Array.from({ length: 2_000 }, (_, index) => task(`large-task-${index}`, `Large task ${index}`, index))
    const largeSnapshot: ProjectSnapshot = {
      ...snapshot,
      tasks,
      progress: { leaf_total: tasks.length, leaf_done: 0, ready: tasks.length, waiting: 0, blocked: 0, review: 0 },
    }
    const operations = [operation('op-large-middle', 'task.update', { priority: 'optional' }, 'large-task-1000')]
    const originalOperations = JSON.parse(JSON.stringify(operations))
    const onChange = vi.fn()

    const { container } = render(<ProposedOutline snapshot={largeSnapshot} operations={operations} onChange={onChange} />)

    expect(container.querySelectorAll('.proposed-task')).toHaveLength(3)
    expect(screen.getByText('1997 active tasks hidden from focused context.')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Show full pipeline context' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Edit task Large task 999' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Edit task Large task 1000' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Edit task Large task 1001' })).not.toBeNull()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all visible tasks' }))

    expect(screen.getByText('3 of 3 visible tasks selected for batch editing')).not.toBeNull()
    const taskCheckboxes = screen.getAllByRole('checkbox', { name: /for batch editing/ }) as HTMLInputElement[]
    expect(taskCheckboxes).toHaveLength(3)
    expect(taskCheckboxes.every((checkbox) => checkbox.checked)).toBe(true)
    expect(onChange).not.toHaveBeenCalled()
    expect(operations).toEqual(originalOperations)
  })

  it('caps a wide affected parent at 20 context children until full context is requested', () => {
    const parent = task('wide-parent', 'Wide parent', 0)
    const children = Array.from({ length: 50 }, (_, index) => task(`wide-child-${index}`, `Wide child ${index}`, index, parent.id))
    const wideSnapshot: ProjectSnapshot = {
      ...snapshot,
      tasks: [parent, ...children],
      progress: { leaf_total: children.length, leaf_done: 0, ready: children.length, waiting: 0, blocked: 0, review: 0 },
    }
    const operations = [operation('op-wide-parent', 'task.update', { priority: 'recommended' }, parent.id)]

    const { container } = render(<ProposedOutline snapshot={wideSnapshot} operations={operations} onChange={vi.fn()} />)

    expect(container.querySelectorAll('.proposed-task')).toHaveLength(21)
    expect(screen.getByText('30 active tasks hidden from focused context.')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Edit task Wide child 19' })).not.toBeNull()
    expect(screen.queryByRole('button', { name: 'Edit task Wide child 20' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Show full pipeline context' }))

    expect(container.querySelectorAll('.proposed-task')).toHaveLength(51)
    expect(screen.getByRole('button', { name: 'Edit task Wide child 49' })).not.toBeNull()
    expect(screen.getByText('All 51 active tasks shown.')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Show focused context' })).not.toBeNull()
  })
})

import { describe, expect, it } from 'vitest'

import type { ProjectSnapshot, ProposalOperation, Task } from '../../types'
import {
  addStagedTask,
  buildProposalFocusProjection,
  buildProjectedPipelineOrder,
  buildProposalStage,
  moveStagedPipeline,
  moveStagedTask,
  nextStagedPipelinePosition,
  operationsChanged,
  removeStagedPipeline,
  removeStagedTask,
  splitStagedTask,
  updateStagedTask,
} from './staging'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const rootId = '33333333-3333-4333-8333-333333333333'
const childId = '44444444-4444-4444-8444-444444444444'
const siblingId = '55555555-5555-4555-8555-555555555555'

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
    version: 3,
  }
}

const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Staging test',
    root_path: '/tmp/staging-test',
    color: '#4f46e5',
    archived: false,
    semantic_revision: 4,
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
    task(rootId, 'Existing parent', 0),
    task(childId, 'Existing child', 0, rootId),
    task(siblingId, 'Existing sibling', 1),
  ],
  edges: [],
  journals: [],
  artifacts: [],
  task_artifacts: [],
  layouts: [],
  progress: { leaf_total: 2, leaf_done: 0, ready: 2, waiting: 0, blocked: 0, review: 0 },
}

function operation(
  id: string,
  type: string,
  data: Record<string, unknown>,
  entityId?: string,
): ProposalOperation {
  return {
    id,
    type,
    data,
    entity_id: entityId,
    expected_version: entityId ? 3 : undefined,
    rationale: 'Source-backed draft operation.',
    confidence: 0.8,
    evidence: [{ kind: 'source_text', summary: 'Tracked in the project plan.' }],
    source_references: [],
    prerequisite_operation_ids: [],
  }
}

function ids(...values: string[]) {
  let index = 0
  return () => {
    const value = values[index]
    if (!value) throw new Error(`Unexpected UUID request at index ${index}`)
    index += 1
    return value
  }
}

describe('proposal outline staging', () => {
  it('projects a changed descendant together with its unchanged ancestors', () => {
    const operations = [operation('op-child', 'task.update', { title: 'Reviewed child' }, childId)]

    const stage = buildProposalStage(snapshot, operations)

    expect(stage.pipelineById.get(pipelineId)?.title).toBe('Existing pipeline')
    expect(stage.taskById.get(childId)).toMatchObject({ title: 'Reviewed child', proposed: true, operationIds: ['op-child'] })
    expect(stage.taskById.get(rootId)).toMatchObject({ title: 'Existing parent', proposed: false, operationIds: [] })
    expect(stage.children.get(`pipeline:${pipelineId}`)?.map((item) => item.id)).toEqual([rootId, siblingId])
    expect(stage.children.get(rootId)?.map((item) => item.id)).toEqual([childId])
    expect(stage.taskById.get(siblingId)).toMatchObject({ title: 'Existing sibling', proposed: false, operationIds: [] })
  })

  it('shows both original and projected neighborhoods for a staged reparent', () => {
    const destinationPipelineId = '66666666-6666-4666-8666-666666666666'
    const sourceParentId = '77777777-7777-4777-8777-777777777770'
    const originalBeforeId = '77777777-7777-4777-8777-777777777771'
    const movedId = '77777777-7777-4777-8777-777777777772'
    const originalAfterId = '77777777-7777-4777-8777-777777777773'
    const destinationParentId = '88888888-8888-4888-8888-888888888880'
    const destinationBeforeId = '88888888-8888-4888-8888-888888888881'
    const destinationAfterId = '88888888-8888-4888-8888-888888888882'
    const movedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        snapshot.pipelines[0],
        {
          id: destinationPipelineId,
          project_id: projectId,
          title: 'Destination pipeline',
          flow_mode: 'freeform',
          position: 1,
          archived: false,
          version: 1,
        },
      ],
      tasks: [
        task(sourceParentId, 'Source parent', 0),
        task(originalBeforeId, 'Original before', 0, sourceParentId),
        task(movedId, 'Moved task', 1, sourceParentId),
        task(originalAfterId, 'Original after', 2, sourceParentId),
        { ...task(destinationParentId, 'Destination parent', 0), pipeline_id: destinationPipelineId },
        { ...task(destinationBeforeId, 'Destination before', 0, destinationParentId), pipeline_id: destinationPipelineId },
        { ...task(destinationAfterId, 'Destination after', 2, destinationParentId), pipeline_id: destinationPipelineId },
      ],
    }
    const operations = [operation('op-reparent', 'task.update', {
      pipeline_id: destinationPipelineId,
      parent_id: destinationParentId,
      position: 1,
    }, movedId)]

    const stage = buildProposalStage(movedSnapshot, operations)
    const projection = buildProposalFocusProjection(stage, movedSnapshot, operations)

    expect(stage.pipelines.map((pipeline) => pipeline.id)).toEqual([pipelineId, destinationPipelineId])
    expect(projection.visibleTaskIds).toEqual(new Set([
      sourceParentId,
      originalBeforeId,
      movedId,
      originalAfterId,
      destinationParentId,
      destinationBeforeId,
      destinationAfterId,
    ]))
  })

  it('projects a cross-pipeline parent move onto descendants and blocks stale descendant edits', () => {
    const destinationPipelineId = '66666666-6666-4666-8666-666666666666'
    const movedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        ...snapshot.pipelines,
        {
          id: destinationPipelineId,
          project_id: projectId,
          title: 'Destination pipeline',
          flow_mode: 'freeform',
          position: 1,
          archived: false,
          version: 1,
        },
      ],
    }
    const operations = [operation('op-parent-move', 'task.update', {
      pipeline_id: destinationPipelineId,
      parent_id: null,
    }, rootId)]

    const stage = buildProposalStage(movedSnapshot, operations)

    expect(stage.taskById.get(rootId)?.pipeline_id).toBe(destinationPipelineId)
    expect(stage.taskById.get(childId)?.pipeline_id).toBe(destinationPipelineId)
    expect(() => updateStagedTask(
      operations,
      movedSnapshot,
      childId,
      { priority: 'optional' },
      ids('blocked-child-edit'),
    )).toThrow(/Apply, revert, or reject that subtree move/)
    expect(() => addStagedTask(
      operations,
      movedSnapshot,
      rootId,
      rootId,
      'Unsafe new child',
      1,
      ids('blocked-child-id', 'blocked-child-create'),
    )).toThrow(/Apply, revert, or reject that subtree move/)
  })

  it('blocks only cross-pipeline parent moves that precede an existing descendant edit', () => {
    const destinationPipelineId = '66666666-6666-4666-8666-666666666666'
    const movedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        ...snapshot.pipelines,
        {
          id: destinationPipelineId,
          project_id: projectId,
          title: 'Destination pipeline',
          flow_mode: 'freeform',
          position: 1,
          archived: false,
          version: 1,
        },
      ],
    }
    const parentThenChild = [
      operation('op-parent', 'task.update', { title: 'Reviewed parent' }, rootId),
      operation('op-child', 'task.update', { priority: 'optional' }, childId),
    ]

    expect(() => updateStagedTask(
      parentThenChild,
      movedSnapshot,
      rootId,
      { pipeline_id: destinationPipelineId, parent_id: null },
    )).toThrow(/Apply, revert, or reject the descendant change before staging this subtree move/)

    const childThenParent = [parentThenChild[1], parentThenChild[0]]
    const safelyMoved = updateStagedTask(
      childThenParent,
      movedSnapshot,
      rootId,
      { pipeline_id: destinationPipelineId, parent_id: null },
    )
    expect(safelyMoved.map((item) => item.id)).toEqual(['op-child', 'op-parent'])
    expect(safelyMoved[1].data.pipeline_id).toBe(destinationPipelineId)
  })

  it('adds a trimmed child task and splits a proposed task into ordered subtasks', () => {
    const added = addStagedTask(
      [], snapshot, pipelineId, rootId, '  Added child  ', 2,
      ids('66666666-6666-4666-8666-666666666666', 'op-added'),
    )
    expect(added[0]).toMatchObject({
      id: 'op-added',
      type: 'task.create',
      data: {
        id: '66666666-6666-4666-8666-666666666666',
        parent_id: rootId,
        title: 'Added child',
        position: 2,
      },
    })

    const proposedPipelineId = '77777777-7777-4777-8777-777777777777'
    const proposedTaskId = '88888888-8888-4888-8888-888888888888'
    const proposed = [
      operation('op-pipeline', 'pipeline.create', { id: proposedPipelineId, title: 'Proposed pipeline', position: 1 }),
      {
        ...operation('op-parent', 'task.create', {
          id: proposedTaskId,
          pipeline_id: proposedPipelineId,
          title: 'Broad task',
          position: 0,
        }),
        prerequisite_operation_ids: ['op-pipeline'],
      },
    ]
    const split = splitStagedTask(
      proposed, snapshot, proposedTaskId, [' First leaf ', '', 'Second leaf'],
      ids(
        '99999999-9999-4999-8999-999999999991', 'op-first',
        '99999999-9999-4999-8999-999999999992', 'op-second',
      ),
    )
    const stage = buildProposalStage(snapshot, split)

    expect(stage.taskById.get(proposedTaskId)).toMatchObject({ kind: 'milestone', child_flow_mode: 'freeform' })
    expect(stage.children.get(proposedTaskId)?.map((item) => item.title)).toEqual(['First leaf', 'Second leaf'])
    const children = split.filter((item) => item.type === 'task.create' && item.id !== 'op-parent')
    expect(children.map((item) => item.prerequisite_operation_ids)).toEqual([
      ['op-pipeline', 'op-parent'],
      ['op-pipeline', 'op-parent'],
    ])
  })

  it('indents, outdents, and reorders staged siblings', () => {
    const taskA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const taskB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    const taskC = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
    const movementPipelineId = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
    const operations = [
      operation('op-move-pipeline', 'pipeline.create', { id: movementPipelineId, title: 'Movement pipeline', position: 1 }),
      operation('op-a', 'task.create', { id: taskA, pipeline_id: movementPipelineId, title: 'A', position: 0 }),
      {
        ...operation("op-b", "task.create", { id: taskB, pipeline_id: movementPipelineId, title: "B", position: 1 }),
        prerequisite_operation_ids: ["op-note", "op-move-pipeline"],
      },
      operation("op-c", "task.create", { id: taskC, pipeline_id: movementPipelineId, title: "C", position: 2 }),
      operation("op-note", "journal.create", { id: "note-one", task_id: taskB, content: "Keep this prerequisite" }),
    ]

    const indented = moveStagedTask(operations, snapshot, taskB, 'indent')
    expect(buildProposalStage(snapshot, indented).taskById.get(taskB)?.parent_id).toBe(taskA)
    expect(indented.find((item) => item.id === "op-b")?.prerequisite_operation_ids).toEqual(["op-note", "op-move-pipeline", "op-a"])

    const outdented = moveStagedTask(indented, snapshot, taskB, 'outdent')
    expect(buildProposalStage(snapshot, outdented).taskById.get(taskB)?.parent_id).toBeNull()
    expect(outdented.find((item) => item.id === "op-b")?.prerequisite_operation_ids).toEqual(["op-note", "op-move-pipeline"])

    const movedUp = moveStagedTask(operations, snapshot, taskC, 'up')
    expect(buildProposalStage(snapshot, movedUp).children.get(`pipeline:${movementPipelineId}`)?.map((item) => item.title)).toEqual(['A', 'C', 'B'])
  })

  it('reorders staged pipelines without changing unrelated operations', () => {
    const firstPipeline = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'
    const secondPipeline = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
    const operations = [
      operation('op-first-pipeline', 'pipeline.create', { id: firstPipeline, title: 'First pipeline', position: 1 }),
      operation('op-second-pipeline', 'pipeline.create', { id: secondPipeline, title: 'Second pipeline', position: 2 }),
      operation('op-unrelated-task', 'task.update', { priority: 'optional' }, siblingId),
    ]

    const reordered = moveStagedPipeline(operations, snapshot, secondPipeline, 'up')

    expect(buildProposalStage(snapshot, reordered).pipelines.map((pipeline) => pipeline.id)).toEqual([
      pipelineId,
      secondPipeline,
      firstPipeline,
    ])
    expect(reordered.find((item) => item.id === 'op-unrelated-task')).toEqual(operations[2])
  })

  it('uses the full canonical pipeline order for sparse reordering and append placement', () => {
    const pipelineOne = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1'
    const pipelineTwo = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2'
    const pipelineThree = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3'
    const orderedSnapshot: ProjectSnapshot = {
      ...snapshot,
      pipelines: [
        snapshot.pipelines[0],
        { ...snapshot.pipelines[0], id: pipelineOne, title: 'Pipeline one', position: 1 },
        { ...snapshot.pipelines[0], id: pipelineTwo, title: 'Pipeline two', position: 2 },
        { ...snapshot.pipelines[0], id: pipelineThree, title: 'Pipeline three', position: 3 },
      ],
    }
    const sparseOperations = [
      operation('op-first-visible', 'pipeline.update', { title: 'First visible' }, pipelineId),
      operation('op-last-visible', 'pipeline.update', { title: 'Last visible' }, pipelineThree),
    ]

    const reordered = moveStagedPipeline(sparseOperations, orderedSnapshot, pipelineThree, 'up')
    const lastVisible = reordered.find((item) => item.id === 'op-last-visible')

    expect(lastVisible?.data.position).toBe(1.5)
    expect(buildProjectedPipelineOrder(orderedSnapshot, reordered).map((pipeline) => pipeline.id)).toEqual([
      pipelineId,
      pipelineOne,
      pipelineThree,
      pipelineTwo,
    ])
    expect(nextStagedPipelinePosition(orderedSnapshot, [])).toBe(4)
  })

  it('removes a new pipeline with its staged task subtree and consumers', () => {
    const proposedPipeline = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'
    const proposedParent = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2'
    const proposedChild = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3'
    const operations: ProposalOperation[] = [
      operation('op-new-pipeline', 'pipeline.create', { id: proposedPipeline, title: 'Disposable pipeline', position: 1 }),
      {
        ...operation('op-new-parent', 'task.create', { id: proposedParent, pipeline_id: proposedPipeline, title: 'Parent', position: 0 }),
        prerequisite_operation_ids: ['op-new-pipeline'],
      },
      {
        ...operation('op-new-child', 'task.create', { id: proposedChild, pipeline_id: proposedPipeline, parent_id: proposedParent, title: 'Child', position: 0 }),
        prerequisite_operation_ids: ['op-new-pipeline', 'op-new-parent'],
      },
      operation('op-child-note', 'journal.create', { id: 'note-new-child', task_id: proposedChild, content: 'Dependent note' }),
      operation('op-independent-task', 'task.update', { priority: 'optional' }, siblingId),
    ]

    const remaining = removeStagedPipeline(operations, snapshot, proposedPipeline)

    expect(remaining.map((item) => item.id)).toEqual(['op-independent-task'])
  })

  it('reverts only an existing pipeline update and its dependents', () => {
    const operations = [
      operation('op-existing-pipeline', 'pipeline.update', { title: 'Reviewed pipeline' }, pipelineId),
      operation('op-independent-child', 'task.update', { priority: 'optional' }, childId),
      {
        ...operation('op-dependent-sibling', 'task.update', { priority: 'recommended' }, siblingId),
        prerequisite_operation_ids: ['op-existing-pipeline'],
      },
    ]

    const remaining = removeStagedPipeline(operations, snapshot, pipelineId)

    expect(remaining.map((item) => item.id)).toEqual(['op-independent-child'])
    expect(buildProposalStage(snapshot, remaining).taskById.get(childId)).toMatchObject({
      proposed: true,
      priority: 'optional',
    })
  })

  it('reverts proposal changes to an existing task without deleting the monitor task', () => {
    const operations = [
      operation('op-child', 'task.update', { title: 'Proposed child title' }, childId),
      operation('op-pipeline', 'pipeline.update', { title: 'Reviewed pipeline' }, pipelineId),
    ]

    const reverted = removeStagedTask(operations, snapshot, childId)
    const stage = buildProposalStage(snapshot, reverted)

    expect(reverted.map((item) => item.id)).toEqual(['op-pipeline'])
    expect(snapshot.tasks.find((item) => item.id === childId)?.title).toBe('Existing child')
    expect(stage.taskById.get(childId)).toMatchObject({ title: 'Existing child', proposed: false, operationIds: [] })
  })

  it('reverts only an existing parent edit while retaining independent descendant and reference operations', () => {
    const operations = [
      operation('op-parent', 'task.update', { title: 'Proposed parent title' }, rootId),
      operation('op-child', 'task.update', { priority: 'optional' }, childId),
      operation('op-note', 'journal.create', { id: 'note-parent', task_id: rootId, content: 'Independent parent note' }),
      {
        ...operation('op-dependent', 'pipeline.update', { description: 'Depends on the parent edit' }, pipelineId),
        prerequisite_operation_ids: ['op-parent'],
      },
    ]

    const reverted = removeStagedTask(operations, snapshot, rootId)

    expect(reverted.map((item) => item.id)).toEqual(['op-child', 'op-note'])
    expect(buildProposalStage(snapshot, reverted).taskById.get(childId)).toMatchObject({ priority: 'optional', proposed: true })
  })

  it('removes a proposed subtree, its consumers, and complete atomic groups', () => {
    const proposedParent = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd'
    const proposedChild = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
    const operations: ProposalOperation[] = [
      operation('op-parent', 'task.create', { id: proposedParent, pipeline_id: pipelineId, title: 'Parent', position: 2 }),
      {
        ...operation('op-child', 'task.create', { id: proposedChild, pipeline_id: pipelineId, parent_id: proposedParent, title: 'Child', position: 0 }),
        atomic_group_id: 'group-one',
        prerequisite_operation_ids: ['op-parent'],
      },
      {
        ...operation('op-grouped-artifact', 'artifact.create', { id: 'artifact-one', locator: 'result.json' }),
        atomic_group_id: 'group-one',
      },
      operation('op-journal', 'journal.create', { id: 'journal-one', task_id: proposedChild, content: 'Draft note' }),
      {
        ...operation('op-dependent', 'pipeline.update', { description: 'Depends on the subtree' }, pipelineId),
        prerequisite_operation_ids: ['op-child'],
      },
      operation('op-unrelated', 'task.update', { priority: 'optional' }, siblingId),
    ]

    const remaining = removeStagedTask(operations, snapshot, proposedParent)

    expect(remaining.map((item) => item.id)).toEqual(['op-unrelated'])
  })

  it('refuses to split a task that already has an existing or staged child', () => {
    const existingParent = [operation('op-root', 'task.update', { title: 'Reviewed existing parent' }, rootId)]
    expect(() => splitStagedTask(existingParent, snapshot, rootId, ['First', 'Second'])).toThrow(/already has children/i)

    const proposedParent = '66666666-6666-4666-8666-666666666661'
    const proposedChild = '66666666-6666-4666-8666-666666666662'
    const stagedParent = [
      operation('op-proposed-parent', 'task.create', { id: proposedParent, pipeline_id: pipelineId, title: 'Proposed parent', position: 2 }),
      operation('op-proposed-child', 'task.create', { id: proposedChild, pipeline_id: pipelineId, parent_id: proposedParent, title: 'Proposed child', position: 0 }),
    ]
    expect(() => splitStagedTask(stagedParent, snapshot, proposedParent, ['First', 'Second'])).toThrow(/already has children/i)
  })

  it("refreshes proposed placement prerequisites and honors an explicit root parent", () => {
    const oldPipeline = "aaaaaaaa-1111-4111-8111-111111111111"
    const newPipeline = "bbbbbbbb-2222-4222-8222-222222222222"
    const proposedParent = "cccccccc-3333-4333-8333-333333333333"
    const independentTask = "dddddddd-4444-4444-8444-444444444444"
    const independentPipeline = "eeeeeeee-5555-4555-8555-555555555555"
    const operations: ProposalOperation[] = [
      operation("op-old-pipeline", "pipeline.create", { id: oldPipeline, title: "Old proposed pipeline" }),
      operation("op-new-pipeline", "pipeline.create", { id: newPipeline, title: "New proposed pipeline" }),
      operation("op-independent-pipeline", "pipeline.create", { id: independentPipeline, title: "Independent proposed pipeline" }),
      {
        ...operation("op-parent", "task.create", { id: proposedParent, pipeline_id: oldPipeline, title: "Proposed parent" }),
        prerequisite_operation_ids: ["op-old-pipeline"],
      },
      operation("op-independent-task", "task.create", { id: independentTask, pipeline_id: pipelineId, title: "Independent prerequisite task" }),
      operation("op-note", "journal.create", { id: "note-two", task_id: childId, content: "Preserve me" }),
      {
        ...operation("op-move", "task.update", { pipeline_id: oldPipeline, parent_id: proposedParent }, childId),
        prerequisite_operation_ids: [
          "op-note",
          "op-independent-task",
          "op-independent-pipeline",
          "op-old-pipeline",
          "op-parent",
        ],
      },
    ]

    const moved = updateStagedTask(operations, snapshot, childId, { pipeline_id: newPipeline, parent_id: null })
    const move = moved.find((item) => item.id === "op-move")

    expect(move?.data).toMatchObject({ pipeline_id: newPipeline, parent_id: null })
    expect(move?.prerequisite_operation_ids).toEqual([
      "op-note",
      "op-independent-task",
      "op-independent-pipeline",
      "op-new-pipeline",
    ])
    expect(buildProposalStage(snapshot, moved).taskById.get(childId)).toMatchObject({ pipeline_id: newPipeline, parent_id: null })
  })

  it("applies batch-style task patches while retaining independent entity versions", () => {
    const updated = [childId, siblingId].reduce(
      (current, taskId, index) => updateStagedTask(
        current,
        snapshot,
        taskId,
        { priority: index === 0 ? 'recommended' : 'optional', status: index === 0 ? 'review' : 'in_progress' },
        ids(`batch-op-${index}`),
      ),
      [] as ProposalOperation[],
    )

    expect(updated).toHaveLength(2)
    expect(updated.map((item) => ({ entity: item.entity_id, version: item.expected_version, data: item.data }))).toEqual([
      { entity: childId, version: 3, data: { priority: 'recommended', status: 'review' } },
      { entity: siblingId, version: 3, data: { priority: 'optional', status: 'in_progress' } },
    ])
    expect(updated.every((item) => item.evidence?.some((evidence) => typeof evidence === 'object' && evidence.kind === 'user_instruction'))).toBe(true)
  })

  it('detects semantic staging edits but ignores stored diff and disposition metadata', () => {
    const original = [{
      ...operation('op-child', 'task.update', { title: 'Reviewed child' }, childId),
      disposition: 'pending' as const,
      before: { title: 'Existing child' },
      after: { title: 'Reviewed child' },
    }]

    expect(operationsChanged(original, [{ ...original[0], disposition: 'selected' }])).toBe(false)
    expect(operationsChanged(original, [{ ...original[0], data: { title: 'Human title' } }])).toBe(true)
  })
})

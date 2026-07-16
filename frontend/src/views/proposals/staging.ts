import type {
  Pipeline,
  ProjectSnapshot,
  ProposalOperation,
  Task,
  TaskKind,
  TaskOutcome,
  TaskPriority,
  TaskStatus,
} from '../../types'

export type StagePipeline = Pipeline & {
  operationIds: string[]
  proposed: boolean
}

export type StageTask = Task & {
  operationIds: string[]
  proposed: boolean
}

export type ProposalStage = {
  pipelines: StagePipeline[]
  tasks: StageTask[]
  pipelineById: Map<string, StagePipeline>
  taskById: Map<string, StageTask>
  children: Map<string, StageTask[]>
}

export const PROPOSAL_CONTEXT_LIMIT = 20

export type ProposalFocusProjection = {
  visibleTaskIds: Set<string>
  hiddenTaskCountByPipeline: Map<string, number>
}

export type TaskStagePatch = Partial<Pick<Task,
  'title' | 'user_key' | 'description' | 'kind' | 'status' | 'outcome' |
  'priority' | 'labels' | 'target_date' | 'position' | 'completion_criteria' |
  'blocker_reason' | 'completion_summary' | 'completion_override_reason' |
  'child_flow_mode' | 'pipeline_id' | 'parent_id'
>>

export type PipelineStagePatch = Partial<Pick<Pipeline,
  'title' | 'description' | 'flow_mode' | 'position'
>>

const humanSummary = 'The user edited this operation in graphical proposal staging.'

const humanEvidence = () => ({
  kind: 'user_instruction',
  summary: humanSummary,
})

export function cleanProposalOperation(operation: ProposalOperation): ProposalOperation {
  const { disposition: _disposition, before: _before, after: _after, ...clean } = operation
  return clean
}

export function proposalOperationEntityId(operation: ProposalOperation): string | null {
  const dataId = operation.data.id
  return typeof dataId === 'string' ? dataId : operation.entity_id ?? null
}

function operationTargets(operation: ProposalOperation, prefix: string, entityId: string) {
  return operation.type.startsWith(prefix) && proposalOperationEntityId(operation) === entityId
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function asNullableString(value: unknown, fallback: string | null = null): string | null {
  return value === null ? null : typeof value === 'string' ? value : fallback
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function asLabels(value: unknown, fallback: string[] = []): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : fallback
}

function taskFromOperation(snapshot: ProjectSnapshot, operations: ProposalOperation[], entityId: string): StageTask | null {
  const matching = operations.filter((operation) => operationTargets(operation, 'task.', entityId))
  const create = matching.find((operation) => operation.type === 'task.create')
  const current = snapshot.tasks.find((task) => task.id === entityId)
  if (!create && !current) return null
  const base: Task = current ? { ...current } : {
    id: entityId,
    project_id: snapshot.project.id,
    pipeline_id: asString(create?.data.pipeline_id),
    parent_id: null,
    user_key: null,
    kind: 'task',
    title: asString(create?.data.title, 'Untitled task'),
    description: null,
    status: 'planned',
    outcome: null,
    priority: 'required',
    labels: [],
    target_date: null,
    position: 0,
    completion_criteria: null,
    blocker_reason: null,
    completion_summary: null,
    completion_actor: null,
    completion_source: null,
    completion_provenance: null,
    completion_override_reason: null,
    consistency_warning: null,
    incomplete_descendant_ids: [],
    child_flow_mode: 'freeform',
    readiness: 'ready',
    unsatisfied_predecessor_ids: [],
    completed_at: null,
    deleted_at: null,
    version: 0,
  }
  const data = matching.reduce<Record<string, unknown>>((result, operation) => ({ ...result, ...operation.data }), {})
  return {
    ...base,
    pipeline_id: asString(data.pipeline_id, base.pipeline_id),
    parent_id: asNullableString(data.parent_id, base.parent_id ?? null),
    user_key: asNullableString(data.user_key, base.user_key ?? null),
    title: asString(data.title, base.title),
    description: asNullableString(data.description, base.description ?? null),
    kind: asString(data.kind, base.kind) as TaskKind,
    status: asString(data.status, base.status) as TaskStatus,
    outcome: asNullableString(data.outcome, base.outcome ?? null) as TaskOutcome | null,
    priority: asString(data.priority, base.priority) as TaskPriority,
    labels: asLabels(data.labels, base.labels),
    target_date: asNullableString(data.target_date, base.target_date ?? null),
    position: asNumber(data.position, base.position),
    completion_criteria: asNullableString(data.completion_criteria, base.completion_criteria ?? null),
    blocker_reason: asNullableString(data.blocker_reason, base.blocker_reason ?? null),
    completion_summary: asNullableString(data.completion_summary, base.completion_summary ?? null),
    completion_override_reason: asNullableString(data.completion_override_reason, base.completion_override_reason ?? null),
    child_flow_mode: asString(data.child_flow_mode, base.child_flow_mode) as 'sequential' | 'freeform',
    operationIds: matching.map((operation) => operation.id),
    proposed: matching.length > 0,
  }
}

function pipelineFromOperation(snapshot: ProjectSnapshot, operations: ProposalOperation[], entityId: string): StagePipeline | null {
  const matching = operations.filter((operation) => operationTargets(operation, 'pipeline.', entityId))
  const create = matching.find((operation) => operation.type === 'pipeline.create')
  const current = snapshot.pipelines.find((pipeline) => pipeline.id === entityId)
  if (!create && !current) return null
  const base: Pipeline = current ? { ...current } : {
    id: entityId,
    project_id: snapshot.project.id,
    title: asString(create?.data.title, 'Untitled pipeline'),
    description: null,
    flow_mode: 'sequential',
    position: 0,
    archived: false,
    deleted_at: null,
    version: 0,
  }
  const data = matching.reduce<Record<string, unknown>>((result, operation) => ({ ...result, ...operation.data }), {})
  return {
    ...base,
    title: asString(data.title, base.title),
    description: asNullableString(data.description, base.description ?? null),
    flow_mode: asString(data.flow_mode, base.flow_mode) as 'sequential' | 'freeform',
    position: asNumber(data.position, base.position),
    operationIds: matching.map((operation) => operation.id),
    proposed: matching.length > 0,
  }
}

export function buildProposalStage(snapshot: ProjectSnapshot, operations: ProposalOperation[]): ProposalStage {
  const taskIds = new Set<string>()
  const pipelineIds = new Set<string>()
  const taskBySnapshotId = new Map(snapshot.tasks.map((task) => [task.id, task]))
  operations.forEach((operation) => {
    const entityId = proposalOperationEntityId(operation)
    if (entityId && operation.type.startsWith('task.')) {
      taskIds.add(entityId)
      const current = taskBySnapshotId.get(entityId)
      if (current) pipelineIds.add(current.pipeline_id)
      const proposedPipelineId = operation.data.pipeline_id
      if (typeof proposedPipelineId === 'string') pipelineIds.add(proposedPipelineId)
    }
    if (entityId && operation.type.startsWith('pipeline.')) pipelineIds.add(entityId)
  })

  snapshot.tasks
    .filter((task) => !task.deleted_at && pipelineIds.has(task.pipeline_id))
    .forEach((task) => taskIds.add(task.id))
  const addAncestors = (taskId: string) => {
    let task = taskBySnapshotId.get(taskId)
    while (task?.parent_id && !taskIds.has(task.parent_id)) {
      taskIds.add(task.parent_id)
      task = taskBySnapshotId.get(task.parent_id)
    }
  }
  ;[...taskIds].forEach(addAncestors)

  const tasks = [...taskIds]
    .map((id) => taskFromOperation(snapshot, operations, id))
    .filter((task): task is StageTask => Boolean(task))
  const taskById = new Map(tasks.map((task) => [task.id, task]))
  const resolvedPipelines = new Map<string, string>()
  const resolving = new Set<string>()
  const resolvePipeline = (task: StageTask): string => {
    const cached = resolvedPipelines.get(task.id)
    if (cached) return cached
    if (resolving.has(task.id)) return task.pipeline_id
    resolving.add(task.id)
    const parent = task.parent_id ? taskById.get(task.parent_id) : undefined
    const pipelineId = parent ? resolvePipeline(parent) : task.pipeline_id
    resolving.delete(task.id)
    resolvedPipelines.set(task.id, pipelineId)
    return pipelineId
  }
  tasks.forEach((task) => { task.pipeline_id = resolvePipeline(task) })
  tasks.forEach((task) => pipelineIds.add(task.pipeline_id))
  const pipelines = [...pipelineIds]
    .map((id) => pipelineFromOperation(snapshot, operations, id))
    .filter((pipeline): pipeline is StagePipeline => Boolean(pipeline))
    .sort((left, right) => left.position - right.position || left.title.localeCompare(right.title))
  const pipelineById = new Map(pipelines.map((pipeline) => [pipeline.id, pipeline]))
  const children = new Map<string, StageTask[]>()
  tasks.forEach((task) => {
    const key = task.parent_id ?? `pipeline:${task.pipeline_id}`
    children.set(key, [...(children.get(key) ?? []), task])
  })
  children.forEach((group) => group.sort((left, right) => left.position - right.position || left.title.localeCompare(right.title)))
  return { pipelines, tasks, pipelineById, taskById, children }
}

export function buildProjectedPipelineOrder(
  snapshot: ProjectSnapshot,
  operations: ProposalOperation[],
): StagePipeline[] {
  const ids = new Set(
    snapshot.pipelines
      .filter((pipeline) => !pipeline.deleted_at && !pipeline.archived)
      .map((pipeline) => pipeline.id),
  )
  operations.forEach((operation) => {
    const entityId = proposalOperationEntityId(operation)
    if (entityId && operation.type.startsWith('pipeline.')) ids.add(entityId)
  })
  return [...ids]
    .map((id) => pipelineFromOperation(snapshot, operations, id))
    .filter((pipeline): pipeline is StagePipeline => Boolean(pipeline))
    .sort((left, right) => left.position - right.position || left.title.localeCompare(right.title))
}

export function nextStagedPipelinePosition(snapshot: ProjectSnapshot, operations: ProposalOperation[]): number {
  const pipelines = buildProjectedPipelineOrder(snapshot, operations)
  return pipelines.length ? Math.max(...pipelines.map((pipeline) => pipeline.position)) + 1 : 0
}

export function buildProposalFocusProjection(
  stage: ProposalStage,
  snapshot: ProjectSnapshot,
  operations: ProposalOperation[],
): ProposalFocusProjection {
  const seedTaskIds = new Set(operations
    .filter((operation) => operation.type.startsWith('task.'))
    .map(proposalOperationEntityId)
    .filter((id): id is string => Boolean(id && stage.taskById.has(id))))
  const seedPipelineIds = new Set(operations
    .filter((operation) => operation.type.startsWith('pipeline.'))
    .map(proposalOperationEntityId)
    .filter((id): id is string => Boolean(id && stage.pipelineById.has(id))))
  const visibleTaskIds = new Set<string>()
  const alwaysVisibleTaskIds = new Set<string>()
  const snapshotTaskById = new Map(snapshot.tasks.map((task) => [task.id, task]))

  const includeAlways = (id?: string | null) => {
    if (!id || !stage.taskById.has(id)) return
    alwaysVisibleTaskIds.add(id)
    visibleTaskIds.add(id)
  }
  const includeProjectedAncestors = (taskId: string) => {
    let task = stage.taskById.get(taskId)
    const visited = new Set<string>()
    while (task?.parent_id && !visited.has(task.parent_id)) {
      visited.add(task.parent_id)
      includeAlways(task.parent_id)
      task = stage.taskById.get(task.parent_id)
    }
  }
  const includeNeighbors = (siblings: StageTask[], taskId: string) => {
    const index = siblings.findIndex((task) => task.id === taskId)
    if (index < 0) return
    includeAlways(siblings[index - 1]?.id)
    includeAlways(siblings[index]?.id)
    includeAlways(siblings[index + 1]?.id)
  }

  seedTaskIds.forEach((taskId) => {
    const task = stage.taskById.get(taskId)
    if (!task) return
    includeAlways(taskId)
    includeProjectedAncestors(taskId)
    includeNeighbors(stage.children.get(task.parent_id ?? `pipeline:${task.pipeline_id}`) ?? [], taskId)
  })

  operations.forEach((operation) => {
    if (!operation.type.startsWith('task.')) return
    const taskId = proposalOperationEntityId(operation)
    const original = taskId ? snapshotTaskById.get(taskId) : undefined
    const projected = taskId ? stage.taskById.get(taskId) : undefined
    const placementChanged = operation.type === 'task.move'
      || Object.prototype.hasOwnProperty.call(operation.data, 'parent_id')
      || Object.prototype.hasOwnProperty.call(operation.data, 'pipeline_id')
    if (!original || !projected || !placementChanged) return
    let ancestor = original.parent_id ? snapshotTaskById.get(original.parent_id) : undefined
    const visited = new Set<string>()
    while (ancestor && !visited.has(ancestor.id)) {
      visited.add(ancestor.id)
      includeAlways(ancestor.id)
      ancestor = ancestor.parent_id ? snapshotTaskById.get(ancestor.parent_id) : undefined
    }
    const originalSiblings = snapshot.tasks
      .filter((task) => !task.deleted_at && task.pipeline_id === original.pipeline_id && task.parent_id === original.parent_id)
      .sort((left, right) => left.position - right.position || left.title.localeCompare(right.title))
    const index = originalSiblings.findIndex((task) => task.id === original.id)
    includeAlways(originalSiblings[index - 1]?.id)
    includeAlways(originalSiblings[index + 1]?.id)
  })

  seedTaskIds.forEach((taskId) => {
    let contextCount = 0
    for (const child of stage.children.get(taskId) ?? []) {
      if (alwaysVisibleTaskIds.has(child.id)) continue
      if (contextCount >= PROPOSAL_CONTEXT_LIMIT) break
      visibleTaskIds.add(child.id)
      contextCount += 1
    }
  })
  seedPipelineIds.forEach((pipelineId) => {
    let contextCount = 0
    for (const root of stage.children.get(`pipeline:${pipelineId}`) ?? []) {
      if (alwaysVisibleTaskIds.has(root.id)) continue
      if (contextCount >= PROPOSAL_CONTEXT_LIMIT) break
      visibleTaskIds.add(root.id)
      contextCount += 1
    }
  })

  const hiddenTaskCountByPipeline = new Map<string, number>()
  stage.pipelines.forEach((pipeline) => {
    const total = stage.tasks.filter((task) => task.pipeline_id === pipeline.id).length
    const visible = stage.tasks.filter((task) => task.pipeline_id === pipeline.id && visibleTaskIds.has(task.id)).length
    hiddenTaskCountByPipeline.set(pipeline.id, Math.max(0, total - visible))
  })
  return { visibleTaskIds, hiddenTaskCountByPipeline }
}

function withHumanEdit(operation: ProposalOperation, summary: string, data: Record<string, unknown>): ProposalOperation {
  const evidence = operation.evidence ?? []
  const hasHumanMarker = evidence.some((item) => typeof item === 'object' && item != null && item.kind === 'user_instruction' && item.summary === humanSummary)
  return {
    ...operation,
    type: operation.type === 'task.move' ? 'task.update' : operation.type,
    data,
    rationale: operation.rationale ? `${operation.rationale} Human review: ${summary}` : `Edited during graphical proposal review: ${summary}`,
    evidence: hasHumanMarker ? evidence : [...evidence, humanEvidence()],
  }
}

function createdPrerequisites(operations: ProposalOperation[], pipelineId: string, parentId?: string | null) {
  const ids = operations.filter((operation) => {
    const entityId = proposalOperationEntityId(operation)
    return (operation.type === 'pipeline.create' && entityId === pipelineId) ||
      (operation.type === 'task.create' && entityId === parentId)
  }).map((operation) => operation.id)
  return [...new Set(ids)]
}

function hasOwn(value: object, field: string) {
  return Object.prototype.hasOwnProperty.call(value, field)
}

function refreshedCreatedPrerequisites(
  operations: ProposalOperation[],
  operation: ProposalOperation,
  previousPipelineId: string,
  previousParentId: string | null,
  pipelineId: string,
  parentId?: string | null,
) {
  const previousPlacementIds = new Set(createdPrerequisites(
    operations,
    previousPipelineId,
    previousParentId,
  ))
  const preserved = (operation.prerequisite_operation_ids ?? [])
    .filter((id) => !previousPlacementIds.has(id))
  return [...new Set([...preserved, ...createdPrerequisites(operations, pipelineId, parentId)])]
}

function projectedTaskPipelineId(
  snapshot: ProjectSnapshot,
  operations: ProposalOperation[],
  taskId: string,
  cache: Map<string, string | null>,
  visiting = new Set<string>(),
): string | null {
  if (cache.has(taskId)) return cache.get(taskId) ?? null
  if (visiting.has(taskId)) return null
  const current = snapshot.tasks.find((task) => task.id === taskId)
  const matching = operations.filter((operation) => operationTargets(operation, 'task.', taskId))
  const create = matching.find((operation) => operation.type === 'task.create')
  if (!current && !create) return null
  const data = matching.reduce<Record<string, unknown>>((result, operation) => ({ ...result, ...operation.data }), {})
  const ownPipelineId = asString(data.pipeline_id, current?.pipeline_id ?? asString(create?.data.pipeline_id))
  const parentId = hasOwn(data, 'parent_id')
    ? asNullableString(data.parent_id)
    : current?.parent_id ?? asNullableString(create?.data.parent_id)
  visiting.add(taskId)
  const pipelineId = parentId
    ? projectedTaskPipelineId(snapshot, operations, parentId, cache, visiting) ?? ownPipelineId
    : ownPipelineId
  visiting.delete(taskId)
  cache.set(taskId, pipelineId || null)
  return pipelineId || null
}

function crossPipelineMovedLineageTask(
  snapshot: ProjectSnapshot,
  operations: ProposalOperation[],
  taskId: string,
  includeSelf: boolean,
): Task | null {
  const taskById = new Map(snapshot.tasks.map((task) => [task.id, task]))
  const task = taskById.get(taskId)
  let current = includeSelf ? task : task?.parent_id ? taskById.get(task.parent_id) : undefined
  const cache = new Map<string, string | null>()
  const visited = new Set<string>()
  while (current && !visited.has(current.id)) {
    visited.add(current.id)
    const projectedPipelineId = projectedTaskPipelineId(snapshot, operations, current.id, cache)
    if (projectedPipelineId && projectedPipelineId !== current.pipeline_id) return current
    current = current.parent_id ? taskById.get(current.parent_id) : undefined
  }
  return null
}

function assertDescendantEditSafe(
  snapshot: ProjectSnapshot,
  operations: ProposalOperation[],
  taskId: string,
  includeSelf: boolean,
) {
  const moved = crossPipelineMovedLineageTask(snapshot, operations, taskId, includeSelf)
  if (!moved) return
  throw new Error(
    `Cannot edit descendants while “${moved.title}” is staged to move across pipelines. ` +
    'Apply, revert, or reject that subtree move before editing its existing descendants.',
  )
}

function topologicallyOrderedOperations(operations: ProposalOperation[]): ProposalOperation[] {
  const byId = new Map(operations.map((operation) => [operation.id, operation]))
  if (byId.size !== operations.length) return operations
  const sourceOrder = new Map(operations.map((operation, index) => [operation.id, index]))
  const outgoing = new Map<string, string[]>()
  const degree = new Map(operations.map((operation) => [operation.id, 0]))
  for (const operation of operations) {
    for (const prerequisiteId of operation.prerequisite_operation_ids ?? []) {
      if (!byId.has(prerequisiteId)) return operations
      outgoing.set(prerequisiteId, [...(outgoing.get(prerequisiteId) ?? []), operation.id])
      degree.set(operation.id, (degree.get(operation.id) ?? 0) + 1)
    }
  }
  const order = (left: string, right: string) => (
    (sourceOrder.get(left) ?? 0) - (sourceOrder.get(right) ?? 0)
  )
  const ready = operations
    .filter((operation) => degree.get(operation.id) === 0)
    .map((operation) => operation.id)
    .sort(order)
  const result: ProposalOperation[] = []
  while (ready.length) {
    const operationId = ready.shift()
    if (!operationId) break
    const operation = byId.get(operationId)
    if (!operation) return operations
    result.push(operation)
    for (const consumerId of [...(outgoing.get(operationId) ?? [])].sort(order)) {
      const nextDegree = (degree.get(consumerId) ?? 0) - 1
      degree.set(consumerId, nextDegree)
      if (nextDegree === 0) {
        ready.push(consumerId)
        ready.sort(order)
      }
    }
  }
  return result.length === operations.length ? result : operations
}

function snapshotDescendantIds(snapshot: ProjectSnapshot, taskId: string): Set<string> {
  const children = new Map<string, string[]>()
  snapshot.tasks.forEach((task) => {
    if (!task.parent_id) return
    children.set(task.parent_id, [...(children.get(task.parent_id) ?? []), task.id])
  })
  const result = new Set<string>()
  const visit = (parentId: string) => {
    for (const childId of children.get(parentId) ?? []) {
      if (result.has(childId)) continue
      result.add(childId)
      visit(childId)
    }
  }
  visit(taskId)
  return result
}

function assertCrossPipelineMoveOrderSafe(
  snapshot: ProjectSnapshot,
  operations: ProposalOperation[],
  taskId: string,
  moveOperationId: string,
) {
  const task = snapshot.tasks.find((item) => item.id === taskId)
  if (!task) return
  const projectedPipelineId = projectedTaskPipelineId(
    snapshot,
    operations,
    taskId,
    new Map<string, string | null>(),
  )
  if (!projectedPipelineId || projectedPipelineId === task.pipeline_id) return
  const descendants = snapshotDescendantIds(snapshot, taskId)
  if (!descendants.size) return
  const ordered = topologicallyOrderedOperations(operations)
  const moveIndex = ordered.findIndex((operation) => operation.id === moveOperationId)
  if (moveIndex < 0) return
  const laterDescendantOperation = ordered.slice(moveIndex + 1).find((operation) => (
    ['task.update', 'task.move'].includes(operation.type)
    && descendants.has(proposalOperationEntityId(operation) ?? '')
  ))
  if (!laterDescendantOperation) return
  const descendantId = proposalOperationEntityId(laterDescendantOperation)
  const descendant = snapshot.tasks.find((item) => item.id === descendantId)
  throw new Error(
    `Cannot move “${task.title}” across pipelines before the staged change to ` +
    `existing descendant “${descendant?.title ?? 'task'}”. Apply, revert, or reject the ` +
    'descendant change before staging this subtree move.',
  )
}

export function updateStagedTask(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  taskId: string,
  patch: TaskStagePatch,
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  if (snapshot.tasks.some((task) => task.id === taskId)) {
    assertDescendantEditSafe(snapshot, operations, taskId, false)
  }
  const next = operations.map(cleanProposalOperation)
  const createIndex = next.findIndex((operation) => operation.type === 'task.create' && proposalOperationEntityId(operation) === taskId)
  if (createIndex >= 0) {
    const current = next[createIndex]
    const previousPipelineId = asString(current.data.pipeline_id)
    const previousParentId = hasOwn(current.data, 'parent_id') ? asNullableString(current.data.parent_id) : null
    const data = { ...current.data, ...patch }
    const pipelineId = asString(data.pipeline_id)
    const parentId = hasOwn(data, 'parent_id') ? asNullableString(data.parent_id) : null
    next[createIndex] = {
      ...withHumanEdit(current, 'task fields changed', data),
      prerequisite_operation_ids: refreshedCreatedPrerequisites(
        next,
        current,
        previousPipelineId,
        previousParentId,
        pipelineId,
        parentId,
      ),
    }
    return next
  }
  const updateIndex = next.findIndex((operation) => ['task.update', 'task.move'].includes(operation.type) && proposalOperationEntityId(operation) === taskId)
  if (updateIndex >= 0) {
    const current = next[updateIndex]
    const task = snapshot.tasks.find((item) => item.id === taskId)
    if (!task) throw new Error('The task is no longer present in the monitor.')
    const previousPipelineId = hasOwn(current.data, 'pipeline_id') ? asString(current.data.pipeline_id, task.pipeline_id) : task.pipeline_id
    const previousParentId = hasOwn(current.data, 'parent_id') ? asNullableString(current.data.parent_id) : task.parent_id ?? null
    const data = { ...current.data, ...patch }
    const pipelineId = hasOwn(data, 'pipeline_id') ? asString(data.pipeline_id, task.pipeline_id) : task.pipeline_id
    const parentId = hasOwn(data, 'parent_id') ? asNullableString(data.parent_id) : task.parent_id
    next[updateIndex] = {
      ...withHumanEdit(current, 'task fields changed', data),
      prerequisite_operation_ids: refreshedCreatedPrerequisites(
        next,
        current,
        previousPipelineId,
        previousParentId,
        pipelineId,
        parentId,
      ),
    }
    assertCrossPipelineMoveOrderSafe(snapshot, next, taskId, current.id)
    return next
  }
  const task = snapshot.tasks.find((item) => item.id === taskId)
  if (!task) throw new Error('The task is no longer present in the monitor.')
  const pipelineId = hasOwn(patch, 'pipeline_id') ? patch.pipeline_id ?? task.pipeline_id : task.pipeline_id
  const parentId = hasOwn(patch, 'parent_id') ? patch.parent_id ?? null : task.parent_id
  const operationId = idFactory()
  next.push({
    id: operationId,
    type: 'task.update',
    entity_id: task.id,
    expected_version: task.version,
    data: { ...patch },
    rationale: 'Edited during graphical proposal review.',
    confidence: 1,
    evidence: [humanEvidence()],
    source_references: [],
    prerequisite_operation_ids: createdPrerequisites(next, pipelineId, parentId),
  })
  assertCrossPipelineMoveOrderSafe(snapshot, next, taskId, operationId)
  return next
}

export function updateStagedPipeline(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  pipelineId: string,
  patch: PipelineStagePatch,
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  const next = operations.map(cleanProposalOperation)
  const createIndex = next.findIndex((operation) => operation.type === 'pipeline.create' && proposalOperationEntityId(operation) === pipelineId)
  if (createIndex >= 0) {
    next[createIndex] = withHumanEdit(next[createIndex], 'pipeline fields changed', { ...next[createIndex].data, ...patch })
    return next
  }
  const updateIndex = next.findIndex((operation) => operation.type === 'pipeline.update' && proposalOperationEntityId(operation) === pipelineId)
  if (updateIndex >= 0) {
    next[updateIndex] = withHumanEdit(next[updateIndex], 'pipeline fields changed', { ...next[updateIndex].data, ...patch })
    return next
  }
  const pipeline = snapshot.pipelines.find((item) => item.id === pipelineId)
  if (!pipeline) throw new Error('The pipeline is no longer present in the monitor.')
  next.push({
    id: idFactory(),
    type: 'pipeline.update',
    entity_id: pipeline.id,
    expected_version: pipeline.version,
    data: { ...patch },
    rationale: 'Edited during graphical proposal review.',
    confidence: 1,
    evidence: [humanEvidence()],
    source_references: [],
    prerequisite_operation_ids: [],
  })
  return next
}

export function addStagedPipeline(
  operations: ProposalOperation[],
  title: string,
  position: number,
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  const entityId = idFactory()
  return [...operations.map(cleanProposalOperation), {
    id: idFactory(),
    type: 'pipeline.create',
    data: { id: entityId, title: title.trim(), flow_mode: 'freeform', position },
    rationale: 'Added during graphical proposal review.',
    confidence: 1,
    evidence: [humanEvidence()],
    source_references: [],
    prerequisite_operation_ids: [],
  }]
}

export function moveStagedPipeline(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  pipelineId: string,
  action: 'up' | 'down',
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  const pipelines = buildProjectedPipelineOrder(snapshot, operations)
  const pipeline = pipelines.find((item) => item.id === pipelineId)
  if (!pipeline) throw new Error('The pipeline is no longer present in the staged outline.')
  const index = pipelines.findIndex((item) => item.id === pipelineId)
  let position = pipeline.position
  if (action === 'up' && index > 0) {
    const previous = pipelines[index - 1]
    const before = pipelines[index - 2]
    position = before ? (before.position + previous.position) / 2 : previous.position - 1
  } else if (action === 'down' && index >= 0 && index < pipelines.length - 1) {
    const next = pipelines[index + 1]
    const after = pipelines[index + 2]
    position = after ? (next.position + after.position) / 2 : next.position + 1
  } else {
    return operations
  }
  return updateStagedPipeline(operations, snapshot, pipelineId, { position }, idFactory)
}

export function removeStagedPipeline(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  pipelineId: string,
): ProposalOperation[] {
  const stage = buildProposalStage(snapshot, operations)
  const isExisting = snapshot.pipelines.some((pipeline) => pipeline.id === pipelineId)
  const removedTaskIds = new Set(
    stage.tasks
      .filter((task) => task.pipeline_id === pipelineId && !snapshot.tasks.some((item) => item.id === task.id))
      .map((task) => task.id),
  )
  const removedOperationIds = new Set(operations.filter((operation) => {
    const entityId = proposalOperationEntityId(operation)
    if (isExisting) return operation.type === 'pipeline.update' && entityId === pipelineId
    if (operation.type.startsWith('pipeline.') && entityId === pipelineId) return true
    if (!operation.type.startsWith('task.') || entityId == null) return false
    return stage.taskById.get(entityId)?.pipeline_id === pipelineId
  }).map((operation) => operation.id))
  const removedGroups = new Set(
    operations
      .filter((operation) => removedOperationIds.has(operation.id) && operation.atomic_group_id)
      .map((operation) => operation.atomic_group_id),
  )
  let changed = true
  while (changed) {
    changed = false
    operations.forEach((operation) => {
      const referencesRemovedEntity = !isExisting && (
        operation.data.pipeline_id === pipelineId
        || ['task_id', 'source_task_id', 'target_task_id', 'parent_id']
          .some((field) => typeof operation.data[field] === 'string' && removedTaskIds.has(String(operation.data[field])))
      )
      const dependsOnRemoved = operation.prerequisite_operation_ids?.some((id) => removedOperationIds.has(id))
      const groupedWithRemoved = operation.atomic_group_id && removedGroups.has(operation.atomic_group_id)
      if (!removedOperationIds.has(operation.id) && (referencesRemovedEntity || dependsOnRemoved || groupedWithRemoved)) {
        removedOperationIds.add(operation.id)
        if (operation.atomic_group_id) removedGroups.add(operation.atomic_group_id)
        changed = true
      }
    })
  }
  return operations.filter((operation) => !removedOperationIds.has(operation.id)).map(cleanProposalOperation)
}

export function addStagedTask(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  pipelineId: string,
  parentId: string | null,
  title: string,
  position: number,
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  if (!title.trim()) throw new Error('Task title is required.')
  if (parentId && snapshot.tasks.some((task) => task.id === parentId)) {
    assertDescendantEditSafe(snapshot, operations, parentId, true)
  }
  const entityId = idFactory()
  return [...operations.map(cleanProposalOperation), {
    id: idFactory(),
    type: 'task.create',
    data: {
      id: entityId,
      pipeline_id: pipelineId,
      parent_id: parentId,
      title: title.trim(),
      kind: 'task',
      status: 'planned',
      priority: 'required',
      labels: [],
      position,
      child_flow_mode: 'freeform',
    },
    rationale: 'Added during graphical proposal review.',
    confidence: 1,
    evidence: [humanEvidence()],
    source_references: [],
    prerequisite_operation_ids: createdPrerequisites(operations, pipelineId, parentId),
  }]
}

export function splitStagedTask(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  taskId: string,
  titles: string[],
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  const stage = buildProposalStage(snapshot, operations)
  const task = stage.taskById.get(taskId)
  if (!task) throw new Error('The task is no longer present in the staged outline.')
  if (task.status === 'done') throw new Error('Change this task from Done before splitting it into unfinished subtasks.')
  const hasExistingChild = snapshot.tasks.some((item) => item.parent_id === taskId && !item.deleted_at)
  const hasStagedChild = (stage.children.get(taskId) ?? []).length > 0
  if (hasExistingChild || hasStagedChild) throw new Error('This task already has children. Split is available only for leaf tasks.')
  const values = titles.map((title) => title.trim()).filter(Boolean)
  if (values.length < 2) throw new Error('Enter at least two nonblank subtask titles.')
  let next = updateStagedTask(operations, snapshot, taskId, { kind: 'milestone', child_flow_mode: 'freeform' }, idFactory)
  values.forEach((title, index) => {
    next = addStagedTask(next, snapshot, task.pipeline_id, task.id, title, index, idFactory)
  })
  return next
}

function descendantIds(stage: ProposalStage, taskId: string): Set<string> {
  const result = new Set<string>()
  const visit = (id: string) => {
    if (result.has(id)) return
    result.add(id)
    ;(stage.children.get(id) ?? []).forEach((child) => visit(child.id))
  }
  visit(taskId)
  return result
}

export function removeStagedTask(operations: ProposalOperation[], snapshot: ProjectSnapshot, taskId: string): ProposalOperation[] {
  const stage = buildProposalStage(snapshot, operations)
  const removesProposedEntities = !snapshot.tasks.some((task) => task.id === taskId)
  const ids = removesProposedEntities ? descendantIds(stage, taskId) : new Set([taskId])
  const removedOperationIds = new Set(operations.filter((operation) => {
    const entityId = proposalOperationEntityId(operation)
    return entityId != null && ids.has(entityId) && operation.type.startsWith('task.')
  }).map((operation) => operation.id))
  const removedGroups = new Set(operations.filter((operation) => removedOperationIds.has(operation.id) && operation.atomic_group_id).map((operation) => operation.atomic_group_id))
  let changed = true
  while (changed) {
    changed = false
    operations.forEach((operation) => {
      const referencesRemovedTask = removesProposedEntities && ['task_id', 'source_task_id', 'target_task_id', 'parent_id']
        .some((field) => typeof operation.data[field] === 'string' && ids.has(String(operation.data[field])))
      const dependsOnRemoved = operation.prerequisite_operation_ids?.some((id) => removedOperationIds.has(id))
      const groupedWithRemoved = operation.atomic_group_id && removedGroups.has(operation.atomic_group_id)
      if (!removedOperationIds.has(operation.id) && (referencesRemovedTask || dependsOnRemoved || groupedWithRemoved)) {
        removedOperationIds.add(operation.id)
        if (operation.atomic_group_id) removedGroups.add(operation.atomic_group_id)
        changed = true
      }
    })
  }
  return operations.filter((operation) => !removedOperationIds.has(operation.id)).map(cleanProposalOperation)
}

export function moveStagedTask(
  operations: ProposalOperation[],
  snapshot: ProjectSnapshot,
  taskId: string,
  action: 'up' | 'down' | 'indent' | 'outdent',
  idFactory: () => string = () => crypto.randomUUID(),
): ProposalOperation[] {
  const stage = buildProposalStage(snapshot, operations)
  const task = stage.taskById.get(taskId)
  if (!task) throw new Error('The task is no longer present in the staged outline.')
  const key = task.parent_id ?? `pipeline:${task.pipeline_id}`
  const siblings = stage.children.get(key) ?? []
  const index = siblings.findIndex((item) => item.id === taskId)
  let parentId = task.parent_id ?? null
  let position = task.position
  if (action === 'up' && index > 0) {
    const previous = siblings[index - 1]
    const before = siblings[index - 2]
    position = before ? (before.position + previous.position) / 2 : previous.position - 1
  } else if (action === 'down' && index >= 0 && index < siblings.length - 1) {
    const next = siblings[index + 1]
    const after = siblings[index + 2]
    position = after ? (next.position + after.position) / 2 : next.position + 1
  } else if (action === 'indent' && index > 0) {
    const previous = siblings[index - 1]
    if (descendantIds(stage, taskId).has(previous.id)) throw new Error('That move would create a hierarchy cycle.')
    parentId = previous.id
    const nested = stage.children.get(previous.id) ?? []
    position = nested.length ? nested[nested.length - 1].position + 1 : 0
  } else if (action === 'outdent' && task.parent_id) {
    const parent = stage.taskById.get(task.parent_id)
    if (!parent) throw new Error('The staged parent is unavailable.')
    parentId = parent.parent_id ?? null
    const parentSiblings = stage.children.get(parent.parent_id ?? `pipeline:${parent.pipeline_id}`) ?? []
    const parentIndex = parentSiblings.findIndex((item) => item.id === parent.id)
    const afterParent = parentSiblings[parentIndex + 1]
    position = afterParent ? (parent.position + afterParent.position) / 2 : parent.position + 1
  } else {
    return operations
  }
  return updateStagedTask(operations, snapshot, taskId, { parent_id: parentId, pipeline_id: task.pipeline_id, position }, idFactory)
}

export function operationsChanged(original: ProposalOperation[], staged: ProposalOperation[]) {
  return JSON.stringify(original.map(cleanProposalOperation)) !== JSON.stringify(staged.map(cleanProposalOperation))
}

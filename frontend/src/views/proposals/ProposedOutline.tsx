import { useEffect, useMemo, useState, type FormEvent } from 'react'
import {
  ArrowDown,
  ArrowLeftFromLine,
  ArrowRightFromLine,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Edit3,
  GitBranch,
  ListChecks,
  Milestone,
  Plus,
  Scissors,
  Trash2,
  Undo2,
} from 'lucide-react'
import type {
  Pipeline,
  ProjectSnapshot,
  ProposalOperation,
  TaskKind,
  TaskOutcome,
  TaskPriority,
  TaskStatus,
} from '../../types'
import { TASK_KINDS, TASK_OUTCOMES, TASK_PRIORITIES, TASK_STATUSES } from '../../types'
import { humanize } from '../../lib/format'
import { requestCloseWithUnsavedChanges } from '../../lib/unsavedChanges'
import { Badge, Button, Dialog, Field, Notice } from '../../components/ui'
import {
  addStagedPipeline,
  addStagedTask,
  buildProposalFocusProjection,
  buildProjectedPipelineOrder,
  buildProposalStage,
  moveStagedPipeline,
  moveStagedTask,
  nextStagedPipelinePosition,
  proposalOperationEntityId,
  removeStagedPipeline,
  removeStagedTask,
  splitStagedTask,
  updateStagedPipeline,
  updateStagedTask,
  type ProposalStage,
  type StagePipeline,
  type StageTask,
  type TaskStagePatch,
} from './staging'

export interface ProposedOutlineProps {
  snapshot: ProjectSnapshot
  operations: ProposalOperation[]
  onChange: (operations: ProposalOperation[]) => void
  disabled?: boolean
}

type PipelineDialogState = { open: boolean; pipelineId?: string }
type TaskDialogState = {
  open: boolean
  taskId?: string
  pipelineId?: string
  parentId?: string | null
}
type SplitDialogState = { open: boolean; taskId?: string }

const safeBatchStatuses: TaskStatus[] = ['planned', 'in_progress', 'review', 'dropped']

export function ProposedOutline({ snapshot, operations, onChange, disabled = false }: ProposedOutlineProps) {
  const stage = useMemo(() => buildProposalStage(snapshot, operations), [snapshot, operations])
  const focusProjection = useMemo(
    () => buildProposalFocusProjection(stage, snapshot, operations),
    [stage, snapshot, operations],
  )
  const projectedPipelineOrder = useMemo(
    () => buildProjectedPipelineOrder(snapshot, operations),
    [snapshot, operations],
  )
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [fullContextPipelineIds, setFullContextPipelineIds] = useState<Set<string>>(new Set())
  const [selectedTaskIds, setSelectedTaskIds] = useState<Set<string>>(new Set())
  const [batchPriority, setBatchPriority] = useState<TaskPriority | ''>('')
  const [batchStatus, setBatchStatus] = useState<TaskStatus | ''>('')
  const [pipelineDialog, setPipelineDialog] = useState<PipelineDialogState>({ open: false })
  const [taskDialog, setTaskDialog] = useState<TaskDialogState>({ open: false })
  const [splitDialog, setSplitDialog] = useState<SplitDialogState>({ open: false })
  const [error, setError] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('')

  const projectedTasks = useMemo(() => {
    const tasks = new Map(snapshot.tasks.filter((task) => !task.deleted_at).map((task) => [task.id, task]))
    stage.tasks.forEach((task) => tasks.set(task.id, task))
    return [...tasks.values()]
  }, [snapshot.tasks, stage.tasks])

  const visibleTaskIds = useMemo(() => {
    const visible = new Set(focusProjection.visibleTaskIds)
    stage.tasks.forEach((task) => {
      if (fullContextPipelineIds.has(task.pipeline_id)) visible.add(task.id)
    })
    return visible
  }, [focusProjection.visibleTaskIds, fullContextPipelineIds, stage.tasks])
  const visibleTasks = useMemo(
    () => stage.tasks.filter((task) => visibleTaskIds.has(task.id)),
    [stage.tasks, visibleTaskIds],
  )
  const selectedVisibleTaskCount = visibleTasks.filter((task) => selectedTaskIds.has(task.id)).length

  useEffect(() => {
    const available = visibleTaskIds
    setSelectedTaskIds((current) => {
      const next = new Set([...current].filter((id) => available.has(id)))
      return next.size === current.size ? current : next
    })
  }, [visibleTaskIds])

  const commit = (next: ProposalOperation[], message: string) => {
    if (disabled) return
    setError(null)
    setAnnouncement(message)
    onChange(next)
  }

  const attempt = (change: () => ProposalOperation[], message: string) => {
    try {
      commit(change(), message)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The staged outline could not be updated.')
    }
  }

  const toggleCollapsed = (id: string) => {
    setCollapsed((current) => {
      const next = new Set(current)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleSelected = (id: string) => {
    setSelectedTaskIds((current) => {
      const next = new Set(current)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const allSelected = visibleTasks.length > 0 && visibleTasks.every((task) => selectedTaskIds.has(task.id))
  const toggleAll = () => {
    setSelectedTaskIds(allSelected ? new Set() : new Set(visibleTasks.map((task) => task.id)))
  }

  const applyBatchPatch = (patch: TaskStagePatch, label: string) => {
    const selectedVisibleTaskIds = [...selectedTaskIds].filter((id) => visibleTaskIds.has(id))
    if (!selectedVisibleTaskIds.length) {
      setError('Select at least one task in the proposed outline first.')
      return
    }
    attempt(() => {
      let next = operations
      selectedVisibleTaskIds.forEach((taskId) => {
        next = updateStagedTask(next, snapshot, taskId, patch)
      })
      return next
    }, `${label} updated for ${selectedVisibleTaskIds.length} staged task${selectedVisibleTaskIds.length === 1 ? '' : 's'}.`)
  }

  const removeOrRevert = (task: StageTask) => {
    const isNew = !snapshot.tasks.some((item) => item.id === task.id)
    const prompt = isNew
      ? 'Remove the proposed task “' + task.title + '” and its staged subtree?'
      : 'Revert the proposed changes for “' + task.title + '” and any staged operations that depend on them?'
    if (!window.confirm(prompt)) return
    attempt(
      () => removeStagedTask(operations, snapshot, task.id),
      isNew ? `Removed proposed task “${task.title}” and its staged subtree.` : `Reverted proposed changes for “${task.title}”.`,
    )
  }

  const removeOrRevertPipeline = (pipeline: StagePipeline) => {
    const isNew = !snapshot.pipelines.some((item) => item.id === pipeline.id)
    const prompt = isNew
      ? 'Remove the proposed pipeline “' + pipeline.title + '”, its staged tasks, and dependent operations?'
      : 'Revert the staged changes for pipeline “' + pipeline.title + '”? Independent task operations will be preserved.'
    if (!window.confirm(prompt)) return
    attempt(
      () => removeStagedPipeline(operations, snapshot, pipeline.id),
      isNew ? `Removed proposed pipeline “${pipeline.title}” and its staged work.` : `Reverted staged pipeline changes for “${pipeline.title}”.`,
    )
  }

  return (
    <section className="proposed-outline" aria-label="Graphical proposal staging">
      <header className="proposed-outline-heading">
        <div>
          <span className="proposed-outline-kicker"><ListChecks size={14} aria-hidden="true" /> Proposed Outline</span>
          <h4>Shape the draft before choosing operations to apply</h4>
          <p>Affected work is shown with nearby context by default. Expand a pipeline explicitly when you need its full active hierarchy. These edits remain inside the proposal until you save a replacement draft.</p>
        </div>
        <Button type="button" size="sm" onClick={() => setPipelineDialog({ open: true })} disabled={disabled}>
          <Plus size={14} /> Add pipeline
        </Button>
      </header>

      <div className="proposed-batch-bar" aria-label="Batch edit staged tasks">
        <label className="proposed-select-all">
          <input type="checkbox" checked={allSelected} onChange={toggleAll} disabled={disabled || !visibleTasks.length} />
          <span>{allSelected ? 'Clear visible selection' : 'Select all visible tasks'}</span>
        </label>
        <span className="proposed-selection-count">{selectedVisibleTaskCount} of {visibleTasks.length} visible tasks selected for batch editing</span>
        <div className="proposed-batch-control">
          <label htmlFor="proposed-batch-priority">Priority</label>
          <select id="proposed-batch-priority" value={batchPriority} onChange={(event) => setBatchPriority(event.target.value as TaskPriority | '')} disabled={disabled}>
            <option value="">Choose…</option>
            {TASK_PRIORITIES.map((priority) => <option key={priority} value={priority}>{humanize(priority)}</option>)}
          </select>
          <Button type="button" size="sm" variant="secondary" disabled={disabled || !batchPriority || !selectedVisibleTaskCount} onClick={() => batchPriority && applyBatchPatch({ priority: batchPriority }, 'Priority')}>Apply</Button>
        </div>
        <div className="proposed-batch-control">
          <label htmlFor="proposed-batch-status">Safe status</label>
          <select id="proposed-batch-status" value={batchStatus} onChange={(event) => setBatchStatus(event.target.value as TaskStatus | '')} disabled={disabled}>
            <option value="">Choose…</option>
            {safeBatchStatuses.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}
          </select>
          <Button type="button" size="sm" variant="secondary" disabled={disabled || !batchStatus || !selectedVisibleTaskCount} onClick={() => batchStatus && applyBatchPatch({ status: batchStatus }, 'Status')}>Apply</Button>
        </div>
      </div>
      <p className="proposed-batch-hint">Batch selection is only for graphical editing. It does not select proposal operations for approval. Blocked and Done require individual review.</p>

      {error && <Notice tone="danger">{error}</Notice>}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement}</div>

      {stage.pipelines.length ? (
        <ul className="proposed-tree" aria-label="Affected proposal pipelines and tasks">
          {stage.pipelines.map((pipeline) => {
            const allRoots = stage.children.get(`pipeline:${pipeline.id}`) ?? []
            const roots = allRoots.filter((task) => visibleTaskIds.has(task.id))
            const isCollapsed = collapsed.has(`pipeline:${pipeline.id}`)
            const isFullContext = fullContextPipelineIds.has(pipeline.id)
            const focusedHiddenCount = focusProjection.hiddenTaskCountByPipeline.get(pipeline.id) ?? 0
            const activeTaskCount = stage.tasks.filter((task) => task.pipeline_id === pipeline.id).length
            const projectOrderIndex = projectedPipelineOrder.findIndex((item) => item.id === pipeline.id)
            const isNew = !snapshot.pipelines.some((item) => item.id === pipeline.id)
            const canRemoveOrRevert = isNew || pipeline.operationIds.some((operationId) => (
              operations.some((operation) => operation.id === operationId && operation.type === 'pipeline.update')
            ))
            const groupId = `proposed-pipeline-${pipeline.id}-tasks`
            return (
              <li
                className={`proposed-pipeline${pipeline.proposed ? '' : ' is-context'}`}
                key={pipeline.id}
              >
                <div className="proposed-pipeline-row">
                  <button
                    type="button"
                    className="proposed-collapse"
                    onClick={() => toggleCollapsed(`pipeline:${pipeline.id}`)}
                    aria-expanded={!isCollapsed}
                    aria-controls={groupId}
                    aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${pipeline.title}`}
                  >
                    {isCollapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
                  </button>
                  <span className="proposed-pipeline-icon"><GitBranch size={16} aria-hidden="true" /></span>
                  <div className="proposed-pipeline-copy">
                    <span>
                      <strong>{pipeline.title}</strong>
                      {pipeline.proposed && <Badge tone={isNew ? 'green' : 'purple'}>{isNew ? 'New' : 'Changed'}</Badge>}
                      {!pipeline.proposed && <Badge tone="muted">Context</Badge>}
                    </span>
                    <small>{humanize(pipeline.flow_mode)} flow · {allRoots.length} active root task{allRoots.length === 1 ? '' : 's'} · project order {projectOrderIndex + 1} of {projectedPipelineOrder.length}</small>
                  </div>
                  <div className="proposed-pipeline-actions">
                    <Button type="button" size="icon" variant="ghost" onClick={() => attempt(() => moveStagedPipeline(operations, snapshot, pipeline.id, 'up'), `Moved pipeline “${pipeline.title}” one place earlier in the full project order.`)} disabled={disabled || projectOrderIndex <= 0} aria-label={`Move pipeline ${pipeline.title} up`} title="Move one place earlier in full project order"><ArrowUp size={14} /></Button>
                    <Button type="button" size="icon" variant="ghost" onClick={() => attempt(() => moveStagedPipeline(operations, snapshot, pipeline.id, 'down'), `Moved pipeline “${pipeline.title}” one place later in the full project order.`)} disabled={disabled || projectOrderIndex < 0 || projectOrderIndex >= projectedPipelineOrder.length - 1} aria-label={`Move pipeline ${pipeline.title} down`} title="Move one place later in full project order"><ArrowDown size={14} /></Button>
                    <Button type="button" size="icon" variant="ghost" onClick={() => setPipelineDialog({ open: true, pipelineId: pipeline.id })} disabled={disabled} aria-label={`Edit pipeline ${pipeline.title}`}><Edit3 size={14} /></Button>
                    <Button type="button" size="sm" variant="secondary" onClick={() => setTaskDialog({ open: true, pipelineId: pipeline.id, parentId: null })} disabled={disabled}><Plus size={14} /> Root task</Button>
                    {canRemoveOrRevert && (
                      <Button type="button" size="icon" variant={isNew ? 'danger' : 'ghost'} onClick={() => removeOrRevertPipeline(pipeline)} disabled={disabled} aria-label={isNew ? `Remove proposed pipeline ${pipeline.title}` : `Revert proposed changes for pipeline ${pipeline.title}`} title={isNew ? 'Remove proposed pipeline' : 'Revert proposed pipeline changes'}>
                        {isNew ? <Trash2 size={14} /> : <Undo2 size={14} />}
                      </Button>
                    )}
                  </div>
                </div>
                {focusedHiddenCount > 0 && (
                  <div className="proposed-focus-summary" role="status" aria-live="polite">
                    <span>{isFullContext
                      ? `All ${activeTaskCount} active task${activeTaskCount === 1 ? '' : 's'} shown.`
                      : `${focusedHiddenCount} active task${focusedHiddenCount === 1 ? '' : 's'} hidden from focused context.`}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-expanded={isFullContext}
                      aria-controls={groupId}
                      onClick={() => setFullContextPipelineIds((current) => {
                        const next = new Set(current)
                        next.has(pipeline.id) ? next.delete(pipeline.id) : next.add(pipeline.id)
                        return next
                      })}
                    >
                      {isFullContext ? 'Show focused context' : 'Show full pipeline context'}
                    </Button>
                  </div>
                )}
                {roots.length ? (
                  <ul id={groupId} className="proposed-task-group" hidden={isCollapsed}>
                    {roots.map((task) => (
                      <ProposedTaskRow
                        key={task.id}
                        task={task}
                        depth={0}
                        stage={stage}
                        snapshot={snapshot}
                        collapsed={collapsed}
                        selected={selectedTaskIds}
                        visibleTaskIds={visibleTaskIds}
                        disabled={disabled}
                        onToggleCollapsed={toggleCollapsed}
                        onToggleSelected={toggleSelected}
                        onEdit={(item) => setTaskDialog({ open: true, taskId: item.id })}
                        onAddChild={(item) => setTaskDialog({ open: true, pipelineId: item.pipeline_id, parentId: item.id })}
                        onSplit={(item) => setSplitDialog({ open: true, taskId: item.id })}
                        onMove={(item, action) => attempt(() => moveStagedTask(operations, snapshot, item.id, action), `${humanize(action)} applied to “${item.title}”.`)}
                        onRemove={removeOrRevert}
                      />
                    ))}
                  </ul>
                ) : <p id={groupId} className="proposed-pipeline-empty" hidden={isCollapsed}>No active tasks yet. Add a root task to this staged pipeline.</p>}
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="proposed-outline-empty">
          <GitBranch size={22} aria-hidden="true" />
          <div><strong>No task structure in this proposal</strong><p>This draft may contain only artifacts, journals, or dependencies. You can add a pipeline to begin shaping a task outline.</p></div>
        </div>
      )}

      <PipelineStageDialog
        open={pipelineDialog.open}
        pipeline={pipelineDialog.pipelineId ? stage.pipelineById.get(pipelineDialog.pipelineId) : undefined}
        stage={stage}
        snapshot={snapshot}
        operations={operations}
        disabled={disabled}
        onClose={() => setPipelineDialog({ open: false })}
        onCommit={commit}
      />
      <TaskStageDialog
        state={taskDialog}
        stage={stage}
        snapshot={snapshot}
        operations={operations}
        projectedTasks={projectedTasks}
        disabled={disabled}
        onClose={() => setTaskDialog({ open: false })}
        onCommit={commit}
      />
      <SplitStageDialog
        open={splitDialog.open}
        task={splitDialog.taskId ? stage.taskById.get(splitDialog.taskId) : undefined}
        snapshot={snapshot}
        operations={operations}
        disabled={disabled}
        onClose={() => setSplitDialog({ open: false })}
        onCommit={commit}
      />
    </section>
  )
}

function ProposedTaskRow({
  task,
  depth,
  stage,
  snapshot,
  collapsed,
  selected,
  visibleTaskIds,
  disabled,
  onToggleCollapsed,
  onToggleSelected,
  onEdit,
  onAddChild,
  onSplit,
  onMove,
  onRemove,
}: {
  task: StageTask
  depth: number
  stage: ProposalStage
  snapshot: ProjectSnapshot
  collapsed: Set<string>
  selected: Set<string>
  visibleTaskIds: Set<string>
  disabled: boolean
  onToggleCollapsed: (id: string) => void
  onToggleSelected: (id: string) => void
  onEdit: (task: StageTask) => void
  onAddChild: (task: StageTask) => void
  onSplit: (task: StageTask) => void
  onMove: (task: StageTask, action: 'up' | 'down' | 'indent' | 'outdent') => void
  onRemove: (task: StageTask) => void
}) {
  const allChildren = stage.children.get(task.id) ?? []
  const children = allChildren.filter((child) => visibleTaskIds.has(child.id))
  const hasAnyChildren = allChildren.length > 0 || snapshot.tasks.some((item) => item.parent_id === task.id && !item.deleted_at)
  const siblings = stage.children.get(task.parent_id ?? `pipeline:${task.pipeline_id}`) ?? []
  const index = siblings.findIndex((item) => item.id === task.id)
  const isCollapsed = collapsed.has(task.id)
  const isNew = !snapshot.tasks.some((item) => item.id === task.id)
  const canIndent = index > 0
  const canOutdent = Boolean(task.parent_id)

  return (
    <li className={`proposed-task${task.proposed ? '' : ' is-context'}`}>
      <div className="proposed-task-row" style={{ paddingLeft: `${10 + depth * 24}px` }}>
        {children.length ? (
          <button
            type="button"
            className="proposed-collapse"
            onClick={() => onToggleCollapsed(task.id)}
            aria-expanded={!isCollapsed}
            aria-controls={`proposed-task-${task.id}-children`}
            aria-label={`${isCollapsed ? 'Expand' : 'Collapse'} ${task.title}`}
          >
            {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          </button>
        ) : <span className="proposed-collapse invisible" aria-hidden="true" />}
        <label className="proposed-task-checkbox" title="Select for batch editing">
          <input type="checkbox" checked={selected.has(task.id)} onChange={() => onToggleSelected(task.id)} disabled={disabled} aria-label={`Select ${task.title} for batch editing`} />
        </label>
        <span className="proposed-task-kind" aria-hidden="true">{task.kind === 'task' ? <ListChecks size={14} /> : <Milestone size={14} />}</span>
        <button type="button" className="proposed-task-copy" onClick={() => onEdit(task)} disabled={disabled} aria-label={`Edit task ${task.title}`}>
          <span>
            {task.user_key && <code>{task.user_key}</code>}
            <strong>{task.title}</strong>
            {task.proposed && <Badge tone={isNew ? 'green' : 'purple'}>{isNew ? 'New' : 'Changed'}</Badge>}
            {!task.proposed && <Badge tone="muted">Context</Badge>}
          </span>
          <small>{humanize(task.status)} · {humanize(task.priority)} · {humanize(task.kind)}</small>
        </button>
        <div className="proposed-task-actions">
          <Button type="button" size="icon" variant="ghost" onClick={() => onMove(task, 'up')} disabled={disabled || index <= 0} aria-label={`Move ${task.title} up`} title="Move up"><ArrowUp size={14} /></Button>
          <Button type="button" size="icon" variant="ghost" onClick={() => onMove(task, 'down')} disabled={disabled || index < 0 || index >= siblings.length - 1} aria-label={`Move ${task.title} down`} title="Move down"><ArrowDown size={14} /></Button>
          <Button type="button" size="icon" variant="ghost" onClick={() => onMove(task, 'outdent')} disabled={disabled || !canOutdent} aria-label={`Outdent ${task.title}`} title="Outdent"><ArrowLeftFromLine size={14} /></Button>
          <Button type="button" size="icon" variant="ghost" onClick={() => onMove(task, 'indent')} disabled={disabled || !canIndent} aria-label={`Indent ${task.title}`} title="Indent"><ArrowRightFromLine size={14} /></Button>
          <Button type="button" size="icon" variant="ghost" onClick={() => onAddChild(task)} disabled={disabled} aria-label={`Add child task to ${task.title}`} title="Add child"><Plus size={14} /></Button>
          <Button type="button" size="icon" variant="ghost" onClick={() => onSplit(task)} disabled={disabled || task.status === 'done' || hasAnyChildren} aria-label={`Split ${task.title} into subtasks`} title={hasAnyChildren ? 'Task already has children' : 'Split into subtasks'}><Scissors size={14} /></Button>
          <Button type="button" size="icon" variant="ghost" onClick={() => onEdit(task)} disabled={disabled} aria-label={`Edit ${task.title}`} title="Edit"><Edit3 size={14} /></Button>
          {task.proposed && (
            <Button type="button" size="icon" variant={isNew ? 'danger' : 'ghost'} onClick={() => onRemove(task)} disabled={disabled} aria-label={isNew ? `Remove proposed task ${task.title}` : `Revert proposed changes for ${task.title}`} title={isNew ? 'Remove proposed task' : 'Revert proposed changes'}>
              {isNew ? <Trash2 size={14} /> : <Undo2 size={14} />}
            </Button>
          )}
        </div>
      </div>
      {children.length > 0 && (
        <ul id={`proposed-task-${task.id}-children`} className="proposed-task-children" hidden={isCollapsed}>
          {children.map((child) => (
            <ProposedTaskRow
              key={child.id}
              task={child}
              depth={depth + 1}
              stage={stage}
              snapshot={snapshot}
              collapsed={collapsed}
              selected={selected}
              visibleTaskIds={visibleTaskIds}
              disabled={disabled}
              onToggleCollapsed={onToggleCollapsed}
              onToggleSelected={onToggleSelected}
              onEdit={onEdit}
              onAddChild={onAddChild}
              onSplit={onSplit}
              onMove={onMove}
              onRemove={onRemove}
            />
          ))}
        </ul>
      )}
    </li>
  )
}

function changedStageFields<TPatch extends object>(current: object, patch: TPatch): TPatch {
  const result: Partial<TPatch> = {}
  const currentValues = current as Record<string, unknown>
  ;(Object.keys(patch) as Array<keyof TPatch>).forEach((key) => {
    const previous = currentValues[String(key)]
    const next = patch[key]
    const equal = previous == null && next == null ? true : JSON.stringify(previous) === JSON.stringify(next)
    if (!equal) Object.assign(result, { [key]: next })
  })
  return result as TPatch
}

function PipelineStageDialog
({ open, pipeline, stage, snapshot, operations, disabled, onClose, onCommit }: {
  open: boolean
  pipeline?: StagePipeline
  stage: ProposalStage
  snapshot: ProjectSnapshot
  operations: ProposalOperation[]
  disabled: boolean
  onClose: () => void
  onCommit: (operations: ProposalOperation[], message: string) => void
}) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [flowMode, setFlowMode] = useState<'sequential' | 'freeform'>('sequential')
  const [formError, setFormError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setTitle(pipeline?.title ?? '')
    setDescription(pipeline?.description ?? '')
    setFlowMode(pipeline?.flow_mode ?? 'sequential')
    setFormError(null)
  }, [open, pipeline?.id])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (disabled) return
    if (!title.trim()) {
      setFormError('Pipeline title is required.')
      return
    }
    try {
      let next: ProposalOperation[]
      if (pipeline) {
        const desired = { title: title.trim(), description: description.trim() || null, flow_mode: flowMode }
        const patch = changedStageFields(pipeline, desired)
        if (!Object.keys(patch).length) {
          onClose()
          return
        }
        next = updateStagedPipeline(operations, snapshot, pipeline.id, patch)
      } else {
        const position = nextStagedPipelinePosition(snapshot, operations)
        next = addStagedPipeline(operations, title, position)
        const previousIds = new Set(stage.pipelines.map((item) => item.id))
        const created = [...next].reverse().find((operation) => operation.type === 'pipeline.create' && !previousIds.has(proposalOperationEntityId(operation) ?? ''))
        const pipelineId = created ? proposalOperationEntityId(created) : null
        if (!pipelineId) throw new Error('The new staged pipeline could not be identified.')
        next = updateStagedPipeline(next, snapshot, pipelineId, { description: description.trim() || null, flow_mode: flowMode })
      }
      onCommit(next, pipeline ? `Updated staged pipeline “${title.trim()}”.` : `Added staged pipeline “${title.trim()}”.`)
      onClose()
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : 'The staged pipeline could not be saved.')
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={pipeline ? 'Edit proposed pipeline' : 'Add proposed pipeline'} description="This changes only the proposal draft until it is reviewed and applied.">
      <form className="form-stack" onSubmit={submit}>
        <Field label="Title"><input required value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Evaluation and analysis" /></Field>
        <Field label="Description"><textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Purpose and scope of this workstream…" /></Field>
        <Field label="Task flow"><select value={flowMode} onChange={(event) => setFlowMode(event.target.value as typeof flowMode)}><option value="sequential">Sequential — sibling order creates precedence</option><option value="freeform">Freeform — no derived sibling precedence</option></select></Field>
        {formError && <Notice tone="danger">{formError}</Notice>}
        <div className="dialog-actions"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" disabled={disabled}>{pipeline ? 'Save pipeline changes' : 'Add pipeline'}</Button></div>
      </form>
    </Dialog>
  )
}

type TaskDraft = {
  title: string
  userKey: string
  description: string
  kind: TaskKind
  status: TaskStatus
  outcome: TaskOutcome | ''
  priority: TaskPriority
  labels: string
  targetDate: string
  completionCriteria: string
  blockerReason: string
  completionSummary: string
  completionOverrideReason: string
  childFlowMode: 'sequential' | 'freeform'
}

function taskDraft(task?: StageTask): TaskDraft {
  return {
    title: task?.title ?? '',
    userKey: task?.user_key ?? '',
    description: task?.description ?? '',
    kind: task?.kind ?? 'task',
    status: task?.status ?? 'planned',
    outcome: task?.outcome ?? '',
    priority: task?.priority ?? 'required',
    labels: task?.labels.join(', ') ?? '',
    targetDate: task?.target_date ?? '',
    completionCriteria: task?.completion_criteria ?? '',
    blockerReason: task?.blocker_reason ?? '',
    completionSummary: task?.completion_summary ?? '',
    completionOverrideReason: task?.completion_override_reason ?? '',
    childFlowMode: task?.child_flow_mode ?? 'freeform',
  }
}

function incompleteDescendants(tasks: Array<{ id: string; parent_id?: string | null; status: TaskStatus }>, rootId: string) {
  const result: string[] = []
  const queue = [rootId]
  while (queue.length) {
    const parentId = queue.shift()!
    tasks.filter((task) => task.parent_id === parentId).forEach((task) => {
      queue.push(task.id)
      if (!['done', 'dropped'].includes(task.status)) result.push(task.id)
    })
  }
  return result
}

function TaskStageDialog({ state, stage, snapshot, operations, projectedTasks, disabled, onClose, onCommit }: {
  state: TaskDialogState
  stage: ProposalStage
  snapshot: ProjectSnapshot
  operations: ProposalOperation[]
  projectedTasks: Array<{ id: string; parent_id?: string | null; status: TaskStatus }>
  disabled: boolean
  onClose: () => void
  onCommit: (operations: ProposalOperation[], message: string) => void
}) {
  const task = state.taskId ? stage.taskById.get(state.taskId) : undefined
  const [draft, setDraft] = useState<TaskDraft>(() => taskDraft(task))
  const [formError, setFormError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const incomplete = task ? incompleteDescendants(projectedTasks, task.id) : []
  const requestClose = () => requestCloseWithUnsavedChanges(dirty, onClose)

  useEffect(() => {
    if (!state.open) return
    setDraft(taskDraft(task))
    setFormError(null)
    setDirty(false)
  }, [state.open, task?.id])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (disabled) return
    if (!draft.title.trim()) return setFormError('Task title is required.')
    if (draft.status === 'blocked' && !draft.blockerReason.trim()) return setFormError('Blocked tasks require a blocker explanation.')
    if (draft.status === 'done' && !draft.completionSummary.trim()) return setFormError('Done tasks require a completion summary.')
    if (draft.status === 'done' && incomplete.length > 0 && !draft.completionOverrideReason.trim()) return setFormError('Record why this parent can be done while descendants remain incomplete.')

    const desiredPatch: TaskStagePatch = {
      title: draft.title.trim(),
      user_key: draft.userKey.trim() || null,
      description: draft.description.trim() || null,
      kind: draft.kind,
      status: draft.status,
      outcome: draft.outcome || null,
      priority: draft.priority,
      labels: draft.labels.split(',').map((label) => label.trim()).filter(Boolean),
      target_date: draft.targetDate || null,
      completion_criteria: draft.completionCriteria.trim() || null,
      blocker_reason: draft.blockerReason.trim() || null,
      completion_summary: draft.completionSummary.trim() || null,
      completion_override_reason: draft.completionOverrideReason.trim() || null,
      child_flow_mode: draft.childFlowMode,
    }

    try {
      let next = operations
      let taskId = task?.id
      const patch = task ? changedStageFields(task, desiredPatch) : desiredPatch
      if (task && !Object.keys(patch).length) {
        onClose()
        return
      }
      if (!taskId) {
        const pipelineId = state.pipelineId
        if (!pipelineId) throw new Error('Choose a staged pipeline before adding a task.')
        const parentId = state.parentId ?? null
        const siblings = stage.children.get(parentId ?? `pipeline:${pipelineId}`) ?? []
        const position = siblings.length ? Math.max(...siblings.map((item) => item.position)) + 1 : 0
        const previousIds = new Set(stage.tasks.map((item) => item.id))
        next = addStagedTask(next, snapshot, pipelineId, parentId, draft.title, position)
        const created = [...next].reverse().find((operation) => operation.type === 'task.create' && !previousIds.has(proposalOperationEntityId(operation) ?? ''))
        taskId = created ? proposalOperationEntityId(created) ?? undefined : undefined
        if (!taskId) throw new Error('The new staged task could not be identified.')
      }
      next = updateStagedTask(next, snapshot, taskId, patch)
      onCommit(next, task ? `Updated staged task “${draft.title.trim()}”.` : `Added staged task “${draft.title.trim()}”.`)
      onClose()
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : 'The staged task could not be saved.')
    }
  }

  return (
    <Dialog open={state.open} onClose={requestClose} title={task ? 'Edit proposed task' : state.parentId ? 'Add proposed subtask' : 'Add proposed task'} description="Edit the human-readable task here; the operation audit remains available beside this view." wide>
      <form className="proposed-task-form" onSubmit={submit} onChangeCapture={() => setDirty(true)} noValidate>
        <div className="form-grid key-title-grid"><Field label="Key"><input value={draft.userKey} onChange={(event) => setDraft({ ...draft, userKey: event.target.value })} placeholder="EXP-01" /></Field><Field label="Task title"><input required value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} placeholder="Run baseline comparison" /></Field></div>
        <Field label="Description"><textarea rows={4} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="Context, approach, and constraints…" /></Field>
        <div className="form-grid three">
          <Field label="Kind"><select value={draft.kind} onChange={(event) => setDraft({ ...draft, kind: event.target.value as TaskKind })}>{TASK_KINDS.map((kind) => <option key={kind} value={kind}>{humanize(kind)}</option>)}</select></Field>
          <Field label="Status"><select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value as TaskStatus })}>{TASK_STATUSES.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}</select></Field>
          <Field label="Priority"><select value={draft.priority} onChange={(event) => setDraft({ ...draft, priority: event.target.value as TaskPriority })}>{TASK_PRIORITIES.map((priority) => <option key={priority} value={priority}>{humanize(priority)}</option>)}</select></Field>
        </div>
        <div className="form-grid two">
          <Field label="Research outcome"><select value={draft.outcome} onChange={(event) => setDraft({ ...draft, outcome: event.target.value as TaskOutcome | '' })}><option value="">Not recorded</option>{TASK_OUTCOMES.map((outcome) => <option key={outcome} value={outcome}>{humanize(outcome)}</option>)}</select></Field>
          <Field label="Target date"><input type="date" value={draft.targetDate} onChange={(event) => setDraft({ ...draft, targetDate: event.target.value })} /></Field>
        </div>
        <div className="form-grid two">
          <Field label="Labels" hint="Comma-separated"><input value={draft.labels} onChange={(event) => setDraft({ ...draft, labels: event.target.value })} placeholder="baseline, analysis" /></Field>
          <Field label="Child task flow"><select value={draft.childFlowMode} onChange={(event) => setDraft({ ...draft, childFlowMode: event.target.value as typeof draft.childFlowMode })}><option value="freeform">Freeform</option><option value="sequential">Sequential</option></select></Field>
        </div>
        <Field label="Completion criteria"><textarea rows={3} value={draft.completionCriteria} onChange={(event) => setDraft({ ...draft, completionCriteria: event.target.value })} placeholder="What observable evidence makes this complete?" /></Field>
        {draft.status === 'blocked' && <Field label="Blocker explanation"><textarea required rows={2} value={draft.blockerReason} onChange={(event) => setDraft({ ...draft, blockerReason: event.target.value })} placeholder="What is preventing progress?" /></Field>}
        {draft.status === 'done' && <Field label="Completion summary"><textarea required rows={3} value={draft.completionSummary} onChange={(event) => setDraft({ ...draft, completionSummary: event.target.value })} placeholder="What was completed, and what was learned?" /></Field>}
        {draft.status === 'done' && incomplete.length > 0 && <><Notice tone="warning">{incomplete.length} descendant task{incomplete.length === 1 ? '' : 's'} remain incomplete. A guarded override rationale is required.</Notice><Field label="Completion override rationale"><textarea required rows={2} value={draft.completionOverrideReason} onChange={(event) => setDraft({ ...draft, completionOverrideReason: event.target.value })} placeholder="Why can the parent be closed now?" /></Field></>}
        {formError && <Notice tone="danger">{formError}</Notice>}
        <div className="dialog-actions"><Button type="button" variant="ghost" onClick={requestClose}>Cancel</Button><Button type="submit" disabled={disabled}>{task ? 'Save task changes' : 'Add task'}</Button></div>
      </form>
    </Dialog>
  )
}

function SplitStageDialog({ open, task, snapshot, operations, disabled, onClose, onCommit }: {
  open: boolean
  task?: StageTask
  snapshot: ProjectSnapshot
  operations: ProposalOperation[]
  disabled: boolean
  onClose: () => void
  onCommit: (operations: ProposalOperation[], message: string) => void
}) {
  const [titles, setTitles] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setTitles('')
    setFormError(null)
  }, [open, task?.id])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (disabled || !task) return
    try {
      const values = titles.split('\n').map((title) => title.trim()).filter(Boolean)
      const next = splitStagedTask(operations, snapshot, task.id, values)
      onCommit(next, `Split “${task.title}” into ${values.length} staged subtasks.`)
      onClose()
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : 'The staged task could not be split.')
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Split proposed task" description="The task becomes a milestone and each nonblank line becomes a planned child task.">
      <form className="form-stack" onSubmit={submit}>
        <Field label="Subtask titles" hint="Enter at least two titles, one per line."><textarea rows={7} required value={titles} onChange={(event) => setTitles(event.target.value)} placeholder={'Prepare inputs\nRun evaluation\nAnalyze outputs'} /></Field>
        {formError && <Notice tone="danger">{formError}</Notice>}
        <div className="dialog-actions"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" disabled={disabled || !task}><Scissors size={14} /> Split task</Button></div>
      </form>
    </Dialog>
  )
}

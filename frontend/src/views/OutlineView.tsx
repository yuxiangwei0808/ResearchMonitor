import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { DndContext, KeyboardSensor, PointerSensor, useDraggable, useDroppable, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { CSS } from '@dnd-kit/utilities'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router-dom'
import { Archive, ArrowDown, ArrowLeftFromLine, ArrowRightFromLine, ArrowUp, Ban, CheckCircle2, ChevronDown, ChevronRight, Circle, CircleDot, Edit3, ExternalLink, FileText, GitBranch, GripVertical, ListFilter, Milestone, MoreHorizontal, Plus, RotateCcw, Search, Trash2 } from 'lucide-react'
import { clsx } from 'clsx'
import type { JournalEntry, Pipeline, ProjectSnapshot, Task, TaskKind, TaskOutcome, TaskPriority, TaskStatus } from '../types'
import { ARTIFACT_ROLES, TASK_KINDS, TASK_OUTCOMES, TASK_PRIORITIES, TASK_STATUSES } from '../types'
import { formatCalendarDate, formatDate, humanize, statusTone } from '../lib/format'
import { api, operation } from '../lib/api'
import { useProjectMutation } from '../lib/hooks'
import { createTaskLabeler } from '../lib/taskLabels'
import { requestCloseWithUnsavedChanges } from '../lib/unsavedChanges'
import { Badge, Button, Dialog, EmptyState, Field, Notice } from '../components/ui'
import { SafeMarkdown } from '../components/SafeMarkdown'
import { TaskActionsButton, TaskContextRegion } from '../components/TaskActions'

const statusIcons: Record<TaskStatus, React.ComponentType<{ size?: number }>> = {
  planned: Circle,
  in_progress: CircleDot,
  blocked: Ban,
  review: MoreHorizontal,
  done: CheckCircle2,
  dropped: Archive,
}

type TaskDraft = {
  title: string
  user_key: string
  description: string
  kind: TaskKind
  status: TaskStatus
  outcome: TaskOutcome | ''
  priority: TaskPriority
  labels: string
  target_date: string
  completion_criteria: string
  blocker_reason: string
  completion_summary: string
  completion_override_reason: string
  child_flow_mode: 'sequential' | 'freeform'
}

const blankDraft = (): TaskDraft => ({
  title: '', user_key: '', description: '', kind: 'task', status: 'planned', outcome: '', priority: 'required', labels: '', target_date: '', completion_criteria: '', blocker_reason: '', completion_summary: '', completion_override_reason: '', child_flow_mode: 'freeform',
})

function toDraft(task?: Task): TaskDraft {
  if (!task) return blankDraft()
  return {
    title: task.title,
    user_key: task.user_key ?? '',
    description: task.description ?? '',
    kind: task.kind,
    status: task.status,
    outcome: task.outcome ?? '',
    priority: task.priority,
    labels: task.labels.join(', '),
    target_date: task.target_date ?? '',
    completion_criteria: task.completion_criteria ?? '',
    blocker_reason: task.blocker_reason ?? '',
    completion_summary: task.completion_summary ?? '',
    completion_override_reason: task.completion_override_reason ?? '',
    child_flow_mode: task.child_flow_mode,
  }
}

export function OutlineView({ snapshot }: { snapshot: ProjectSnapshot }) {
  const mutation = useProjectMutation(snapshot)
  const [params, setParams] = useSearchParams()
  const [search, setSearch] = useState(params.get('q') ?? '')
  const [filter, setFilter] = useState(params.get('view') ?? 'remaining')
  const [moreFilters, setMoreFilters] = useState(false)
  const [priority, setPriority] = useState('all')
  const [label, setLabel] = useState('all')
  const [artifactType, setArtifactType] = useState('all')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [pipelineDialog, setPipelineDialog] = useState<{ open: boolean; pipeline?: Pipeline }>({ open: false })
  const [taskDialog, setTaskDialog] = useState<{ open: boolean; task?: Task; pipelineId?: string; parentId?: string | null }>({ open: false })
  const [taskAfterPipeline, setTaskAfterPipeline] = useState(false)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  )
  const tasks = snapshot.tasks.filter((task) => !task.deleted_at)
  const pipelines = snapshot.pipelines.filter((pipeline) => !pipeline.deleted_at && !pipeline.archived).sort((a, b) => a.position - b.position)
  const deletedPipelines = snapshot.pipelines.filter((pipeline) => Boolean(pipeline.deleted_at))
  const archivedPipelines = snapshot.pipelines.filter((pipeline) => pipeline.archived && !pipeline.deleted_at)
  const allTaskById = new Map(snapshot.tasks.map((task) => [task.id, task]))
  const deletedPipelineIds = new Set(deletedPipelines.map((pipeline) => pipeline.id))
  const deletedTaskRoots = snapshot.tasks.filter((task) => task.deleted_at && !deletedPipelineIds.has(task.pipeline_id) && (!task.parent_id || !allTaskById.get(task.parent_id)?.deleted_at))
  const taskById = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks])
  const deferredSearch = useDeferredValue(search.trim())
  const searchQuery = useQuery({
    queryKey: ['project-search', snapshot.project.id, deferredSearch],
    queryFn: () => api.searchProject(snapshot.project.id, deferredSearch),
    enabled: Boolean(deferredSearch),
    staleTime: 10_000,
  })
  const labels = useMemo(() => [...new Set(tasks.flatMap((task) => task.labels))].sort((a, b) => a.localeCompare(b)), [tasks])
  const artifactById = useMemo(() => new Map(snapshot.artifacts.filter((artifact) => !artifact.deleted_at).map((artifact) => [artifact.id, artifact])), [snapshot.artifacts])
  const linksByTask = useMemo(() => {
    const result = new Map<string, typeof snapshot.task_artifacts>()
    snapshot.task_artifacts.forEach((link) => result.set(link.task_id, [...(result.get(link.task_id) ?? []), link]))
    return result
  }, [snapshot.task_artifacts])
  const fullTextTaskIds = useMemo(() => {
    if (!deferredSearch || !searchQuery.data) return null
    const result = new Set<string>()
    searchQuery.data.results.forEach((item) => {
      if (item.entity_type === 'task') result.add(item.entity_id)
      else if (item.entity_type === 'journal' && item.task_id) result.add(item.task_id)
      else if (item.entity_type === 'artifact') snapshot.task_artifacts.filter((link) => link.artifact_id === item.entity_id).forEach((link) => result.add(link.task_id))
    })
    return result
  }, [deferredSearch, searchQuery.data, snapshot.task_artifacts])
  const children = useMemo(() => {
    const map = new Map<string, Task[]>()
    tasks.forEach((task) => {
      const key = task.parent_id ?? `pipeline:${task.pipeline_id}`
      const group = map.get(key) ?? []
      group.push(task)
      map.set(key, group)
    })
    map.forEach((group) => group.sort((a, b) => a.position - b.position))
    return map
  }, [tasks])

  useEffect(() => {
    const taskId = params.get('task')
    const task = taskId ? taskById.get(taskId) : undefined
    if (task) setTaskDialog((current) => current.open && current.task?.id === task.id ? current : { open: true, task })
  }, [params, taskById])

  useEffect(() => {
    const action = params.get('action')
    if (!action) return
    if (action === 'new-pipeline') setPipelineDialog({ open: true })
    if (action === 'new-task') {
      if (pipelines.length) setTaskDialog({ open: true, pipelineId: pipelines[0].id, parentId: null })
      else { setTaskAfterPipeline(true); setPipelineDialog({ open: true }) }
    }
    const next = new URLSearchParams(params)
    next.delete('action')
    setParams(next, { replace: true })
  }, [params, pipelines, setParams])

  const startNewTask = () => {
    if (pipelines.length) setTaskDialog({ open: true, pipelineId: pipelines[0].id, parentId: null })
    else { setTaskAfterPipeline(true); setPipelineDialog({ open: true }) }
  }

  const matchesFilter = (task: Task) => {
    const quick = filter === 'all'
      ? true
      : filter === 'remaining'
        ? !['done', 'dropped'].includes(task.status)
        : filter === 'ready' || filter === 'waiting'
          ? !['done', 'dropped'].includes(task.status) && task.readiness === filter
          : task.status === filter
    if (!quick || (priority !== 'all' && task.priority !== priority) || (label !== 'all' && !task.labels.includes(label))) return false
    if (artifactType !== 'all') {
      const matchingArtifact = (linksByTask.get(task.id) ?? []).some((link) => artifactType === link.role || artifactById.get(link.artifact_id)?.kind === artifactType)
      if (!matchingArtifact) return false
    }
    return true
  }
  const matchesSearch = (task: Task) => {
    if (!search.trim()) return true
    if (deferredSearch === search.trim() && fullTextTaskIds) return fullTextTaskIds.has(task.id)
    return `${task.user_key ?? ''} ${task.title} ${task.description ?? ''} ${task.labels.join(' ')}`.toLowerCase().includes(search.toLowerCase())
  }
  const visible = (task: Task): boolean => {
    if (matchesFilter(task) && matchesSearch(task)) return true
    return (children.get(task.id) ?? []).some(visible)
  }

  const moveTask = (task: Task, action: 'up' | 'down' | 'indent' | 'outdent', targetId?: string) => {
    const siblingKey = task.parent_id ?? `pipeline:${task.pipeline_id}`
    const siblings = children.get(siblingKey) ?? []
    const index = siblings.findIndex((item) => item.id === task.id)
    let parentId = task.parent_id ?? null
    let position = task.position
    if (targetId) {
      const target = taskById.get(targetId)
      if (!target || target.pipeline_id !== task.pipeline_id || target.id === task.id) return
      parentId = target.parent_id ?? null
      const targetSiblings = (children.get(target.parent_id ?? `pipeline:${target.pipeline_id}`) ?? []).filter((item) => item.id !== task.id)
      const targetIndex = targetSiblings.findIndex((item) => item.id === target.id)
      const beforeTarget = targetIndex > 0 ? targetSiblings[targetIndex - 1] : undefined
      position = beforeTarget ? (beforeTarget.position + target.position) / 2 : target.position - 1
    } else if (action === 'up' && index > 0) {
      const previous = siblings[index - 1]
      const beforePrevious = index > 1 ? siblings[index - 2] : undefined
      position = beforePrevious ? (beforePrevious.position + previous.position) / 2 : previous.position - 1
    } else if (action === 'down' && index < siblings.length - 1) {
      const next = siblings[index + 1]
      const afterNext = index + 2 < siblings.length ? siblings[index + 2] : undefined
      position = afterNext ? (next.position + afterNext.position) / 2 : next.position + 1
    }
    else if (action === 'indent' && index > 0) {
      const previous = siblings[index - 1]
      parentId = previous.id
      const nested = children.get(previous.id) ?? []
      position = nested.length ? nested[nested.length - 1].position + 1 : 1
    } else if (action === 'outdent' && task.parent_id) {
      const parent = taskById.get(task.parent_id)
      if (!parent) return
      parentId = parent.parent_id ?? null
      const parentSiblings = children.get(parent.parent_id ?? `pipeline:${parent.pipeline_id}`) ?? []
      const parentIndex = parentSiblings.findIndex((item) => item.id === parent.id)
      const nextParentSibling = parentIndex >= 0 ? parentSiblings[parentIndex + 1] : undefined
      position = nextParentSibling ? (parent.position + nextParentSibling.position) / 2 : parent.position + 1
    } else return
    mutation.mutate(operation('task.move', { pipeline_id: task.pipeline_id, parent_id: parentId, position }, { id: task.id, version: task.version }))
  }

  const updateStatus = (task: Task, status: TaskStatus) => {
    if (status === 'blocked' || status === 'done') {
      setTaskDialog({ open: true, task: { ...task, status } })
      return
    }
    mutation.mutate(operation('task.update', { status }, { id: task.id, version: task.version }))
  }

  const finishTaskDrag = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return
    const source = taskById.get(String(active.id))
    const target = taskById.get(String(over.id))
    if (source && target) moveTask(source, 'up', target.id)
  }

  const movePipeline = (pipeline: Pipeline, direction: -1 | 1) => {
    const index = pipelines.findIndex((item) => item.id === pipeline.id)
    const targetIndex = index + direction
    if (index < 0 || targetIndex < 0 || targetIndex >= pipelines.length) return
    let position: number
    if (direction < 0) {
      const target = pipelines[targetIndex]
      const before = pipelines[targetIndex - 1]
      position = before ? (before.position + target.position) / 2 : target.position - 1
    } else {
      const target = pipelines[targetIndex]
      const after = pipelines[targetIndex + 1]
      position = after ? (target.position + after.position) / 2 : target.position + 1
    }
    mutation.mutate(operation('pipeline.update', { position }, { id: pipeline.id, version: pipeline.version }))
  }

  const closeTask = () => {
    setTaskDialog({ open: false })
    if (params.has('task')) {
      const next = new URLSearchParams(params)
      next.delete('task')
      setParams(next, { replace: true })
    }
  }

  const deleteTask = async (task: Task) => {
    if (!window.confirm(`Move “${task.title}” and all subtasks to trash?`)) return
    try { await mutation.mutateAsync(operation('task.delete', {}, { id: task.id, version: task.version })) } catch { /* rendered by the mutation notice */ }
  }

  return (
    <div className="view-page outline-view">
      <header className="view-toolbar">
        <div><h2>Research outline</h2><p>Plan pipelines, nest tasks, and record the status of the work.</p></div>
        <div className="button-row"><Button variant="secondary" onClick={() => setPipelineDialog({ open: true })}><GitBranch size={16} />New pipeline</Button><Button onClick={startNewTask}><Plus size={16} />New task</Button></div>
      </header>
      <div className="filter-bar">
        <label className="search-field"><Search size={16} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search tasks, labels, keys…" /></label>
        <div className="filter-pills" aria-label="Quick task views">
          {['remaining', 'ready', 'waiting', 'blocked', 'review', 'done', 'dropped', 'all'].map((item) => <button key={item} className={filter === item ? 'active' : ''} onClick={() => { setFilter(item); const next = new URLSearchParams(params); next.set('view', item); setParams(next, { replace: true }) }}>{humanize(item)}</button>)}
        </div>
        <Button size="icon" variant={moreFilters ? 'secondary' : 'ghost'} aria-label="More filters" aria-expanded={moreFilters} onClick={() => setMoreFilters((value) => !value)}><ListFilter size={17} /></Button>
      </div>
      {moreFilters && <div className="advanced-task-filters" aria-label="Advanced task filters">
        <label><span>Priority</span><select value={priority} onChange={(event) => setPriority(event.target.value)}><option value="all">All priorities</option>{TASK_PRIORITIES.map((item) => <option value={item} key={item}>{humanize(item)}</option>)}</select></label>
        <label><span>Label</span><select value={label} onChange={(event) => setLabel(event.target.value)}><option value="all">All labels</option>{labels.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        <label><span>Linked artifact</span><select value={artifactType} onChange={(event) => setArtifactType(event.target.value)}><option value="all">Any artifact</option><option value="local">Local path</option><option value="url">External URL</option>{ARTIFACT_ROLES.map((item) => <option value={item} key={item}>{humanize(item)} role</option>)}</select></label>
        {(priority !== 'all' || label !== 'all' || artifactType !== 'all') && <Button variant="ghost" size="sm" onClick={() => { setPriority('all'); setLabel('all'); setArtifactType('all') }}>Clear filters</Button>}
      </div>}
      {searchQuery.error && <Notice tone="danger">Full-text search failed: {searchQuery.error.message}</Notice>}
      {searchQuery.data?.truncated && <Notice tone="warning">Search reached the safety limit. Narrow the query to see every match.</Notice>}
      {mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}
      {!pipelines.length ? <EmptyState icon={<GitBranch size={28} />} title="Create the first pipeline" description="A task belongs to a pipeline. You can create only the pipeline, or continue directly into its first task." action={<div className="button-row"><Button onClick={() => setPipelineDialog({ open: true })}><GitBranch size={17} />Create pipeline</Button><Button variant="secondary" onClick={startNewTask}><Plus size={17} />Create task</Button></div>} /> : (
        <DndContext sensors={sensors} onDragEnd={finishTaskDrag}>
        <div className="pipeline-outline-list">
          {pipelines.map((pipeline) => {
            const roots = (children.get(`pipeline:${pipeline.id}`) ?? []).filter(visible)
            const pipelineTasks = tasks.filter((task) => task.pipeline_id === pipeline.id)
            const done = pipelineTasks.filter((task) => task.status === 'done').length
            return (
              <section className="outline-pipeline" id={pipeline.id} key={pipeline.id}>
                <header className="outline-pipeline-header">
                  <button className="collapse-button" aria-label={`${collapsed.has(`pipeline:${pipeline.id}`) ? 'Expand' : 'Collapse'} ${pipeline.title}`} aria-expanded={!collapsed.has(`pipeline:${pipeline.id}`)} onClick={() => setCollapsed((previous) => toggleSet(previous, `pipeline:${pipeline.id}`))}>{collapsed.has(`pipeline:${pipeline.id}`) ? <ChevronRight size={18} /> : <ChevronDown size={18} />}</button>
                  <span className="pipeline-icon"><GitBranch size={17} /></span>
                  <div className="pipeline-heading"><div><h3>{pipeline.title}</h3><Badge tone={pipeline.flow_mode === 'sequential' ? 'blue' : 'neutral'}>{humanize(pipeline.flow_mode)}</Badge></div>{pipeline.description && <p>{pipeline.description}</p>}</div>
                  <span className="pipeline-count">{done}/{pipelineTasks.length} done</span>
                  <Button size="icon" variant="ghost" onClick={() => movePipeline(pipeline, -1)} aria-label={`Move ${pipeline.title} up`}><ArrowUp size={14} /></Button>
                  <Button size="icon" variant="ghost" onClick={() => movePipeline(pipeline, 1)} aria-label={`Move ${pipeline.title} down`}><ArrowDown size={14} /></Button>
                  <Button size="icon" variant="ghost" onClick={() => setTaskDialog({ open: true, pipelineId: pipeline.id, parentId: null })} aria-label={`Add task to ${pipeline.title}`}><Plus size={17} /></Button>
                  <Button size="icon" variant="ghost" onClick={() => setPipelineDialog({ open: true, pipeline })} aria-label={`Edit ${pipeline.title}`}><Edit3 size={16} /></Button>
                </header>
                {!collapsed.has(`pipeline:${pipeline.id}`) && <div className="task-tree">{roots.map((task) => <TaskRow key={task.id} task={task} depth={0} children={children} visible={visible} collapsed={collapsed} setCollapsed={setCollapsed} onEdit={(item) => setTaskDialog({ open: true, task: item })} onAddChild={(item) => setTaskDialog({ open: true, pipelineId: item.pipeline_id, parentId: item.id })} onDelete={deleteTask} onMove={moveTask} onStatus={updateStatus} />)}{!roots.length && <div className="pipeline-empty"><p>{search || filter !== 'all' ? 'No tasks match this view.' : 'This pipeline is empty.'}</p><Button variant="ghost" size="sm" onClick={() => setTaskDialog({ open: true, pipelineId: pipeline.id, parentId: null })}><Plus size={15} />Add task</Button></div>}</div>}
              </section>
            )
          })}
        </div>
        </DndContext>
      )}
      {archivedPipelines.length > 0 && <section className="settings-section"><header><span className="settings-icon"><Archive size={18} /></span><div><h3>Archived pipelines</h3><p>Archived workstreams remain in monitor history and can be restored at any time.</p></div></header><div className="lifecycle-actions">{archivedPipelines.map((pipeline) => <div key={pipeline.id}><span><strong>{pipeline.title}</strong><small>Archived pipeline and its task subtrees</small></span><Button variant="secondary" size="sm" onClick={() => mutation.mutate(operation('pipeline.restore', {}, { id: pipeline.id, version: pipeline.version }))}><RotateCcw size={14} />Restore pipeline</Button></div>)}</div></section>}
      {(deletedPipelines.length > 0 || deletedTaskRoots.length > 0) && <section className="settings-section danger-section"><header><span className="settings-icon"><Trash2 size={18} /></span><div><h3>Deleted items</h3><p>Restore a pipeline or task subtree together with every still-valid incident dependency.</p></div></header><div className="lifecycle-actions">{deletedPipelines.map((pipeline) => <div key={pipeline.id}><span><strong>{pipeline.title}</strong><small>Deleted pipeline and its task subtrees</small></span><Button variant="secondary" size="sm" onClick={() => mutation.mutate(operation('pipeline.restore', {}, { id: pipeline.id, version: pipeline.version }))}><RotateCcw size={14} />Restore pipeline</Button></div>)}{deletedTaskRoots.map((task) => <div key={task.id}><span><strong>{task.title}</strong><small>Deleted task subtree</small></span><Button variant="secondary" size="sm" onClick={() => mutation.mutate(operation('task.restore', {}, { id: task.id, version: task.version }))}><RotateCcw size={14} />Restore subtree</Button></div>)}</div></section>}
      <PipelineEditor snapshot={snapshot} state={pipelineDialog} onClose={() => { setPipelineDialog({ open: false }); setTaskAfterPipeline(false) }} afterCreate={taskAfterPipeline ? (pipelineId) => { setTaskAfterPipeline(false); setTaskDialog({ open: true, pipelineId, parentId: null }) } : undefined} />
      <TaskEditor snapshot={snapshot} state={taskDialog} onClose={closeTask} />
    </div>
  )
}

function TaskRow({ task, depth, children, visible, collapsed, setCollapsed, onEdit, onAddChild, onDelete, onMove, onStatus }: {
  task: Task; depth: number; children: Map<string, Task[]>; visible: (task: Task) => boolean; collapsed: Set<string>; setCollapsed: React.Dispatch<React.SetStateAction<Set<string>>>; onEdit: (task: Task) => void; onAddChild: (task: Task) => void; onDelete: (task: Task) => void; onMove: (task: Task, action: 'up' | 'down' | 'indent' | 'outdent') => void; onStatus: (task: Task, status: TaskStatus) => void
}) {
  const descendants = (children.get(task.id) ?? []).filter(visible)
  const hasChildren = (children.get(task.id) ?? []).length > 0
  const leafProgress = hasChildren ? descendantLeafProgress(task.id, children) : null
  const StatusIcon = statusIcons[task.status]
  const { attributes, listeners, setNodeRef: setDragRef, transform, isDragging } = useDraggable({ id: task.id })
  const { setNodeRef: setDropRef, isOver } = useDroppable({ id: task.id })
  const setRefs = (node: HTMLDivElement | null) => { setDragRef(node); setDropRef(node) }
  return (
    <div className={clsx('task-branch', isDragging && 'dragging', isOver && !isDragging && 'drop-target')}>
      <TaskContextRegion task={task} onEdit={() => onEdit(task)} onAddSubtask={() => onAddChild(task)} onDelete={() => onDelete(task)}>
      <div ref={setRefs} className="task-row" style={{ '--depth': depth, transform: CSS.Translate.toString(transform) } as React.CSSProperties}>
        <button type="button" className="drag-handle" title="Drag to reorder" aria-label={`Drag ${task.title} to reorder`} {...listeners} {...attributes}><GripVertical size={15} /></button>
        <button className={clsx('collapse-button', !hasChildren && 'invisible')} aria-label={`${collapsed.has(task.id) ? 'Expand' : 'Collapse'} ${task.title}`} aria-expanded={hasChildren ? !collapsed.has(task.id) : undefined} onClick={() => setCollapsed((previous) => toggleSet(previous, task.id))}>{collapsed.has(task.id) ? <ChevronRight size={17} /> : <ChevronDown size={17} />}</button>
        <span className={clsx('task-status-icon', `tone-${statusTone[task.status]}`)}><StatusIcon size={17} /></span>
        <button className="task-title-cell" aria-label={task.title} onClick={() => onEdit(task)}><span>{task.user_key && <code>{task.user_key}</code>}<strong>{task.title}</strong>{task.kind !== 'task' && <Badge tone="purple">{humanize(task.kind)}</Badge>}{task.consistency_warning && <Badge tone="amber">Completion override</Badge>}</span><small>{task.labels.slice(0, 3).map((label) => <em key={label}>{label}</em>)}{task.target_date && <time className="task-target-date" dateTime={task.target_date}>Target {formatCalendarDate(task.target_date)}</time>}{task.readiness === 'waiting' && <span>Waiting on {task.unsatisfied_predecessor_ids.length}</span>}{task.consistency_warning && <span title={task.consistency_warning}>{task.consistency_warning}</span>}{leafProgress && <span>{leafProgress.done}/{leafProgress.total} descendant leaves done</span>}</small></button>
        <Badge tone={task.readiness === 'ready' ? 'blue' : task.readiness === 'blocked' ? 'red' : task.readiness === 'inconsistent' ? 'amber' : 'muted'}>{humanize(task.readiness)}</Badge>
        <select className={`status-select tone-${statusTone[task.status]}`} value={task.status} onChange={(e) => onStatus(task, e.target.value as TaskStatus)} aria-label={`Status for ${task.title}`}>{TASK_STATUSES.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}</select>
        <div className="task-row-actions">
          <Button size="icon" variant="ghost" onClick={() => onMove(task, 'up')} aria-label={`Move ${task.title} up`}><ArrowUp size={14} /></Button>
          <Button size="icon" variant="ghost" onClick={() => onMove(task, 'down')} aria-label={`Move ${task.title} down`}><ArrowDown size={14} /></Button>
          <Button size="icon" variant="ghost" onClick={() => onMove(task, 'outdent')} aria-label={`Outdent ${task.title}`}><ArrowLeftFromLine size={14} /></Button>
          <Button size="icon" variant="ghost" onClick={() => onMove(task, 'indent')} aria-label={`Indent ${task.title}`}><ArrowRightFromLine size={14} /></Button>
          <Button size="icon" variant="ghost" onClick={() => onAddChild(task)} aria-label={`Add subtask to ${task.title}`}><Plus size={15} /></Button>
        </div>
        <TaskActionsButton task={task} onEdit={() => onEdit(task)} onAddSubtask={() => onAddChild(task)} onDelete={() => onDelete(task)} />
      </div>
      </TaskContextRegion>
      {hasChildren && !collapsed.has(task.id) && <div className="task-children">{descendants.map((child) => <TaskRow key={child.id} task={child} depth={depth + 1} children={children} visible={visible} collapsed={collapsed} setCollapsed={setCollapsed} onEdit={onEdit} onAddChild={onAddChild} onDelete={onDelete} onMove={onMove} onStatus={onStatus} />)}</div>}
    </div>
  )
}

function PipelineEditor({ snapshot, state, onClose, afterCreate }: { snapshot: ProjectSnapshot; state: { open: boolean; pipeline?: Pipeline }; onClose: () => void; afterCreate?: (pipelineId: string) => void }) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [flow, setFlow] = useState<'sequential' | 'freeform'>('sequential')
  const mutation = useProjectMutation(snapshot)
  useEffect(() => { setTitle(state.pipeline?.title ?? ''); setDescription(state.pipeline?.description ?? ''); setFlow(state.pipeline?.flow_mode ?? 'sequential') }, [state.open, state.pipeline])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (state.pipeline) await mutation.mutateAsync(operation('pipeline.update', { title, description, flow_mode: flow }, { id: state.pipeline.id, version: state.pipeline.version }))
    else {
      const pipelineId = crypto.randomUUID()
      await mutation.mutateAsync(operation('pipeline.create', { title, description, flow_mode: flow, position: snapshot.pipelines.length }, { id: pipelineId }))
      afterCreate?.(pipelineId)
    }
    onClose()
  }
  const remove = async () => {
    if (!state.pipeline || !window.confirm(`Soft-delete “${state.pipeline.title}” and its task subtrees? You can restore them later.`)) return
    await mutation.mutateAsync(operation('pipeline.delete', { cascade: true }, { id: state.pipeline.id, version: state.pipeline.version }))
    onClose()
  }
  const archive = async () => {
    if (!state.pipeline || !window.confirm(`Archive “${state.pipeline.title}” and hide its task subtrees?`)) return
    await mutation.mutateAsync(operation('pipeline.archive', {}, { id: state.pipeline.id, version: state.pipeline.version }))
    onClose()
  }
  return <Dialog open={state.open} onClose={onClose} title={state.pipeline ? 'Edit pipeline' : 'New pipeline'}><form className="form-stack" onSubmit={submit}><Field label="Title"><input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Data preparation" /></Field><Field label="Description"><textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What belongs in this workstream?" /></Field><Field label="Task flow"><select value={flow} onChange={(e) => setFlow(e.target.value as typeof flow)}><option value="sequential">Sequential — sibling order creates precedence</option><option value="freeform">Freeform — tasks are independent unless connected</option></select></Field>{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<div className="dialog-actions split">{state.pipeline ? <div className="button-row"><Button type="button" variant="secondary" onClick={archive}><Archive size={15} />Archive</Button><Button type="button" variant="danger" onClick={remove}><Trash2 size={15} />Delete</Button></div> : <span />}<div className="button-row"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? 'Saving…' : 'Save pipeline'}</Button></div></div></form></Dialog>
}

export function TaskEditor({ snapshot, state, onClose, compact = false }: { snapshot: ProjectSnapshot; state: { open: boolean; task?: Task; pipelineId?: string; parentId?: string | null }; onClose: () => void; compact?: boolean }) {
  const [draft, setDraft] = useState<TaskDraft>(blankDraft())
  const [targetPipelineId, setTargetPipelineId] = useState('')
  const [targetParentId, setTargetParentId] = useState('')
  const [journalType, setJournalType] = useState<JournalEntry['entry_type']>('progress')
  const [journal, setJournal] = useState('')
  const [dirty, setDirty] = useState(false)
  const [externalUpdate, setExternalUpdate] = useState(false)
  const [baseVersion, setBaseVersion] = useState<number | undefined>(state.task?.version)
  const sourceSignature = useRef('')
  const mutation = useProjectMutation(snapshot)
  const labelTask = useMemo(() => createTaskLabeler(snapshot.pipelines, snapshot.tasks), [snapshot.pipelines, snapshot.tasks])
  const latestTask = state.task ? snapshot.tasks.find((item) => item.id === state.task!.id) ?? state.task : undefined
  const latestSignature = editableTaskSignature(latestTask)
  useEffect(() => {
    if (!state.open) return
    const initialTask = state.task
    const currentTask = state.task ? snapshot.tasks.find((item) => item.id === state.task!.id) ?? state.task : undefined
    setDraft(toDraft(initialTask))
    setTargetPipelineId(initialTask?.pipeline_id ?? state.pipelineId ?? snapshot.pipelines.find((item) => !item.deleted_at && !item.archived)?.id ?? '')
    setTargetParentId(initialTask?.parent_id ?? state.parentId ?? '')
    setBaseVersion(initialTask?.version)
    sourceSignature.current = editableTaskSignature(currentTask)
    setJournalType('progress'); setJournal(''); setDirty(false); setExternalUpdate(false)
  }, [state.open, state.task?.id, state.pipelineId, state.parentId, snapshot.project.id])
  useEffect(() => {
    if (!state.open || !latestTask || sourceSignature.current === latestSignature) return
    if (dirty) setExternalUpdate(true)
    else {
      setDraft(toDraft(latestTask)); setTargetPipelineId(latestTask.pipeline_id); setTargetParentId(latestTask.parent_id ?? '')
      setBaseVersion(latestTask.version); sourceSignature.current = latestSignature; setExternalUpdate(false)
    }
  }, [dirty, latestSignature, latestTask, state.open])
  const existingChildren = state.task ? incompleteDescendants(snapshot.tasks, state.task.id) : []
  const descendantIds = new Set(state.task ? allDescendantIds(snapshot.tasks, state.task.id) : [])
  const parentOptions = snapshot.tasks
    .filter((item) => !item.deleted_at && item.pipeline_id === targetPipelineId && item.id !== state.task?.id && !descendantIds.has(item.id))
    .sort((left, right) => labelTask(left).localeCompare(labelTask(right)))
  const requestClose = () => requestCloseWithUnsavedChanges(dirty, onClose)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (draft.status === 'blocked' && !draft.blocker_reason.trim()) return
    if (draft.status === 'done' && existingChildren.length && !draft.completion_override_reason.trim()) return
    const data = {
      title: draft.title, user_key: draft.user_key || null, description: draft.description || null, kind: draft.kind, status: draft.status, outcome: draft.outcome || 'not_applicable', priority: draft.priority, labels: draft.labels.split(',').map((item) => item.trim()).filter(Boolean), target_date: draft.target_date || null, completion_criteria: draft.completion_criteria || null, blocker_reason: draft.blocker_reason || null, completion_summary: draft.completion_summary || null, completion_override_reason: draft.completion_override_reason || null, child_flow_mode: draft.child_flow_mode, pipeline_id: targetPipelineId, parent_id: targetParentId || null,
    }
    const operations = []
    if (state.task) operations.push(operation('task.update', data, { id: state.task.id, version: baseVersion }))
    else operations.push(operation('task.create', { ...data, position: snapshot.tasks.filter((item) => item.pipeline_id === targetPipelineId && (item.parent_id ?? null) === (targetParentId || null)).length }, { id: crypto.randomUUID() }))
    if (state.task && journal.trim()) operations.push(operation('journal.create', { task_id: state.task.id, entry_type: journalType, content: journal.trim(), occurred_at: new Date().toISOString() }, { id: crypto.randomUUID() }))
    try { await mutation.mutateAsync(operations); onClose() } catch { /* rendered by the mutation notice */ }
  }
  const remove = async () => {
    if (!state.task || !window.confirm(`Move “${state.task.title}” and all subtasks to trash?`)) return
    try { await mutation.mutateAsync(operation('task.delete', {}, { id: state.task.id, version: baseVersion })); onClose() } catch { /* rendered by the mutation notice */ }
  }
  const editJournal = async (entry: JournalEntry) => {
    const content = window.prompt('Edit this journal entry. The previous version remains in audit history:', entry.content)
    if (content == null || !content.trim() || content === entry.content) return
    await mutation.mutateAsync(operation('journal.update', { content: content.trim() }, { id: entry.id, version: entry.version }))
  }
  const removeJournal = async (entry: JournalEntry) => {
    if (!window.confirm('Delete this journal entry from the task view? Its revision remains in audit history.')) return
    await mutation.mutateAsync(operation('journal.delete', {}, { id: entry.id, version: entry.version }))
  }
  const taskJournals = state.task ? snapshot.journals.filter((entry) => entry.task_id === state.task!.id && !entry.deleted_at).sort((a, b) => b.occurred_at.localeCompare(a.occurred_at)) : []
  const artifactRoots = new Map(snapshot.artifact_roots.map((root) => [root.id, root]))
  const linkedArtifacts = state.task ? snapshot.task_artifacts
    .filter((association) => association.task_id === state.task!.id)
    .flatMap((association) => {
      const artifact = snapshot.artifacts.find((item) => item.id === association.artifact_id && !item.deleted_at)
      return artifact ? [{ association, artifact }] : []
    }) : []
  return (
    <Dialog open={state.open} onClose={requestClose} title={state.task ? 'Task details' : state.parentId ? 'New subtask' : 'New task'} wide={!compact}>
      <form className={clsx('task-editor-form', compact && 'compact')} onSubmit={submit} onChangeCapture={() => setDirty(true)}>
        <div className="editor-main form-stack">
          <div className="form-grid key-title-grid"><Field label="Key"><input value={draft.user_key} onChange={(e) => setDraft({ ...draft, user_key: e.target.value })} placeholder="EXP-01" /></Field><Field label="Task title"><input required value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Run baseline comparison" /></Field></div>
          <div className="form-grid two"><Field label="Pipeline"><select required value={targetPipelineId} onChange={(event) => { setTargetPipelineId(event.target.value); setTargetParentId('') }}>{snapshot.pipelines.filter((item) => !item.deleted_at && !item.archived).map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}</select></Field><Field label="Parent task" hint="Moving a parent moves its complete subtree."><select value={targetParentId} onChange={(event) => setTargetParentId(event.target.value)}><option value="">Pipeline root</option>{parentOptions.map((item) => <option value={item.id} key={item.id}>{labelTask(item)}</option>)}</select></Field></div>
          <Field label="Description"><textarea rows={4} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Context, approach, and important constraints…" /></Field>
          <details className="task-description-preview"><summary>Preview Markdown description</summary><SafeMarkdown value={draft.description} /></details>
          <div className="form-grid three"><Field label="Kind"><select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as TaskKind })}>{TASK_KINDS.map((kind) => <option key={kind} value={kind}>{humanize(kind)}</option>)}</select></Field><Field label="Status"><select value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value as TaskStatus })}>{TASK_STATUSES.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}</select></Field><Field label="Priority"><select value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value as TaskPriority })}>{TASK_PRIORITIES.map((priority) => <option key={priority} value={priority}>{humanize(priority)}</option>)}</select></Field></div>
          <div className="form-grid two"><Field label="Research outcome"><select value={draft.outcome} onChange={(e) => setDraft({ ...draft, outcome: e.target.value as TaskOutcome | '' })}><option value="">Not recorded</option>{TASK_OUTCOMES.map((outcome) => <option key={outcome} value={outcome}>{humanize(outcome)}</option>)}</select></Field><Field label="Target date" hint="Optional planning date. Created, updated, and completion times are recorded automatically."><input type="date" value={draft.target_date} onChange={(e) => setDraft({ ...draft, target_date: e.target.value })} /></Field></div>
          {state.task && (latestTask?.created_at || latestTask?.updated_at || latestTask?.completed_at) && <dl className="task-timestamp-strip" aria-label="Task timestamps">
            {latestTask?.created_at && <div><dt>Created</dt><dd><time dateTime={latestTask.created_at}>{formatDate(latestTask.created_at, true)}</time></dd></div>}
            {latestTask?.updated_at && <div><dt>Last updated</dt><dd><time dateTime={latestTask.updated_at}>{formatDate(latestTask.updated_at, true)}</time></dd></div>}
            {latestTask?.completed_at && <div><dt>Completed</dt><dd><time dateTime={latestTask.completed_at}>{formatDate(latestTask.completed_at, true)}</time></dd></div>}
          </dl>}
          <div className="form-grid two"><Field label="Labels" hint="Comma-separated"><input value={draft.labels} onChange={(e) => setDraft({ ...draft, labels: e.target.value })} placeholder="baseline, analysis" /></Field><Field label="Child task flow"><select value={draft.child_flow_mode} onChange={(e) => setDraft({ ...draft, child_flow_mode: e.target.value as typeof draft.child_flow_mode })}><option value="freeform">Freeform</option><option value="sequential">Sequential</option></select></Field></div>
          <Field label="Completion criteria"><textarea rows={3} value={draft.completion_criteria} onChange={(e) => setDraft({ ...draft, completion_criteria: e.target.value })} placeholder="What observable evidence will make this task complete?" /></Field>
          {draft.status === 'blocked' && <Field label="Blocker explanation"><textarea required rows={2} value={draft.blocker_reason} onChange={(e) => setDraft({ ...draft, blocker_reason: e.target.value })} placeholder="What is preventing progress?" /></Field>}
          {draft.status === 'done' && <Field label="Completion summary"><textarea required rows={3} value={draft.completion_summary} onChange={(e) => setDraft({ ...draft, completion_summary: e.target.value })} placeholder="What was completed, and what was learned?" /></Field>}
          {draft.status === 'done' && existingChildren.length > 0 && <><Notice tone="warning">{existingChildren.length} non-dropped descendant task{existingChildren.length === 1 ? '' : 's'} remain incomplete. Record why the parent can still be closed.</Notice><Field label="Completion override rationale"><textarea required rows={2} value={draft.completion_override_reason} onChange={(e) => setDraft({ ...draft, completion_override_reason: e.target.value })} /></Field></>}
          {state.task && !compact && (state.task.completed_at || linkedArtifacts.length > 0) && <section className="task-evidence-panel" aria-label="Completion record and linked evidence">
            <header><div><h3>Completion record & evidence</h3><p>Recorded provenance and artifacts associated with this task.</p></div><Link to={`/projects/${snapshot.project.id}/artifacts`} className="text-link">Manage artifacts</Link></header>
            {state.task.completed_at && <dl className="completion-provenance">
              <div><dt>Completed</dt><dd>{new Date(state.task.completed_at).toLocaleString()}</dd></div>
              <div><dt>Recorded by</dt><dd>{state.task.completion_actor || 'Not recorded'}</dd></div>
              <div><dt>Confirmation</dt><dd>{state.task.completion_source ? humanize(state.task.completion_source) : 'Not recorded'}</dd></div>
              <div><dt>Mode</dt><dd>{state.task.completion_provenance === 'agent' ? 'Accepted agent proposal' : 'Manual entry'}</dd></div>
            </dl>}
            <div className="task-evidence-list">
              {linkedArtifacts.map(({ association, artifact }) => {
                const root = artifact.artifact_root_id ? artifactRoots.get(artifact.artifact_root_id) : undefined
                const content = <><span><Badge tone="neutral">{humanize(association.role)}</Badge>{artifact.provider && <Badge tone="purple">{artifact.provider}</Badge>}</span><strong>{artifact.label}</strong><code title={artifact.locator}>{artifact.kind === 'local' && root ? `${root.name} / ${artifact.locator}` : artifact.locator}</code>{association.notes && <small>{association.notes}</small>}</>
                return artifact.kind === 'url'
                  ? <a key={association.id} href={artifact.locator} target="_blank" rel="noopener noreferrer" className="task-evidence-item">{content}<ExternalLink size={14} aria-hidden="true" /></a>
                  : <Link key={association.id} to={`/projects/${snapshot.project.id}/artifacts#artifact-${artifact.id}`} className="task-evidence-item">{content}<FileText size={14} aria-hidden="true" /></Link>
              })}
              {!linkedArtifacts.length && <p className="muted-copy">No artifacts are associated with this completion yet.</p>}
            </div>
          </section>}
        </div>
        {state.task && !compact && <aside className="editor-journal"><h3>Progress journal</h3><p>Append a dated research note to the immutable activity history.</p><div className="journal-composer"><select value={journalType} onChange={(e) => setJournalType(e.target.value as typeof journalType)}><option value="progress">Progress</option><option value="decision">Decision</option><option value="blocker">Blocker</option><option value="note">Note</option><option value="completion">Completion</option></select><textarea rows={4} value={journal} onChange={(e) => setJournal(e.target.value)} placeholder="Record what changed or why a decision was made…" /></div><div className="journal-list">{taskJournals.map((entry) => <article key={entry.id}><div className="journal-entry-header"><Badge tone="neutral">{humanize(entry.entry_type)}</Badge><span><Button type="button" size="icon" variant="ghost" onClick={() => editJournal(entry)} aria-label="Edit journal entry"><Edit3 size={13} /></Button><Button type="button" size="icon" variant="ghost" onClick={() => removeJournal(entry)} aria-label="Delete journal entry"><Trash2 size={13} /></Button></span></div><p>{entry.content}</p><small>{new Date(entry.occurred_at).toLocaleString()}</small></article>)}{!taskJournals.length && <p className="muted-copy">No journal entries yet.</p>}</div></aside>}
        {externalUpdate && <div className="span-all"><Notice tone="warning">This task changed in another UI or CLI action while you were editing. Your draft is preserved; saving will safely report a conflict instead of overwriting that change.</Notice></div>}
        {mutation.error && <div className="span-all"><Notice tone="danger">{mutation.error.message}</Notice></div>}
        <div className="dialog-actions split span-all">{state.task ? <Button type="button" variant="danger" onClick={remove}><Trash2 size={15} />Delete task</Button> : <span />}<div className="button-row"><Button type="button" variant="ghost" onClick={requestClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? 'Saving…' : state.task ? 'Save changes' : 'Create task'}</Button></div></div>
      </form>
    </Dialog>
  )
}

function toggleSet(previous: Set<string>, value: string) {
  const next = new Set(previous)
  next.has(value) ? next.delete(value) : next.add(value)
  return next
}

function incompleteDescendants(tasks: Task[], rootId: string) {
  const result: Task[] = []
  const queue = [rootId]
  while (queue.length) {
    const parentId = queue.shift()!
    const direct = tasks.filter((task) => task.parent_id === parentId && !task.deleted_at)
    direct.forEach((task) => {
      queue.push(task.id)
      if (!['done', 'dropped'].includes(task.status)) result.push(task)
    })
  }
  return result
}

function allDescendantIds(tasks: Task[], rootId: string) {
  const result: string[] = []
  const queue = [rootId]
  while (queue.length) {
    const parentId = queue.shift()!
    tasks.filter((task) => task.parent_id === parentId).forEach((task) => { result.push(task.id); queue.push(task.id) })
  }
  return result
}

function descendantLeafProgress(rootId: string, children: Map<string, Task[]>) {
  const leaves: Task[] = []
  const visit = (parentId: string) => {
    const direct = children.get(parentId) ?? []
    direct.forEach((task) => {
      if ((children.get(task.id) ?? []).length) visit(task.id)
      else leaves.push(task)
    })
  }
  visit(rootId)
  const nonDropped = leaves.filter((task) => task.status !== 'dropped')
  return { done: nonDropped.filter((task) => task.status === 'done').length, total: nonDropped.length }
}

function editableTaskSignature(task?: Task) {
  if (!task) return 'new-task'
  return JSON.stringify({
    id: task.id, version: task.version, pipeline_id: task.pipeline_id, parent_id: task.parent_id ?? null,
    user_key: task.user_key ?? null, kind: task.kind, title: task.title, description: task.description ?? null,
    status: task.status, outcome: task.outcome ?? null, priority: task.priority, labels: task.labels,
    target_date: task.target_date ?? null, completion_criteria: task.completion_criteria ?? null,
    blocker_reason: task.blocker_reason ?? null, completion_summary: task.completion_summary ?? null,
    completion_override_reason: task.completion_override_reason ?? null, child_flow_mode: task.child_flow_mode,
  })
}

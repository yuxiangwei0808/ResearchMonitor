import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type Viewport,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import { PreviewCard } from '@base-ui/react/preview-card'
import { ArrowLeft, GitBranch, LayoutGrid, Link2, Network, Plus, Unlink2 } from 'lucide-react'
import type { ProjectSnapshot, Task, TaskEdge } from '../types'
import { operation } from '../lib/api'
import { useLayoutMutation, useProjectMutation } from '../lib/hooks'
import { formatCalendarDate, humanize, statusTone } from '../lib/format'
import { createTaskLabeler, taskPickerSearchText } from '../lib/taskLabels'
import { Badge, Button, Dialog, EmptyState, Field, Notice } from '../components/ui'
import { TaskActionsButton, TaskContextRegion } from '../components/TaskActions'
import type { GuidedRequestSeed } from '../components/AskCodexDialog'
import { TaskEditor } from './OutlineView'

const nodeColors: Record<string, string> = {
  planned: '#94a09a', in_progress: '#477bb5', blocked: '#b34d4d', review: '#bf7b28', done: '#4e825c', dropped: '#a4a7a0',
}

export function GraphView({ snapshot, onAskCodex }: { snapshot: ProjectSnapshot; onAskCodex?: (seed: GuidedRequestSeed) => void }) {
  const [scope, setScope] = useState<string | null>(null)
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [edgeType, setEdgeType] = useState<'dependency' | 'related'>('dependency')
  const [layouting, setLayouting] = useState(false)
  const [layoutError, setLayoutError] = useState<string | null>(null)
  const [editingEdge, setEditingEdge] = useState<TaskEdge | null>(null)
  const [taskDialog, setTaskDialog] = useState<{ open: boolean; task?: Task; pipelineId?: string; parentId?: string | null }>({ open: false })
  const semanticMutation = useProjectMutation(snapshot)
  const layoutMutation = useLayoutMutation(snapshot)
  const activePipelines = useMemo(
    () => snapshot.pipelines.filter((pipeline) => !pipeline.deleted_at && !pipeline.archived).sort((left, right) => left.position - right.position),
    [snapshot.pipelines],
  )
  const activePipelineIds = useMemo(() => new Set(activePipelines.map((pipeline) => pipeline.id)), [activePipelines])
  const [selectedPipelineId, setSelectedPipelineId] = useState(() => activePipelines[0]?.id ?? 'all')
  const allTaskMap = useMemo(() => new Map(snapshot.tasks.map((task) => [task.id, task])), [snapshot.tasks])
  const taskMap = useMemo(() => new Map(snapshot.tasks.filter((task) => !task.deleted_at && activePipelineIds.has(task.pipeline_id)).map((task) => [task.id, task])), [snapshot.tasks, activePipelineIds])
  const disabledEdges = useMemo(() => snapshot.edges.filter((edge) => edge.disabled && !edge.deleted_at), [snapshot.edges])
  useEffect(() => {
    if (selectedPipelineId !== 'all' && !activePipelineIds.has(selectedPipelineId)) {
      setSelectedPipelineId(activePipelines[0]?.id ?? 'all')
      setScope(null)
    }
  }, [activePipelineIds, activePipelines, selectedPipelineId])
  const children = useMemo(() => {
    const map = new Map<string | null, Task[]>()
    taskMap.forEach((task) => {
      const list = map.get(task.parent_id ?? null) ?? []
      list.push(task)
      map.set(task.parent_id ?? null, list)
    })
    map.forEach((list) => list.sort((a, b) => a.position - b.position))
    return map
  }, [taskMap])
  const visibleTasks = useMemo(
    () => (children.get(scope) ?? []).filter((task) => scope !== null || selectedPipelineId === 'all' || task.pipeline_id === selectedPipelineId),
    [children, scope, selectedPipelineId],
  )
  const visibleTaskIds = useMemo(() => new Set(visibleTasks.map((task) => task.id)), [visibleTasks])
  const pipelineOrder = useMemo(
    () => new Map(activePipelines.map((pipeline, index) => [pipeline.id, index])),
    [activePipelines],
  )

  const ancestorInScope = useCallback((taskId: string): string | null => {
    let task = taskMap.get(taskId)
    const visited = new Set<string>()
    while (task && !visited.has(task.id)) {
      visited.add(task.id)
      if ((task.parent_id ?? null) === scope) return visibleTaskIds.has(task.id) ? task.id : null
      if (!task.parent_id) return null
      task = taskMap.get(task.parent_id)
    }
    return null
  }, [scope, taskMap, visibleTaskIds])

  const linkIndicators = useMemo(() => {
    const counts = new Map<string, { nested: number; external: number }>()
    const bump = (taskId: string, kind: 'nested' | 'external') => {
      const current = counts.get(taskId) ?? { nested: 0, external: 0 }
      counts.set(taskId, { ...current, [kind]: current[kind] + 1 })
    }
    snapshot.edges.filter((edge) => !edge.disabled && !edge.deleted_at).forEach((edge) => {
      const source = ancestorInScope(edge.source_task_id)
      const target = ancestorInScope(edge.target_task_id)
      const projected = new Map<string, { nested: boolean; external: boolean }>()
      if (source) projected.set(source, { nested: source !== edge.source_task_id, external: !target })
      if (target) {
        const current = projected.get(target) ?? { nested: false, external: false }
        projected.set(target, {
          nested: current.nested || target !== edge.target_task_id,
          external: current.external || !source,
        })
      }
      projected.forEach((flags, taskId) => {
        if (flags.nested) bump(taskId, 'nested')
        if (flags.external) bump(taskId, 'external')
      })
    })
    return counts
  }, [ancestorInScope, snapshot.edges])

  const editTask = useCallback((task: Task) => setTaskDialog({ open: true, task }), [])
  const addSubtask = useCallback((task: Task) => setTaskDialog({ open: true, pipelineId: task.pipeline_id, parentId: task.id }), [])
  const deleteTask = useCallback((task: Task) => {
    if (!window.confirm(`Move “${task.title}” and all subtasks to trash?`)) return
    semanticMutation.mutate(operation('task.delete', {}, { id: task.id, version: task.version }))
  }, [semanticMutation.mutate])

  useEffect(() => setSelectedTaskId(null), [scope, selectedPipelineId, snapshot.project.id])
  useEffect(() => {
    if (selectedTaskId && !visibleTaskIds.has(selectedTaskId)) setSelectedTaskId(null)
  }, [selectedTaskId, visibleTaskIds])

  const initialNodes = useMemo<Node[]>(() => visibleTasks.map((task, index) => {
    const saved = snapshot.layouts.find((layout) => layout.task_id === task.id && (layout.parent_id ?? null) === scope)
    const pipelineIndex = pipelineOrder.get(task.pipeline_id) ?? 0
    const subtasks = children.get(task.id) ?? []
    const childCount = subtasks.length
    return {
      id: task.id,
      selected: selectedTaskId === task.id,
      position: saved ? { x: saved.x, y: saved.y } : scope || selectedPipelineId !== 'all' ? { x: (index % 3) * 330, y: Math.floor(index / 3) * 160 } : { x: pipelineIndex * 360, y: task.position * 155 },
      data: {
        label: <GraphNode
          task={task}
          subtasks={subtasks}
          childCounts={children}
          selected={selectedTaskId === task.id}
          nestedLinks={linkIndicators.get(task.id)?.nested ?? 0}
          externalLinks={linkIndicators.get(task.id)?.external ?? 0}
          onSelect={() => setSelectedTaskId(task.id)}
          onOpen={() => setScope(task.id)}
          onEdit={() => editTask(task)}
          onAddSubtask={() => addSubtask(task)}
          onDelete={() => deleteTask(task)}
          onAskCodex={onAskCodex ? () => onAskCodex({ mode: 'expand_task', scopeType: 'task', scopeId: task.id }) : undefined}
        />,
      },
      className: `research-node status-${task.status}${selectedTaskId === task.id ? ' is-selected' : ''}`,
      style: { borderColor: nodeColors[task.status] },
    }
  }), [visibleTasks, snapshot.layouts, scope, selectedPipelineId, selectedTaskId, pipelineOrder, children, linkIndicators, editTask, addSubtask, deleteTask, onAskCodex])

  const graphEdges = useMemo<Edge[]>(() => {
    const result = new Map<string, Edge>()
    snapshot.edges.filter((edge) => !edge.disabled && !edge.deleted_at).forEach((edge) => {
      const source = ancestorInScope(edge.source_task_id)
      const target = ancestorInScope(edge.target_task_id)
      if (!source || !target || source === target) return
      const aggregated = source !== edge.source_task_id || target !== edge.target_task_id
      const key = `${edge.edge_type}:${source}:${target}`
      const previous = result.get(key)
      const count = Number(previous?.data?.count ?? 0) + 1
      result.set(key, {
        id: aggregated ? key : edge.id,
        source,
        target,
        type: 'smoothstep',
        markerEnd: edge.edge_type === 'dependency' ? { type: MarkerType.ArrowClosed, color: '#6d7d72' } : undefined,
        style: { stroke: edge.edge_type === 'related' ? '#9a91b3' : '#6d7d72', strokeDasharray: aggregated || edge.edge_type === 'related' ? '6 5' : undefined, strokeWidth: 1.7 },
        label: aggregated ? `${count} nested` : edge.waived ? 'waived' : undefined,
        data: { count, explicitId: aggregated ? undefined : edge.id },
      })
    })
    const rootPipelines = selectedPipelineId === 'all'
      ? activePipelines
      : activePipelines.filter((pipeline) => pipeline.id === selectedPipelineId)
    const siblingGroups = scope
      ? [[taskMap.get(scope)?.child_flow_mode, visibleTasks] as const]
      : rootPipelines.map((pipeline) => [pipeline.flow_mode, visibleTasks.filter((task) => task.pipeline_id === pipeline.id)] as const)
    siblingGroups.forEach(([mode, group]) => {
      if (mode !== 'sequential') return
      const active = group.filter((task) => task.status !== 'dropped').sort((a, b) => a.position - b.position)
      active.slice(1).forEach((task, index) => {
        const source = active[index]
        result.set(`sequence:${source.id}:${task.id}`, { id: `sequence:${source.id}:${task.id}`, source: source.id, target: task.id, type: 'smoothstep', markerEnd: { type: MarkerType.ArrowClosed }, className: 'derived-edge', label: 'sequence', style: { stroke: '#a3aaa4', strokeDasharray: '3 4' } })
      })
    })
    return [...result.values()]
  }, [snapshot.edges, activePipelines, selectedPipelineId, ancestorInScope, scope, taskMap, visibleTasks])

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(graphEdges)
  useEffect(() => setNodes(initialNodes), [initialNodes, setNodes])
  useEffect(() => setEdges(graphEdges), [graphEdges, setEdges])
  const savedViewport = useMemo(
    () => (snapshot.viewports ?? []).find((viewport) => (viewport.parent_id ?? null) === scope),
    [snapshot.viewports, scope],
  )

  const connect = (connection: Connection) => {
    if (!connection.source || !connection.target || connection.source === connection.target) return
    const [sourceTaskId, targetTaskId] = normalizedEndpoints(connection.source, connection.target, edgeType)
    semanticMutation.mutate(operation('edge.create', { source_task_id: sourceTaskId, target_task_id: targetTaskId, edge_type: edgeType }, { id: crypto.randomUUID() }))
  }
  const savePosition = (_event: MouseEvent | TouchEvent, node: Node) => {
    const saved = snapshot.layouts.find((layout) => layout.task_id === node.id && (layout.parent_id ?? null) === scope)
    layoutMutation.mutate(operation('layout.upsert', { task_id: node.id, parent_id: scope, x: node.position.x, y: node.position.y }, { id: saved?.id ?? crypto.randomUUID(), version: saved?.version }))
  }
  const saveViewport = (viewport: Viewport) => {
    if (!scope && selectedPipelineId !== 'all') return
    if (
      savedViewport
      && Math.abs(savedViewport.x - viewport.x) < 0.01
      && Math.abs(savedViewport.y - viewport.y) < 0.01
      && Math.abs(savedViewport.zoom - viewport.zoom) < 0.0001
    ) return
    layoutMutation.mutate(operation('viewport.upsert', {
      parent_id: scope,
      x: viewport.x,
      y: viewport.y,
      zoom: viewport.zoom,
    }, { id: savedViewport?.id ?? crypto.randomUUID(), version: savedViewport?.version }))
  }
  const openEdgeEditor = (_event: React.MouseEvent, edge: Edge) => {
    const explicit = edge.data?.explicitId as string | undefined
    if (!explicit) return
    const source = snapshot.edges.find((item) => item.id === explicit)
    if (source) setEditingEdge(source)
  }
  const autoLayout = () => {
    setLayouting(true)
    setLayoutError(null)
    const worker = new Worker(new URL('../lib/layout.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = (event: MessageEvent<{ ok: boolean; positions?: Record<string, { x: number; y: number }>; message?: string }>) => {
      setLayouting(false)
      worker.terminate()
      if (!event.data.ok || !event.data.positions) {
        setLayoutError(event.data.message ?? 'Auto-layout failed')
        return
      }
      const arranged = nodes.map((node) => ({ ...node, position: event.data.positions![node.id] ?? node.position }))
      setNodes(arranged)
      layoutMutation.mutate(arranged.map((node) => {
        const saved = snapshot.layouts.find((layout) => layout.task_id === node.id && (layout.parent_id ?? null) === scope)
        return operation('layout.upsert', { task_id: node.id, parent_id: scope, x: node.position.x, y: node.position.y }, { id: saved?.id ?? crypto.randomUUID(), version: saved?.version })
      }))
    }
    worker.onerror = () => {
      worker.terminate()
      setLayouting(false)
      setLayoutError('The layout worker could not start.')
    }
    worker.postMessage({
      nodes: nodes.map((node) => node.id),
      edges: edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target })),
    })
  }
  const breadcrumbs = useMemo(() => {
    const result: Task[] = []
    let task = scope ? taskMap.get(scope) : undefined
    while (task) { result.unshift(task); task = task.parent_id ? taskMap.get(task.parent_id) : undefined }
    return result
  }, [scope, taskMap])
  const selectedPipeline = selectedPipelineId === 'all' ? undefined : activePipelines.find((pipeline) => pipeline.id === selectedPipelineId)
  const choosePipeline = (pipelineId: string) => {
    setSelectedTaskId(null)
    setSelectedPipelineId(pipelineId)
    setScope(null)
  }

  if (!taskMap.size) return <div className="view-page"><EmptyState icon={<Network size={28} />} title="No active tasks to graph" description="Create tasks in the Outline, or restore an archived pipeline, then return to connect dependencies and explore the structure." /></div>
  return (
    <div className="graph-view">
      <header className="view-toolbar graph-toolbar"><div><h2>Task graph</h2><p>Focus on one pipeline and open nested work one level at a time.</p></div><div className="button-row"><label className="graph-pipeline-selector"><span>Pipeline</span><select aria-label="Graph pipeline" value={selectedPipelineId} onChange={(event) => choosePipeline(event.target.value)}><option value="all">All pipelines</option>{activePipelines.map((pipeline) => <option key={pipeline.id} value={pipeline.id}>{pipeline.title}</option>)}</select></label><div className="segmented"><button className={edgeType === 'dependency' ? 'active' : ''} onClick={() => setEdgeType('dependency')}><Link2 size={14} />Dependency</button><button className={edgeType === 'related' ? 'active' : ''} onClick={() => setEdgeType('related')}><Unlink2 size={14} />Related</button></div><Button variant="secondary" onClick={autoLayout} disabled={layouting || !visibleTasks.length}><LayoutGrid size={16} />{layouting ? 'Laying out…' : 'Auto-layout'}</Button></div></header>
      {(semanticMutation.error || layoutMutation.error || layoutError) && <Notice tone="danger">{(semanticMutation.error || layoutMutation.error)?.message ?? layoutError}</Notice>}
      <div className="graph-breadcrumbs"><button onClick={() => choosePipeline('all')}>Project</button>{selectedPipeline && <span>/ <button onClick={() => setScope(null)}>{selectedPipeline.title}</button></span>}{breadcrumbs.map((task) => <span key={task.id}>/ <button onClick={() => setScope(task.id)}>{task.title}</button></span>)}</div>
      <div className="graph-canvas">
        <ReactFlow key={`${selectedPipelineId}:${scope ?? 'root'}`} nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={connect} onNodeClick={(_event, node) => setSelectedTaskId(node.id)} onNodeDoubleClick={(_event, node) => { if ((children.get(node.id) ?? []).length) setScope(node.id) }} onPaneClick={() => setSelectedTaskId(null)} onNodeDragStop={savePosition} onMoveEnd={(_event, viewport) => saveViewport(viewport)} onEdgeDoubleClick={openEdgeEditor} defaultViewport={savedViewport && (scope || selectedPipelineId === 'all') ? { x: savedViewport.x, y: savedViewport.y, zoom: savedViewport.zoom } : undefined} fitView={!savedViewport || (!scope && selectedPipelineId !== 'all')} minZoom={0.25} maxZoom={1.8} zoomOnDoubleClick={false} deleteKeyCode={null}>
          <Background gap={22} color="#dce1d9" />
          <Controls showInteractive={false} />
          <MiniMap nodeColor={(node) => node.style?.borderColor as string ?? '#7b8b7e'} pannable zoomable />
          <Panel position="top-left">{scope && <Button size="sm" variant="secondary" onClick={() => setScope(taskMap.get(scope)?.parent_id ?? null)}><ArrowLeft size={14} />Up one level</Button>}</Panel>
        </ReactFlow>
        {!visibleTasks.length && <div className="graph-empty-level">No tasks at this level{selectedPipeline ? ` in ${selectedPipeline.title}` : ''}.</div>}
      </div>
      <p className="graph-help"><GitBranch size={14} />Hover or focus a parent to preview its subtasks. Select with one click; double-click, Enter, Space, or use the subtask count to open it. Right-click or use … for task actions. Drag between handles to create a {edgeType} edge.</p>
      <RelationshipPanel
        tasks={[...taskMap.values()]}
        taskById={allTaskMap}
        pipelines={snapshot.pipelines}
        edges={snapshot.edges}
        onCreate={(sourceTaskId, targetTaskId, relationshipType) => {
          semanticMutation.mutate(operation('edge.create', {
            source_task_id: sourceTaskId,
            target_task_id: targetTaskId,
            edge_type: relationshipType,
          }, { id: crypto.randomUUID() }))
        }}
        onEdit={setEditingEdge}
        onDelete={(edge) => {
          if (window.confirm('Remove this explicit relationship?')) {
            semanticMutation.mutate(operation('edge.delete', {}, { id: edge.id, version: edge.version }))
          }
        }}
      />
      {disabledEdges.length > 0 && <section className="disabled-edge-panel">
        <header><div><h3>Relationships needing resolution</h3><p>These restored relationships remain recorded but disabled because an endpoint is missing or enabling them may create a cycle.</p></div><Badge tone="amber">{disabledEdges.length}</Badge></header>
        <div>{disabledEdges.map((edge) => <article key={edge.id}><span><strong>{taskMap.get(edge.source_task_id)?.title ?? 'Unavailable task'} → {taskMap.get(edge.target_task_id)?.title ?? 'Unavailable task'}</strong><small>{humanize(edge.disabled_reason ?? 'restore conflict')} · {humanize(edge.edge_type)}</small></span><div className="button-row"><Button size="sm" variant="secondary" onClick={() => semanticMutation.mutate(operation('edge.update', { disabled: false }, { id: edge.id, version: edge.version }))}>Try enabling</Button><Button size="sm" variant="ghost" onClick={() => window.confirm('Remove this disabled relationship permanently?') && semanticMutation.mutate(operation('edge.delete', {}, { id: edge.id, version: edge.version }))}>Remove</Button></div></article>)}</div>
      </section>}
      <EdgeEditor
        edge={editingEdge}
        onClose={() => setEditingEdge(null)}
        onSave={(edge, waiverReason) => {
          semanticMutation.mutate(operation('edge.update', { waiver_reason: waiverReason }, { id: edge.id, version: edge.version }))
          setEditingEdge(null)
        }}
        onDelete={(edge) => {
          semanticMutation.mutate(operation('edge.delete', {}, { id: edge.id, version: edge.version }))
          setEditingEdge(null)
        }}
      />
      <TaskEditor snapshot={snapshot} state={taskDialog} onClose={() => setTaskDialog({ open: false })} compact />
    </div>
  )
}

function GraphNode({ task, subtasks, childCounts, selected, nestedLinks, externalLinks, onSelect, onOpen, onEdit, onAddSubtask, onDelete, onAskCodex }: {
  task: Task
  subtasks: Task[]
  childCounts: Map<string | null, Task[]>
  selected: boolean
  nestedLinks: number
  externalLinks: number
  onSelect: () => void
  onOpen: () => void
  onEdit: () => void
  onAddSubtask: () => void
  onDelete: () => void
  onAskCodex?: () => void
}) {
  const childCount = subtasks.length
  const previewTasks = subtasks.slice(0, 6)
  const remainingCount = Math.max(0, childCount - previewTasks.length)
  const [previewOpen, setPreviewOpen] = useState(false)
  const previewTriggerHovered = useRef(false)
  const previewPopupHovered = useRef(false)
  const previewCloseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelPreviewClose = () => {
    if (previewCloseTimer.current === null) return
    clearTimeout(previewCloseTimer.current)
    previewCloseTimer.current = null
  }
  const closePreviewAfterDelay = () => {
    cancelPreviewClose()
    previewCloseTimer.current = setTimeout(() => {
      previewCloseTimer.current = null
      setPreviewOpen(false)
    }, 200)
  }
  useEffect(() => () => {
    if (previewCloseTimer.current !== null) clearTimeout(previewCloseTimer.current)
  }, [])
  const actionProps = { task, childCount, onEdit, onAddSubtask, onDelete, onOpenSubtasks: onOpen, onAskCodex }
  const mainButton = (
    <button
      type="button"
      className="graph-node-main nodrag nopan"
      aria-label={`Select ${task.title}`}
      aria-pressed={selected}
      onMouseEnter={() => {
        previewTriggerHovered.current = true
        cancelPreviewClose()
      }}
      onMouseLeave={() => { previewTriggerHovered.current = false }}
      onClick={(event) => {
        event.stopPropagation()
        // PreviewCard composes trigger handlers around this button. Browsers
        // still emit a detail=0 click for keyboard activation even when the
        // composed keydown handler consumes Enter/Space first, so keep the
        // documented keyboard drill behavior reliable at that native seam.
        if (childCount && event.detail === 0) onOpen()
        else onSelect()
      }}
      onDoubleClick={(event) => {
        event.stopPropagation()
        if (childCount) onOpen()
      }}
      onKeyDown={(event) => {
        if (childCount && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          event.stopPropagation()
          onOpen()
        }
      }}
    >
      <strong>{task.title}</strong>
      <small>{humanize(task.readiness)}{task.target_date ? ` · Target ${formatCalendarDate(task.target_date)}` : ''}{nestedLinks ? ` · ${nestedLinks} nested link${nestedLinks === 1 ? '' : 's'}` : ''}{externalLinks ? ` · ${externalLinks} external link${externalLinks === 1 ? '' : 's'}` : ''}</small>
    </button>
  )
  return (
    <TaskContextRegion {...actionProps}>
      <div className="graph-node-content">
        <div className="graph-node-heading">
          <span><Badge tone={statusTone[task.status]}>{humanize(task.status)}</Badge>{task.user_key && <code>{task.user_key}</code>}</span>
          <TaskActionsButton {...actionProps} />
        </div>
        {childCount > 0 ? (
          <PreviewCard.Root
            open={previewOpen}
            onOpenChange={(open, details) => {
              const hoveringPreview = previewTriggerHovered.current || previewPopupHovered.current
              if (!open && hoveringPreview && details.reason === 'trigger-hover') return
              if (!open) {
                cancelPreviewClose()
                previewTriggerHovered.current = false
                previewPopupHovered.current = false
              }
              setPreviewOpen(open)
            }}
          >
            <PreviewCard.Trigger delay={300} closeDelay={200} render={mainButton} />
            <PreviewCard.Portal>
              <PreviewCard.Positioner className="graph-node-preview-positioner" side="right" align="start" sideOffset={10} collisionPadding={12}>
                <PreviewCard.Popup
                  className="graph-node-preview"
                  role="dialog"
                  aria-label={`Subtasks for ${task.title}`}
                  onMouseEnter={() => {
                    previewPopupHovered.current = true
                    cancelPreviewClose()
                    setPreviewOpen(true)
                  }}
                  onMouseLeave={() => {
                    previewPopupHovered.current = false
                    closePreviewAfterDelay()
                  }}
                >
                  <PreviewCard.Arrow className="graph-node-preview-arrow" />
                  <header>
                    <div><small>Subtasks for</small><strong>{task.user_key ? `${task.user_key} · ` : ''}{task.title}</strong></div>
                    <Badge tone={task.child_flow_mode === 'sequential' ? 'blue' : 'neutral'}>{humanize(task.child_flow_mode)}</Badge>
                  </header>
                  {task.child_flow_mode === 'freeform' && <p className="graph-node-preview-note">These subtasks have no automatic order unless explicit dependencies connect them.</p>}
                  <ol className={`graph-node-preview-list ${task.child_flow_mode}`}>
                    {previewTasks.map((subtask) => {
                      const deeperCount = (childCounts.get(subtask.id) ?? []).length
                      return (
                        <li key={subtask.id}>
                          <div><span>{subtask.user_key && <code>{subtask.user_key}</code>}<strong>{subtask.title}</strong></span><Badge tone={statusTone[subtask.status]}>{humanize(subtask.status)}</Badge></div>
                          <small><span>{humanize(subtask.readiness)}</span>{subtask.target_date && <span>Target {formatCalendarDate(subtask.target_date)}</span>}{deeperCount > 0 && <span>{deeperCount} subtask{deeperCount === 1 ? '' : 's'}</span>}</small>
                        </li>
                      )
                    })}
                  </ol>
                  {remainingCount > 0 && <p className="graph-node-preview-more">+{remainingCount} more</p>}
                </PreviewCard.Popup>
              </PreviewCard.Positioner>
            </PreviewCard.Portal>
          </PreviewCard.Root>
        ) : mainButton}
        {childCount > 0 && <button type="button" className="graph-node-open nodrag nopan" aria-label={`View ${childCount} subtask${childCount === 1 ? '' : 's'} for ${task.title}`} onClick={(event) => { event.stopPropagation(); onOpen() }}>{childCount} subtask{childCount === 1 ? '' : 's'}</button>}
      </div>
    </TaskContextRegion>
  )
}

function RelationshipPanel({ tasks, taskById, pipelines, edges, onCreate, onEdit, onDelete }: {
  tasks: Task[]
  taskById: Map<string, Task>
  pipelines: ProjectSnapshot['pipelines']
  edges: TaskEdge[]
  onCreate: (sourceTaskId: string, targetTaskId: string, edgeType: 'dependency' | 'related') => void
  onEdit: (edge: TaskEdge) => void
  onDelete: (edge: TaskEdge) => void
}) {
  const [sourceTaskId, setSourceTaskId] = useState('')
  const [targetTaskId, setTargetTaskId] = useState('')
  const [sourceSearch, setSourceSearch] = useState('')
  const [targetSearch, setTargetSearch] = useState('')
  const [relationshipType, setRelationshipType] = useState<'dependency' | 'related'>('dependency')
  const [edgeSearch, setEdgeSearch] = useState('')
  const labelTask = useMemo(() => createTaskLabeler(pipelines, [...taskById.values()]), [pipelines, taskById])
  const orderedTasks = useMemo(
    () => tasks.slice().sort((left, right) => labelTask(left).localeCompare(labelTask(right))),
    [labelTask, tasks],
  )
  const activeEdges = useMemo(
    () => edges.filter((edge) => !edge.deleted_at).sort((left, right) => {
      const leftLabel = `${labelTask(taskById.get(left.source_task_id))} ${labelTask(taskById.get(left.target_task_id))}`
      const rightLabel = `${labelTask(taskById.get(right.source_task_id))} ${labelTask(taskById.get(right.target_task_id))}`
      return leftLabel.localeCompare(rightLabel)
    }),
    [edges, labelTask, taskById],
  )
  const sourceOptions = filterTasks(orderedTasks, sourceSearch, sourceTaskId, labelTask)
  const targetOptions = filterTasks(orderedTasks, targetSearch, targetTaskId, labelTask)
  const duplicate = Boolean(sourceTaskId && targetTaskId && activeEdges.some((edge) => (
    edge.edge_type === relationshipType
    && (relationshipType === 'related'
      ? new Set([edge.source_task_id, edge.target_task_id]).size === 2
        && [edge.source_task_id, edge.target_task_id].includes(sourceTaskId)
        && [edge.source_task_id, edge.target_task_id].includes(targetTaskId)
      : edge.source_task_id === sourceTaskId && edge.target_task_id === targetTaskId)
  )))
  const shownEdges = activeEdges.filter((edge) => {
    const text = `${labelTask(taskById.get(edge.source_task_id))} ${labelTask(taskById.get(edge.target_task_id))} ${edge.edge_type} ${edge.waiver_reason ?? ''}`
    return text.toLowerCase().includes(edgeSearch.trim().toLowerCase())
  })
  const create = (event: React.FormEvent) => {
    event.preventDefault()
    if (!sourceTaskId || !targetTaskId || sourceTaskId === targetTaskId || duplicate) return
    const [source, target] = normalizedEndpoints(sourceTaskId, targetTaskId, relationshipType)
    onCreate(source, target, relationshipType)
    setSourceTaskId('')
    setTargetTaskId('')
    setSourceSearch('')
    setTargetSearch('')
  }

  return (
    <details className="relationship-panel">
      <summary>
        <span><Network size={16} /><span><strong>Explicit relationships</strong><small>Create or manage dependencies and related-task links across every hierarchy level.</small></span></span>
        <Badge tone="neutral">{activeEdges.length}</Badge>
      </summary>
      <div className="relationship-panel-body">
        <form className="relationship-create-form" onSubmit={create}>
          <div><h3>Add relationship</h3><p>A dependency points from the prerequisite to the dependent task.</p></div>
          <Field label="Relationship type">
            <select value={relationshipType} onChange={(event) => setRelationshipType(event.target.value as typeof relationshipType)}>
              <option value="dependency">Dependency (directed prerequisite)</option>
              <option value="related">Related (undirected, no readiness effect)</option>
            </select>
          </Field>
          <div className="relationship-task-picker">
            <Field label="Find source task"><input value={sourceSearch} onChange={(event) => setSourceSearch(event.target.value)} placeholder="Filter by key, title, or label…" /></Field>
            <Field label="Source / prerequisite task"><select required value={sourceTaskId} onChange={(event) => setSourceTaskId(event.target.value)}><option value="">Choose source task</option>{sourceOptions.map((task) => <option value={task.id} key={task.id}>{labelTask(task)}</option>)}</select></Field>
            <Field label="Find target task"><input value={targetSearch} onChange={(event) => setTargetSearch(event.target.value)} placeholder="Filter by key, title, or label…" /></Field>
            <Field label="Target / dependent task"><select required value={targetTaskId} onChange={(event) => setTargetTaskId(event.target.value)}><option value="">Choose target task</option>{targetOptions.map((task) => <option value={task.id} key={task.id}>{labelTask(task)}</option>)}</select></Field>
          </div>
          {sourceTaskId && sourceTaskId === targetTaskId && <Notice tone="warning">Choose two different tasks.</Notice>}
          {duplicate && <Notice tone="warning">That explicit relationship already exists.</Notice>}
          <Button type="submit" disabled={!sourceTaskId || !targetTaskId || sourceTaskId === targetTaskId || duplicate}><Plus size={15} />Add relationship</Button>
        </form>
        <section className="relationship-list" aria-label="All explicit relationships">
          <header><div><h3>Recorded relationships</h3><p>Hidden and cross-level endpoints remain editable here.</p></div><input aria-label="Filter relationships" value={edgeSearch} onChange={(event) => setEdgeSearch(event.target.value)} placeholder="Filter relationships…" /></header>
          <div>
            {shownEdges.map((edge) => <article key={edge.id}>
              <span><strong>{labelTask(taskById.get(edge.source_task_id))} {edge.edge_type === 'dependency' ? '→' : '↔'} {labelTask(taskById.get(edge.target_task_id))}</strong><small><Badge tone={edge.edge_type === 'dependency' ? 'blue' : 'purple'}>{humanize(edge.edge_type)}</Badge>{edge.waived && <Badge tone="amber">Waived</Badge>}{edge.disabled && <Badge tone="amber">Disabled</Badge>}{edge.waiver_reason && <span>{edge.waiver_reason}</span>}</small></span>
              <div className="button-row">{edge.edge_type === 'dependency' && <Button type="button" size="sm" variant="secondary" onClick={() => onEdit(edge)}>{edge.waived ? 'Edit waiver' : 'Waiver settings'}</Button>}<Button type="button" size="sm" variant="ghost" onClick={() => onDelete(edge)}><Unlink2 size={13} />Remove</Button></div>
            </article>)}
            {!shownEdges.length && <p className="relationship-empty">{activeEdges.length ? 'No relationships match this filter.' : 'No explicit relationships yet.'}</p>}
          </div>
        </section>
      </div>
    </details>
  )
}

function filterTasks(tasks: Task[], search: string, selectedId: string, labelTask: (task?: Task) => string) {
  const query = search.trim().toLowerCase()
  if (!query) return tasks
  return tasks.filter((task) => task.id === selectedId || taskPickerSearchText(task, labelTask).includes(query))
}

function normalizedEndpoints(sourceTaskId: string, targetTaskId: string, edgeType: 'dependency' | 'related'): [string, string] {
  if (edgeType === 'related' && sourceTaskId.localeCompare(targetTaskId) > 0) return [targetTaskId, sourceTaskId]
  return [sourceTaskId, targetTaskId]
}

function EdgeEditor({ edge, onClose, onSave, onDelete }: { edge: TaskEdge | null; onClose: () => void; onSave: (edge: TaskEdge, waiverReason: string) => void; onDelete: (edge: TaskEdge) => void }) {
  const [waiverReason, setWaiverReason] = useState('')
  useEffect(() => setWaiverReason(edge?.waiver_reason ?? ''), [edge])
  if (!edge) return null
  const dependency = edge.edge_type === 'dependency'
  return <Dialog open onClose={onClose} title={dependency ? 'Dependency settings' : 'Related-task link'} description={dependency ? 'A waived dependency remains recorded but no longer affects readiness.' : 'Related links never affect task readiness.'}><form className="form-stack" onSubmit={(event) => { event.preventDefault(); if (dependency && (edge.waived || waiverReason.trim())) onSave(edge, waiverReason.trim()) }}>{dependency && <Field label="Waiver reason" hint="A reason is required to waive an active dependency. Clear an existing reason to make the dependency active again."><textarea required={!edge.waived} rows={3} value={waiverReason} onChange={(event) => setWaiverReason(event.target.value)} placeholder="Why may downstream work proceed without this prerequisite?" /></Field>}{dependency && waiverReason.trim() && <Notice tone="warning">This prerequisite will be ignored by readiness until the waiver is removed.</Notice>}<div className="dialog-actions split"><Button type="button" variant="danger" onClick={() => { if (window.confirm('Remove this explicit relationship?')) onDelete(edge) }}><Unlink2 size={15} />Delete edge</Button><div className="button-row"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>{dependency && <Button type="submit" disabled={!edge.waived && !waiverReason.trim()}>{edge.waived ? waiverReason.trim() ? 'Update waiver' : 'Remove waiver' : 'Waive dependency'}</Button>}</div></div></form></Dialog>
}

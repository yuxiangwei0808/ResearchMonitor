/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectSnapshot } from '../types'
import { api } from '../lib/api'
import { GraphView } from './GraphView'

type MockNode = { id: string; position: { x: number; y: number }; data?: { label?: ReactNode } }
type PositionChange = { id: string; position: { x: number; y: number } }

vi.mock('@xyflow/react', async () => {
  const React = await import('react')
  return {
    Background: () => null,
    Controls: () => null,
    MarkerType: { ArrowClosed: 'arrowclosed' },
    MiniMap: () => null,
    Panel: ({ children }: { children?: ReactNode }) => <>{children}</>,
    ReactFlow: ({ nodes, onNodesChange, onNodeDragStop, onMoveEnd, onPaneClick, children }: {
      nodes: MockNode[]
      onNodesChange: (changes: PositionChange[]) => void
      onNodeDragStop: (event: MouseEvent, node: MockNode) => void
      onMoveEnd: (event: MouseEvent, viewport: { x: number; y: number; zoom: number }) => void
      onPaneClick?: () => void
      children?: ReactNode
    }) => (
      <div>
        <div>{nodes.map((node) => <div key={node.id}>{node.data?.label}</div>)}</div>
        <button
          type="button"
          onClick={() => onNodesChange([{ id: nodes[0].id, position: { x: 999, y: 111 } }])}
        >
          Move node
        </button>
        <button type="button" onClick={() => onNodeDragStop(new MouseEvent('mouseup'), nodes[0])}>Save node layout</button>
        <button type="button" onClick={() => onMoveEnd(new MouseEvent('mouseup'), { x: 42, y: -17, zoom: 1.25 })}>Save viewport</button>
        <button type="button" onClick={onPaneClick}>Clear graph selection</button>
        <output data-testid="node-position">{nodes[0].position.x},{nodes[0].position.y}</output>
        {children}
      </div>
    ),
    useEdgesState: <T,>(initial: T[]) => {
      const [edges, setEdges] = React.useState(initial)
      return [edges, setEdges, React.useCallback(() => undefined, [])]
    },
    useNodesState: <T extends MockNode,>(initial: T[]) => {
      const [nodes, setNodes] = React.useState(initial)
      const onNodesChange = React.useCallback((changes: PositionChange[]) => {
        setNodes((current) => current.map((node) => {
          const change = changes.find((item) => item.id === node.id)
          return change ? { ...node, position: change.position } : node
        }))
      }, [])
      return [nodes, setNodes, onNodesChange]
    },
  }
})

const snapshot: ProjectSnapshot = {
  project: {
    id: '11111111-1111-4111-8111-111111111111',
    name: 'Graph test',
    root_path: '/tmp/graph-test',
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
  artifact_roots: [],
  pipelines: [{
    id: '22222222-2222-4222-8222-222222222222',
    project_id: '11111111-1111-4111-8111-111111111111',
    title: 'Pipeline',
    flow_mode: 'freeform',
    position: 0,
    archived: false,
    version: 1,
  }],
  tasks: [{
    id: '33333333-3333-4333-8333-333333333333',
    project_id: '11111111-1111-4111-8111-111111111111',
    pipeline_id: '22222222-2222-4222-8222-222222222222',
    title: 'Task',
    kind: 'task',
    status: 'planned',
    priority: 'required',
    labels: [],
    position: 0,
    child_flow_mode: 'freeform',
    readiness: 'ready',
    unsatisfied_predecessor_ids: [],
    version: 1,
  }],
  edges: [],
  journals: [],
  artifacts: [],
  task_artifacts: [],
  layouts: [],
  progress: { leaf_total: 1, leaf_done: 0, ready: 1, waiting: 0, blocked: 0, review: 0 },
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('GraphView', () => {
  it('keeps a local node move until genuine snapshot data changes', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <GraphView snapshot={snapshot} />
      </QueryClientProvider>,
    )

    expect(screen.getByTestId('node-position').textContent).toBe('0,0')
    fireEvent.click(screen.getByRole('button', { name: 'Move node' }))
    expect(screen.getByTestId('node-position').textContent).toBe('999,111')
  })

  it('focuses the first sorted pipeline by default and keeps cross-pipeline links visible as indicators', () => {
    const earlierPipeline = {
      ...snapshot.pipelines[0],
      id: '22222222-2222-4222-8222-222222222221',
      title: 'Earlier pipeline',
      position: -1,
    }
    const earlierTask = {
      ...snapshot.tasks[0],
      id: '33333333-3333-4333-8333-333333333331',
      pipeline_id: earlierPipeline.id,
      title: 'Earlier task',
    }
    const value: ProjectSnapshot = {
      ...snapshot,
      pipelines: [snapshot.pipelines[0], earlierPipeline],
      tasks: [snapshot.tasks[0], earlierTask],
      edges: [{
        id: '66666666-6666-4666-8666-666666666661',
        project_id: snapshot.project.id,
        source_task_id: earlierTask.id,
        target_task_id: snapshot.tasks[0].id,
        edge_type: 'dependency',
        version: 1,
      }],
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><GraphView snapshot={value} /></QueryClientProvider>)

    const selector = screen.getByLabelText('Graph pipeline') as HTMLSelectElement
    expect(selector.value).toBe(earlierPipeline.id)
    const earlierButton = screen.getByRole('button', { name: 'Select Earlier task' })
    expect(earlierButton).not.toBeNull()
    expect(screen.queryByRole('button', { name: 'Select Task' })).toBeNull()
    expect(screen.getByText(/1 external link/)).not.toBeNull()

    fireEvent.click(earlierButton)
    expect(earlierButton.getAttribute('aria-pressed')).toBe('true')
    fireEvent.change(selector, { target: { value: 'all' } })
    expect(screen.getByRole('button', { name: 'Select Earlier task' }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: 'Select Task' })).not.toBeNull()
    expect(screen.queryByText(/external link/)).toBeNull()
  })

  it('selects without editing and navigates arbitrary depth with double-click, keyboard, and the count button', () => {
    const mutate = vi.spyOn(api, 'mutate')
    const mutateLayout = vi.spyOn(api, 'mutateLayout')
    const parent = { ...snapshot.tasks[0], title: 'Parent task' }
    const child = {
      ...snapshot.tasks[0],
      id: '44444444-4444-4444-8444-444444444441',
      parent_id: parent.id,
      title: 'Child task',
      position: 0,
    }
    const grandchild = {
      ...snapshot.tasks[0],
      id: '44444444-4444-4444-8444-444444444442',
      parent_id: child.id,
      title: 'Grandchild task',
      position: 0,
    }
    const rootLeaf = {
      ...snapshot.tasks[0],
      id: '44444444-4444-4444-8444-444444444443',
      title: 'Root leaf',
      position: 1,
    }
    const value = { ...snapshot, tasks: [parent, child, grandchild, rootLeaf] }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><GraphView snapshot={value} /></QueryClientProvider>)

    let parentButton = screen.getByRole('button', { name: 'Select Parent task' })
    expect(parentButton.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(parentButton)
    expect(parentButton.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Select Root leaf' })).not.toBeNull()
    expect(screen.queryByRole('button', { name: 'Select Child task' })).toBeNull()
    expect(screen.queryByRole('dialog', { name: 'Task details' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Clear graph selection' }))
    expect(parentButton.getAttribute('aria-pressed')).toBe('false')

    fireEvent.keyDown(parentButton, { key: 'Enter' })
    let childButton = screen.getByRole('button', { name: 'Select Child task' })
    expect(within(document.querySelector('.graph-breadcrumbs')!).getByRole('button', { name: 'Parent task' })).not.toBeNull()
    fireEvent.keyDown(childButton, { key: ' ' })
    const grandchildButton = screen.getByRole('button', { name: 'Select Grandchild task' })
    expect(within(document.querySelector('.graph-breadcrumbs')!).getByRole('button', { name: 'Child task' })).not.toBeNull()

    fireEvent.click(grandchildButton)
    expect(grandchildButton.getAttribute('aria-pressed')).toBe('true')
    fireEvent.doubleClick(grandchildButton)
    fireEvent.keyDown(grandchildButton, { key: 'Enter' })
    expect(screen.queryByRole('dialog', { name: 'Task details' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Select Grandchild task' })).not.toBeNull()

    fireEvent.click(within(document.querySelector('.graph-breadcrumbs')!).getByRole('button', { name: 'Pipeline' }))
    parentButton = screen.getByRole('button', { name: 'Select Parent task' })
    expect(parentButton.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(screen.getByRole('button', { name: 'View 1 subtask for Parent task' }))
    expect(screen.getByRole('button', { name: 'Select Child task' })).not.toBeNull()

    fireEvent.click(within(document.querySelector('.graph-breadcrumbs')!).getByRole('button', { name: 'Pipeline' }))
    parentButton = screen.getByRole('button', { name: 'Select Parent task' })
    fireEvent.doubleClick(parentButton)
    childButton = screen.getByRole('button', { name: 'Select Child task' })
    expect(childButton).not.toBeNull()
    expect(mutate).not.toHaveBeenCalled()
    expect(mutateLayout).not.toHaveBeenCalled()
  })

  it('previews six ordered immediate children with timing, metadata, flow, deeper counts, and portal dismissal', () => {
    vi.useFakeTimers()
    const parent = {
      ...snapshot.tasks[0],
      title: 'Preview parent',
      user_key: 'P-0',
      child_flow_mode: 'sequential' as const,
    }
    const subtasks = Array.from({ length: 8 }, (_, index) => {
      const position = 7 - index
      return {
        ...snapshot.tasks[0],
        id: `44444444-4444-4444-8444-44444444445${position}`,
        parent_id: parent.id,
        user_key: `C-${position}`,
        title: `Child ${position}`,
        position,
        status: position === 0 ? 'review' as const : 'planned' as const,
        readiness: position === 0 ? 'waiting' as const : 'ready' as const,
        target_date: position === 0 ? '2026-08-15' : null,
      }
    })
    const grandchild = {
      ...snapshot.tasks[0],
      id: '55555555-5555-4555-8555-555555555555',
      parent_id: subtasks.find((task) => task.position === 0)!.id,
      title: 'Nested child',
      position: 0,
    }
    const value: ProjectSnapshot = { ...snapshot, tasks: [parent, ...subtasks, grandchild] }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><GraphView snapshot={value} /></QueryClientProvider>)

    const trigger = screen.getByRole('button', { name: 'Select Preview parent' })
    fireEvent.mouseEnter(trigger)
    act(() => vi.advanceTimersByTime(299))
    expect(screen.queryByRole('dialog', { name: 'Subtasks for Preview parent' })).toBeNull()
    act(() => vi.advanceTimersByTime(1))

    const preview = screen.getByRole('dialog', { name: 'Subtasks for Preview parent' })
    expect(preview.closest('.graph-node-content')).toBeNull()
    expect(within(preview).getByText('Sequential')).not.toBeNull()
    const items = within(preview).getAllByRole('listitem')
    expect(items).toHaveLength(6)
    expect(items.map((item) => within(item).getByRole('strong').textContent)).toEqual([
      'Child 0', 'Child 1', 'Child 2', 'Child 3', 'Child 4', 'Child 5',
    ])
    expect(items[0].textContent).toContain('C-0')
    expect(items[0].textContent).toContain('Review')
    expect(items[0].textContent).toContain('Waiting')
    expect(items[0].textContent).toContain('Target Aug 15, 2026')
    expect(items[0].textContent).toContain('1 subtask')
    expect(within(preview).getByText('+2 more')).not.toBeNull()
    expect(within(preview).queryByText('Child 6')).toBeNull()
    expect(within(preview).queryByText('Nested child')).toBeNull()

    fireEvent.mouseLeave(trigger)
    act(() => vi.advanceTimersByTime(199))
    expect(screen.getByRole('dialog', { name: 'Subtasks for Preview parent' })).not.toBeNull()
    fireEvent.mouseEnter(preview)
    act(() => vi.advanceTimersByTime(1))
    expect(screen.getByRole('dialog', { name: 'Subtasks for Preview parent' })).not.toBeNull()
    fireEvent.mouseLeave(preview)
    act(() => vi.advanceTimersByTime(200))
    expect(screen.queryByRole('dialog', { name: 'Subtasks for Preview parent' })).toBeNull()

    fireEvent.focus(trigger)
    act(() => vi.advanceTimersByTime(300))
    expect(screen.getByRole('dialog', { name: 'Subtasks for Preview parent' })).not.toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Subtasks for Preview parent' })).toBeNull()
  })

  it('offers add, edit, drill, and versioned delete actions through ellipsis and right-click menus', async () => {
    const parent = { ...snapshot.tasks[0], title: 'Action parent', version: 7 }
    const child = {
      ...snapshot.tasks[0],
      id: '44444444-4444-4444-8444-444444444442',
      parent_id: parent.id,
      title: 'Action child',
      position: 0,
    }
    const value = { ...snapshot, tasks: [parent, child] }
    const mutate = vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: '88888888-8888-4888-8888-888888888888',
      project_id: snapshot.project.id,
      semantic_revision: snapshot.project.semantic_revision + 1,
      layout_revision: snapshot.project.layout_revision,
      results: [],
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><GraphView snapshot={value} /></QueryClientProvider>)

    fireEvent.click(screen.getByRole('button', { name: 'Actions for Action parent' }))
    let menu = await screen.findByRole('menu', { name: 'Actions for Action parent' })
    expect(within(menu).getByText('Edit task')).not.toBeNull()
    expect(within(menu).getByText('View 1 subtask')).not.toBeNull()
    fireEvent.click(within(menu).getByText('Add subtask'))

    let dialog = screen.getByRole('dialog', { name: 'New subtask' })
    expect((within(dialog).getByRole('combobox', { name: /Parent task/ }) as HTMLSelectElement).value).toBe(parent.id)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    const contextRegion = screen.getByRole('button', { name: 'Select Action parent' }).closest('.graph-node-content')
    expect(contextRegion).not.toBeNull()
    fireEvent.contextMenu(contextRegion!, { clientX: 20, clientY: 20 })
    menu = await screen.findByRole('menu', { name: 'Actions for Action parent' })
    fireEvent.click(within(menu).getByText('Edit task'))
    dialog = screen.getByRole('dialog', { name: 'Task details' })
    expect((within(dialog).getByLabelText('Task title') as HTMLInputElement).value).toBe('Action parent')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    fireEvent.click(screen.getByRole('button', { name: 'Actions for Action parent' }))
    menu = await screen.findByRole('menu', { name: 'Actions for Action parent' })
    fireEvent.click(within(menu).getByText('Delete task'))
    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1))
    const deleteOperation = mutate.mock.calls[0][2][0]
    expect(deleteOperation.type).toBe('task.delete')
    expect(deleteOperation.entity_id).toBe(parent.id)
    expect(deleteOperation.expected_version).toBe(7)
  })

  it('offers hidden cross-level tasks and relationships through the keyboard editor', () => {
    const hiddenTask = {
      ...snapshot.tasks[0],
      id: '44444444-4444-4444-8444-444444444444',
      parent_id: snapshot.tasks[0].id,
      title: 'Hidden child task',
      position: 0,
    }
    const otherRoot = {
      ...snapshot.tasks[0],
      id: '55555555-5555-4555-8555-555555555555',
      title: 'Other root task',
      position: 1,
    }
    const value: ProjectSnapshot = {
      ...snapshot,
      tasks: [...snapshot.tasks, hiddenTask, otherRoot],
      edges: [{
        id: '66666666-6666-4666-8666-666666666666',
        project_id: snapshot.project.id,
        source_task_id: hiddenTask.id,
        target_task_id: otherRoot.id,
        edge_type: 'dependency',
        version: 1,
      }],
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <GraphView snapshot={value} />
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByText('Explicit relationships'))
    const sourceSelect = screen.getByLabelText('Source / prerequisite task') as HTMLSelectElement
    expect([...sourceSelect.options].map((option) => option.text)).toContain('Pipeline › Task › Hidden child task')
    fireEvent.change(screen.getByLabelText('Find source task'), { target: { value: 'Task › Hidden' } })
    expect([...sourceSelect.options].map((option) => option.text)).toEqual(['Choose source task', 'Pipeline › Task › Hidden child task'])
    expect(screen.getByText('Pipeline › Task › Hidden child task → Pipeline › Other root task')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Waiver settings' })).not.toBeNull()
  })

  it('persists viewport and node positions as layout-only records with distinct IDs', async () => {
    const mutateLayout = vi.spyOn(api, 'mutateLayout').mockResolvedValue({
      request_id: '77777777-7777-4777-8777-777777777777',
      project_id: snapshot.project.id,
      semantic_revision: snapshot.project.semantic_revision,
      layout_revision: snapshot.project.layout_revision + 1,
      results: [],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <GraphView snapshot={snapshot} />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByLabelText('Graph pipeline'), { target: { value: 'all' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save viewport' }))
    await waitFor(() => expect(mutateLayout).toHaveBeenCalledTimes(1))
    const viewportOperation = mutateLayout.mock.calls[0][2][0]
    expect(viewportOperation.type).toBe('viewport.upsert')
    expect(viewportOperation.data).toMatchObject({ parent_id: null, x: 42, y: -17, zoom: 1.25 })
    expect(viewportOperation.entity_id).not.toBe(snapshot.tasks[0].id)

    fireEvent.click(screen.getByRole('button', { name: 'Save node layout' }))
    await waitFor(() => expect(mutateLayout).toHaveBeenCalledTimes(2))
    const layoutOperation = mutateLayout.mock.calls[1][2][0]
    expect(layoutOperation.type).toBe('layout.upsert')
    expect(layoutOperation.entity_id).not.toBe(snapshot.tasks[0].id)
    mutateLayout.mockRestore()
  })

  it('keeps a hidden-descendant link indicator when the other endpoint is outside the drilled scope', () => {
    const parent = { ...snapshot.tasks[0], id: '70000000-0000-4000-8000-000000000001', title: 'Scoped parent', position: 0 }
    const child = { ...snapshot.tasks[0], id: '70000000-0000-4000-8000-000000000002', title: 'Visible child', parent_id: parent.id, position: 0 }
    const grandchild = { ...snapshot.tasks[0], id: '70000000-0000-4000-8000-000000000003', title: 'Hidden grandchild', parent_id: child.id, position: 0 }
    const outside = { ...snapshot.tasks[0], id: '70000000-0000-4000-8000-000000000004', title: 'Outside root', position: 1 }
    const value: ProjectSnapshot = {
      ...snapshot,
      tasks: [parent, child, grandchild, outside],
      edges: [{ id: '70000000-0000-4000-8000-000000000005', project_id: snapshot.project.id, source_task_id: grandchild.id, target_task_id: outside.id, edge_type: 'dependency', version: 1 }],
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><GraphView snapshot={value} /></QueryClientProvider>)

    fireEvent.click(screen.getByRole('button', { name: 'View 1 subtask for Scoped parent' }))
    expect(screen.getAllByText('Visible child').some((element) => element.tagName === 'STRONG')).toBe(true)
    expect(screen.getByText(/1 nested link/)).not.toBeNull()
  })
})

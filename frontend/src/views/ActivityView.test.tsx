/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '../lib/api'
import type { ProjectSnapshot } from '../types'
import { ActivityView } from './ActivityView'

const projectId = '11111111-1111-4111-8111-111111111111'
const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Undo invalidation',
    root_path: '/tmp/undo-invalidation',
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
  pipelines: [],
  tasks: [],
  edges: [],
  journals: [],
  artifacts: [],
  task_artifacts: [],
  layouts: [],
  progress: { leaf_total: 0, leaf_done: 0, ready: 0, waiting: 0, blocked: 0, review: 0 },
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('ActivityView undo invalidation', () => {
  it('invalidates the project-search prefix after undo succeeds', async () => {
    vi.spyOn(api, 'getHistory').mockResolvedValue([{
      id: 1,
      project_id: projectId,
      request_id: '22222222-2222-4222-8222-222222222222',
      actor_type: 'ui',
      actor_label: 'Research Monitor UI',
      event_type: 'task.update',
      summary: 'Updated a task',
      created_at: '2026-07-15T00:00:00Z',
      undoable: true,
      undo_request_head: true,
      undo_operation_count: 1,
    }])
    vi.spyOn(api, 'undoMutation').mockResolvedValue({
      request_id: '33333333-3333-4333-8333-333333333333',
      project_id: projectId,
      semantic_revision: 3,
      layout_revision: 0,
      results: [],
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(client, 'invalidateQueries')
    render(
      <QueryClientProvider client={client}>
        <ActivityView snapshot={snapshot} />
      </QueryClientProvider>,
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }))

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({
      queryKey: ['project-search', projectId],
    }))
  })
})

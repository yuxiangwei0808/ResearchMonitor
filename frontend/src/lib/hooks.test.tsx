/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './api'
import type { ProjectSnapshot } from '../types'
import { OUTBOX_CURSOR_KEY, useOutboxReplay, useProjectMutation } from './hooks'

const projectId = '11111111-1111-4111-8111-111111111111'
const snapshot: ProjectSnapshot = {
  project: {
    id: projectId,
    name: 'Search invalidation',
    root_path: '/tmp/search-invalidation',
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

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
}

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.restoreAllMocks()
})

describe('useOutboxReplay', () => {
  it('broadly invalidates before replacing a cursor from an old event stream', async () => {
    window.localStorage.setItem(OUTBOX_CURSOR_KEY, JSON.stringify({
      stream_id: 'old-stream',
      cursor: 41,
    }))
    vi.spyOn(api, 'getEvents').mockResolvedValue({
      events: [{
        id: 3,
        project_id: projectId,
        event_type: 'project.restore',
        created_at: '2026-07-15T00:00:00Z',
      }],
      stream_id: 'new-stream',
      latest_id: 3,
      reset_required: true,
      reset_reason: 'stream_changed',
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    let finishInvalidation!: () => void
    const invalidation = new Promise<void>((resolve) => {
      finishInvalidation = resolve
    })
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockReturnValue(invalidation)
    const view = renderHook(() => useOutboxReplay(), { wrapper: wrapper(client) })

    await waitFor(() => expect(api.getEvents).toHaveBeenCalledWith(41, 'old-stream'))
    expect(invalidate.mock.calls[0]).toEqual([])
    expect(window.localStorage.getItem(OUTBOX_CURSOR_KEY)).toBe(JSON.stringify({
      stream_id: 'old-stream',
      cursor: 41,
    }))

    finishInvalidation()
    await waitFor(() => expect(window.localStorage.getItem(OUTBOX_CURSOR_KEY)).toBe(JSON.stringify({
      stream_id: 'new-stream',
      cursor: 3,
    })))
    view.unmount()
  })

  it('keeps targeted invalidation working when localStorage get and set throw', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('Storage read denied', 'SecurityError')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('Storage write denied', 'QuotaExceededError')
    })
    vi.spyOn(api, 'getEvents').mockResolvedValue({
      events: [{
        id: 7,
        project_id: projectId,
        event_type: 'task.update',
        created_at: '2026-07-15T00:00:00Z',
      }],
      stream_id: 'current-stream',
      latest_id: 7,
      reset_required: false,
      reset_reason: null,
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(client, 'invalidateQueries').mockResolvedValue()
    const view = renderHook(() => useOutboxReplay(), { wrapper: wrapper(client) })

    await waitFor(() => expect(invalidate).toHaveBeenCalledTimes(5))
    expect(api.getEvents).toHaveBeenCalledWith(0, undefined)
    expect(invalidate.mock.calls.map(([filters]) => filters)).toEqual([
      { queryKey: ['projects'] },
      { queryKey: ['snapshot', projectId] },
      { queryKey: ['history', projectId] },
      { queryKey: ['proposals', projectId] },
      { queryKey: ['project-search', projectId] },
    ])
    view.unmount()
  })

  it('advances only through returned events when the stream has later bounded batches', async () => {
    window.localStorage.setItem(OUTBOX_CURSOR_KEY, JSON.stringify({
      stream_id: 'current-stream',
      cursor: 10,
    }))
    vi.spyOn(api, 'getEvents').mockResolvedValue({
      events: [{
        id: 11,
        project_id: projectId,
        event_type: 'task.update',
        created_at: '2026-07-15T00:00:00Z',
      }],
      stream_id: 'current-stream',
      latest_id: 50,
      reset_required: false,
      reset_reason: null,
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    vi.spyOn(client, 'invalidateQueries').mockResolvedValue()
    const view = renderHook(() => useOutboxReplay(), { wrapper: wrapper(client) })

    await waitFor(() => expect(window.localStorage.getItem(OUTBOX_CURSOR_KEY)).toBe(JSON.stringify({
      stream_id: 'current-stream',
      cursor: 11,
    })))
    expect(api.getEvents).toHaveBeenCalledWith(10, 'current-stream')
    view.unmount()
  })

  it('refetches an active project-search prefix after an immediate semantic mutation', async () => {
    const search = vi.fn().mockResolvedValue({ results: [], count: 0, total: 0, offset: 0, limit: 200, truncated: false })
    vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: '22222222-2222-4222-8222-222222222222',
      project_id: projectId,
      semantic_revision: 3,
      layout_revision: 0,
      results: [],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = renderHook(() => {
      useQuery({
        queryKey: ['project-search', projectId, 'needle'],
        queryFn: search,
      })
      return useProjectMutation(snapshot)
    }, { wrapper: wrapper(client) })

    await waitFor(() => expect(search).toHaveBeenCalledTimes(1))
    act(() => view.result.current.mutate({
      id: '33333333-3333-4333-8333-333333333333',
      type: 'task.update',
      entity_id: '44444444-4444-4444-8444-444444444444',
      expected_version: 1,
      data: { title: 'Updated title' },
    }))
    await waitFor(() => expect(search).toHaveBeenCalledTimes(2))
    view.unmount()
  })
})

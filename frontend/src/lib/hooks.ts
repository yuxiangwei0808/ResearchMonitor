import { useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { MutationOperation, ProjectSnapshot } from '../types'

export const OUTBOX_CURSOR_KEY = 'research-monitor-outbox-cursor-v2'

type OutboxCursor = {
  stream_id?: string
  cursor: number
}

function readOutboxCursor(): OutboxCursor {
  try {
    const serialized = window.localStorage.getItem(OUTBOX_CURSOR_KEY)
    if (!serialized) return { cursor: 0 }
    const parsed = JSON.parse(serialized) as Partial<OutboxCursor>
    if (
      typeof parsed.stream_id === 'string'
      && parsed.stream_id.length > 0
      && Number.isSafeInteger(parsed.cursor)
      && Number(parsed.cursor) >= 0
    ) {
      return { stream_id: parsed.stream_id, cursor: Number(parsed.cursor) }
    }
  } catch {
    // Storage can be unavailable under privacy settings; replay remains in memory.
  }
  return { cursor: 0 }
}

function writeOutboxCursor(value: Required<OutboxCursor>) {
  try {
    window.localStorage.setItem(OUTBOX_CURSOR_KEY, JSON.stringify(value))
  } catch {
    // A persistence failure must not prevent cache invalidation or in-memory replay.
  }
}

export function useOutboxReplay() {
  const queryClient = useQueryClient()
  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    const stored = readOutboxCursor()
    let cursor = stored.cursor
    let streamId = stored.stream_id
    const poll = async () => {
      try {
        const replay = await api.getEvents(cursor, streamId)
        if (stopped) return
        if (replay.reset_required) {
          await queryClient.invalidateQueries()
        } else if (replay.events.length) {
          const projectIds = new Set(replay.events.map((event) => event.project_id).filter(Boolean))
          replay.events
            .filter((event) => event.event_type.toLowerCase().includes('proposal'))
            .forEach((event) => window.dispatchEvent(new CustomEvent('research-monitor:proposal-update', { detail: event })))
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['projects'] }),
            ...[...projectIds].flatMap((projectId) => [
              queryClient.invalidateQueries({ queryKey: ['snapshot', projectId] }),
              queryClient.invalidateQueries({ queryKey: ['history', projectId] }),
              queryClient.invalidateQueries({ queryKey: ['proposals', projectId] }),
              queryClient.invalidateQueries({ queryKey: ['project-search', projectId] }),
            ]),
          ])
        }
        if (stopped) return
        const baseCursor = replay.reset_required ? 0 : cursor
        cursor = replay.events.reduce(
          (latestReturnedId, event) => Math.max(latestReturnedId, event.id),
          baseCursor,
        )
        streamId = replay.stream_id
        writeOutboxCursor({ stream_id: streamId, cursor })
      } catch {
        // The next bounded poll retries after server restarts or brief lock contention.
      } finally {
        if (!stopped) timer = window.setTimeout(poll, 2_000)
      }
    }
    void poll()
    return () => {
      stopped = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [queryClient])
}

export function useProjectMutation(snapshot: ProjectSnapshot) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (operations: MutationOperation | MutationOperation[]) => api.mutate(
      snapshot.project.id,
      snapshot.project.semantic_revision,
      Array.isArray(operations) ? operations : [operations],
    ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['snapshot', snapshot.project.id] }),
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
        queryClient.invalidateQueries({ queryKey: ['history', snapshot.project.id] }),
        queryClient.invalidateQueries({ queryKey: ['project-search', snapshot.project.id] }),
      ])
    },
  })
}

export function useLayoutMutation(snapshot: ProjectSnapshot) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (operations: MutationOperation | MutationOperation[]) => api.mutateLayout(
      snapshot.project.id,
      snapshot.project.layout_revision,
      Array.isArray(operations) ? operations : [operations],
    ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['snapshot', snapshot.project.id] }),
  })
}

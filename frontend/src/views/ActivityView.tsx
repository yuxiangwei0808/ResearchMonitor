import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronRight, Clock3, Filter, History, RotateCcw, Search, UserRound } from 'lucide-react'
import type { AuditEvent, ProjectSnapshot } from '../types'
import { api } from '../lib/api'
import { formatDate, humanize } from '../lib/format'
import { Badge, Button, EmptyState, ErrorState, Notice, Spinner } from '../components/ui'

export function ActivityView({ snapshot }: { snapshot: ProjectSnapshot }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['history', snapshot.project.id], queryFn: () => api.getHistory(snapshot.project.id) })
  const [search, setSearch] = useState('')
  const [actor, setActor] = useState('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const events = useMemo(() => (query.data ?? []).filter((event) => {
    const text = `${event.summary} ${event.event_type} ${event.actor_label ?? ''}`.toLowerCase()
    return text.includes(search.toLowerCase()) && (actor === 'all' || event.actor_type === actor)
  }), [query.data, search, actor])
  const undo = useMutation({
    mutationFn: (targetRequestId: string) => api.undoMutation(
      snapshot.project.id,
      targetRequestId,
      snapshot.project.semantic_revision,
    ),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['snapshot', snapshot.project.id] }),
        queryClient.invalidateQueries({ queryKey: ['history', snapshot.project.id] }),
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
        queryClient.invalidateQueries({ queryKey: ['project-search', snapshot.project.id] }),
      ])
    },
  })
  const requestUndo = (event: AuditEvent) => {
    if (!event.request_id || !event.undoable) return
    const count = event.undo_operation_count ?? 1
    const label = count === 1 ? 'this recorded change' : `all ${count} changes in this request`
    if (window.confirm(`Undo ${label}? The inverse will be recorded as a new audit event.`)) {
      undo.mutate(event.request_id)
    }
  }
  if (query.isLoading) return <div className="content-loading"><Spinner label="Reading project history…" /></div>
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  return (
    <div className="view-page activity-view">
      <header className="view-toolbar"><div><h2>Activity</h2><p>An append-only record of changes, decisions, progress, and proposal acceptance.</p></div></header>
      <div className="filter-bar"><label className="search-field"><Search size={16} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search activity…" /></label><label className="inline-select"><Filter size={15} /><select value={actor} onChange={(e) => setActor(e.target.value)}><option value="all">All actors</option><option value="ui">Manual edits</option><option value="agent">Codex proposals</option><option value="system">System</option><option value="import">Import</option></select></label></div>
      {undo.error && <Notice tone="danger">{undo.error.message}</Notice>}
      {!query.data?.length ? <EmptyState icon={<History size={28} />} title="No recorded activity yet" description="Task edits, status transitions, journal entries, artifact links, and proposal decisions will appear here." /> : !events.length ? <div className="table-empty">No activity matches these filters.</div> : <div className="timeline">{groupEvents(events).map(([day, dayEvents]) => <section className="timeline-day" key={day}><h3>{day}</h3>{dayEvents.map((event) => {
        const id = String(event.id)
        const hasDetails = event.before != null || event.after != null
        const showUndo = event.actor_type === 'ui' && Boolean(event.request_id) && event.undo_request_head
        return <article className="timeline-event" key={id}><div className={`timeline-icon actor-${event.actor_type}`}>{event.actor_type === 'agent' ? <Bot size={16} /> : event.actor_type === 'ui' ? <UserRound size={16} /> : <Clock3 size={16} />}</div><div className="timeline-card"><header><div><strong>{event.summary}</strong><span><Badge tone="neutral">{humanize(event.event_type)}</Badge>{event.actor_label || humanize(event.actor_type)}</span></div><div className="timeline-card-actions"><time>{new Date(event.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>{showUndo && (event.undoable ? <Button variant="ghost" size="sm" disabled={undo.isPending} onClick={() => requestUndo(event)}><RotateCcw size={13} />{undo.isPending && undo.variables === event.request_id ? 'Undoing…' : 'Undo'}</Button> : <Badge tone="neutral" title={event.undo_reason ?? 'This request cannot be safely undone'}>Undo unavailable</Badge>)}</div></header>{hasDetails && <><button className="details-toggle" onClick={() => setExpanded((previous) => toggle(previous, id))}>{expanded.has(id) ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {expanded.has(id) ? 'Hide' : 'Show'} recorded diff</button>{expanded.has(id) && <div className="audit-diff">{event.before != null && <div><span>Before</span><pre>{JSON.stringify(event.before, null, 2)}</pre></div>}{event.after != null && <div><span>After</span><pre>{JSON.stringify(event.after, null, 2)}</pre></div>}</div>}</>}</div></article>
      })}</section>)}</div>}
    </div>
  )
}

function groupEvents(events: AuditEvent[]): Array<[string, AuditEvent[]]> {
  const result = new Map<string, AuditEvent[]>()
  events.slice().sort((a, b) => b.created_at.localeCompare(a.created_at)).forEach((event) => {
    const day = formatDate(event.created_at)
    result.set(day, [...(result.get(day) ?? []), event])
  })
  return [...result.entries()]
}

function toggle(previous: Set<string>, value: string) {
  const next = new Set(previous)
  next.has(value) ? next.delete(value) : next.add(value)
  return next
}

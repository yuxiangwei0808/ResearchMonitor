import { useEffect, useMemo, useState } from 'react'
import { flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from '@tanstack/react-table'
import { useLocation } from 'react-router-dom'
import { CheckCircle2, Edit3, ExternalLink, File, FileCode2, FileImage, FileText, Folder, Link2, Plus, RefreshCw, Search, ShieldCheck, Trash2, TriangleAlert, Unlink } from 'lucide-react'
import type { Artifact, ArtifactRole, ProjectSnapshot } from '../types'
import { ARTIFACT_ROLES } from '../types'
import { api, operation } from '../lib/api'
import { useProjectMutation } from '../lib/hooks'
import { bytes, formatDate, humanize, shortPath } from '../lib/format'
import { createTaskLabeler } from '../lib/taskLabels'
import { Badge, Button, Dialog, EmptyState, Field, Notice } from '../components/ui'

const ARTIFACT_PAGE_SIZE = 100

export function ArtifactsView({ snapshot }: { snapshot: ProjectSnapshot }) {
  const location = useLocation()
  const [search, setSearch] = useState('')
  const [kind, setKind] = useState('all')
  const [editor, setEditor] = useState<{ open: boolean; artifact?: Artifact }>({ open: false })
  const [preview, setPreview] = useState<Artifact | null>(null)
  const [metadata, setMetadata] = useState<Record<string, Artifact>>({})
  const [checking, setChecking] = useState<Set<string>>(new Set())
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const [sorting, setSorting] = useState<SortingState>([])
  const [page, setPage] = useState(0)
  const taskMap = useMemo(() => new Map(snapshot.tasks.map((task) => [task.id, task])), [snapshot.tasks])
  const linksByArtifact = useMemo(() => {
    const map = new Map<string, typeof snapshot.task_artifacts>()
    snapshot.task_artifacts.forEach((link) => map.set(link.artifact_id, [...(map.get(link.artifact_id) ?? []), link]))
    return map
  }, [snapshot.task_artifacts])
  const filtered = useMemo(() => snapshot.artifacts.filter((artifact) => {
    if (artifact.deleted_at) return false
    const matchesSearch = `${artifact.label} ${artifact.locator} ${artifact.provider ?? ''}`.toLowerCase().includes(search.toLowerCase())
    const matchesKind = kind === 'all' || artifact.kind === kind || (linksByArtifact.get(artifact.id) ?? []).some((link) => link.role === kind)
    return matchesSearch && matchesKind
  }), [snapshot.artifacts, search, kind, linksByArtifact])
  const pageCount = Math.max(1, Math.ceil(filtered.length / ARTIFACT_PAGE_SIZE))
  useEffect(() => setPage((current) => Math.min(current, pageCount - 1)), [pageCount])
  const refreshMetadata = async (artifact: Artifact, openPreview = false) => {
    setMetadataError(null)
    setChecking((current) => new Set(current).add(artifact.id))
    try {
      const value = await api.getArtifactMetadata(artifact.id)
      setMetadata((current) => ({ ...current, [artifact.id]: value }))
      if (openPreview) setPreview(value)
    } catch (error) {
      setMetadataError(error instanceof Error ? error.message : 'Could not refresh artifact metadata')
    } finally {
      setChecking((current) => { const next = new Set(current); next.delete(artifact.id); return next })
    }
  }
  const columns: ColumnDef<Artifact>[] = [
    {
      accessorKey: 'label',
      header: 'Artifact',
      cell: ({ row }) => {
        const artifact = row.original
        const current = metadata[artifact.id] ?? artifact
        return <div className="artifact-name-cell"><span className="file-icon">{artifact.kind === 'url' ? <ExternalLink size={18} /> : iconForArtifact(current)}</span><div><strong>{artifact.label}</strong><small title={artifact.locator}>{shortPath(artifact.locator, 62)}</small>{artifact.provider && <Badge tone="purple">{artifact.provider}</Badge>}</div></div>
      },
    },
    {
      id: 'associations',
      header: 'Role & task',
      enableSorting: false,
      cell: ({ row }) => {
        const links = linksByArtifact.get(row.original.id) ?? []
        return <div className="artifact-links">{links.slice(0, 3).map((link) => <span key={link.id}><Badge tone="neutral">{humanize(link.role)}</Badge>{taskMap.get(link.task_id)?.title ?? 'Unknown task'}</span>)}{links.length > 3 && <span className="muted-copy">+{links.length - 3} more associations</span>}{!links.length && <span className="muted-copy">Not linked to a task</span>}</div>
      },
    },
    {
      id: 'availability',
      header: 'Availability',
      enableSorting: false,
      cell: ({ row }) => {
        const artifact = row.original
        const current = metadata[artifact.id] ?? artifact
        return <>{artifact.kind === 'url' ? <Badge tone="blue">External</Badge> : current.available === false ? <Badge tone="red"><TriangleAlert size={12} />Missing</Badge> : current.available ? <Badge tone="green"><CheckCircle2 size={12} />Available</Badge> : <Badge tone="neutral">Unchecked</Badge>}{current.size_bytes != null && <small className="table-subcopy">{bytes(current.size_bytes)}</small>}{artifact.kind === 'local' && <Button size="icon" variant="ghost" disabled={checking.has(artifact.id)} onClick={() => refreshMetadata(artifact)} aria-label={`Refresh ${artifact.label} metadata`}><RefreshCw size={14} /></Button>}</>
      },
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ row }) => formatDate(row.original.updated_at),
    },
    {
      id: 'actions',
      header: '',
      enableSorting: false,
      cell: ({ row }) => {
        const artifact = row.original
        return <div className="table-actions">{artifact.kind === 'url' ? <a className="icon-link" href={artifact.locator} target="_blank" rel="noopener noreferrer" aria-label={`Open ${artifact.label}`}><ExternalLink size={16} /></a> : <Button size="sm" variant="secondary" disabled={checking.has(artifact.id)} onClick={() => refreshMetadata(artifact, true)}>Preview</Button>}<Button size="icon" variant="ghost" onClick={() => setEditor({ open: true, artifact })} aria-label={`Edit ${artifact.label}`}><Edit3 size={15} /></Button></div>
      },
    },
  ]
  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: (updater) => {
      setSorting(updater)
      setPage(0)
    },
    getRowId: (artifact) => artifact.id,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  })
  const sortedRows = table.getRowModel().rows
  const pageRows = sortedRows.slice(page * ARTIFACT_PAGE_SIZE, (page + 1) * ARTIFACT_PAGE_SIZE)
  useEffect(() => {
    if (!location.hash) return
    const elementId = decodeURIComponent(location.hash.slice(1))
    const artifactId = elementId.startsWith('artifact-') ? elementId.slice('artifact-'.length) : null
    const index = artifactId ? sortedRows.findIndex((row) => row.original.id === artifactId) : -1
    const targetPage = index >= 0 ? Math.floor(index / ARTIFACT_PAGE_SIZE) : page
    if (targetPage !== page) { setPage(targetPage); return }
    const frame = requestAnimationFrame(() => {
      document.getElementById(elementId)?.scrollIntoView({ block: 'center' })
    })
    return () => cancelAnimationFrame(frame)
  }, [location.hash, page, sortedRows])
  return (
    <div className="view-page artifacts-view">
      <header className="view-toolbar"><div><h2>Artifacts</h2><p>Connect work to code, results, logs, documents, and external runs without copying project files.</p></div><Button onClick={() => setEditor({ open: true })}><Plus size={16} />Link artifact</Button></header>
      <div className="filter-bar"><label className="search-field"><Search size={16} /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0) }} placeholder="Search artifacts…" /></label><label className="artifact-type-filter"><span className="sr-only">Artifact type</span><select aria-label="Artifact type" value={kind} onChange={(e) => { setKind(e.target.value); setPage(0) }}><option value="all">All artifact types</option><option value="local">Local paths</option><option value="url">External URLs</option>{ARTIFACT_ROLES.map((role) => <option value={role} key={role}>{humanize(role)}</option>)}</select></label></div>
      {metadataError && <Notice tone="danger">{metadataError}</Notice>}
      {!snapshot.artifacts.some((artifact) => !artifact.deleted_at) ? <EmptyState icon={<Link2 size={28} />} title="Link evidence to the work" description="Add project-relative files or safe external URLs, then associate them with the tasks they support." action={<Button onClick={() => setEditor({ open: true })}><Plus size={16} />Link first artifact</Button>} /> : (
        <div className="artifact-table-wrap"><table className="data-table artifact-table"><thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{header.isPlaceholder ? null : header.column.getCanSort() ? <button className="table-sort" type="button" onClick={header.column.getToggleSortingHandler()}>{flexRender(header.column.columnDef.header, header.getContext())}<span>{header.column.getIsSorted() === 'asc' ? '↑' : header.column.getIsSorted() === 'desc' ? '↓' : ''}</span></button> : flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead><tbody>{pageRows.map((row) => <tr id={`artifact-${row.original.id}`} key={row.id}>{row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table>{!filtered.length && <div className="table-empty">No artifacts match these filters.</div>}{filtered.length > ARTIFACT_PAGE_SIZE && <nav className="artifact-pagination" aria-label="Artifact pages"><Button type="button" size="sm" variant="secondary" disabled={page === 0} onClick={() => setPage((current) => Math.max(0, current - 1))}>Previous</Button><span role="status" aria-live="polite">Page {page + 1} of {pageCount} · {filtered.length} artifacts</span><Button type="button" size="sm" variant="secondary" disabled={page + 1 >= pageCount} onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))}>Next</Button></nav>}</div>
      )}
      <ArtifactEditor snapshot={snapshot} state={editor} onClose={() => setEditor({ open: false })} />
      <ArtifactPreview artifact={preview} onClose={() => setPreview(null)} />
    </div>
  )
}

function ArtifactEditor({ snapshot, state, onClose }: { snapshot: ProjectSnapshot; state: { open: boolean; artifact?: Artifact }; onClose: () => void }) {
  const [kind, setKind] = useState<'local' | 'url'>('local')
  const [label, setLabel] = useState('')
  const [locator, setLocator] = useState('')
  const [rootId, setRootId] = useState('')
  const [provider, setProvider] = useState('')
  const [taskId, setTaskId] = useState('')
  const [role, setRole] = useState<ArtifactRole>('evidence')
  const [notes, setNotes] = useState('')
  const mutation = useProjectMutation(snapshot)
  const labelTask = useMemo(() => createTaskLabeler(snapshot.pipelines, snapshot.tasks), [snapshot.pipelines, snapshot.tasks])
  const taskOptions = useMemo(() => snapshot.tasks.filter((task) => !task.deleted_at).sort((left, right) => labelTask(left).localeCompare(labelTask(right))), [labelTask, snapshot.tasks])
  const roots = snapshot.artifact_roots
  const existingLinks = state.artifact ? snapshot.task_artifacts.filter((link) => link.artifact_id === state.artifact!.id) : []
  useEffect(() => {
    const artifact = state.artifact
    setKind(artifact?.kind ?? 'local')
    setLabel(artifact?.label ?? '')
    setLocator(artifact?.locator ?? '')
    setRootId(artifact?.artifact_root_id ?? '')
    setProvider(artifact?.provider ?? '')
    setTaskId('')
    setRole('evidence')
    setNotes(artifact?.notes ?? '')
  }, [state.open, state.artifact])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    const artifactId = state.artifact?.id ?? crypto.randomUUID()
    const artifactData = { kind, label, locator, artifact_root_id: kind === 'local' ? (rootId || roots[0]?.id) : null, provider: provider || (kind === 'local' ? 'local' : 'external'), notes }
    const ops = state.artifact
      ? [operation('artifact.update', artifactData, { id: artifactId, version: state.artifact.version })]
      : [operation('artifact.create', artifactData, { id: artifactId })]
    if (taskId) ops.push(operation('task_artifact.link', { task_id: taskId, artifact_id: artifactId, role, notes: null }, { id: crypto.randomUUID() }))
    await mutation.mutateAsync(ops)
    setLabel(''); setLocator(''); setProvider(''); setTaskId(''); setNotes(''); onClose()
  }
  const unlink = async (linkId: string) => mutation.mutateAsync(operation('task_artifact.unlink', {}, { id: linkId }))
  const remove = async () => {
    if (!state.artifact || !window.confirm(`Delete the monitor link for “${state.artifact.label}”? The underlying artifact will not be changed.`)) return
    await mutation.mutateAsync([
      ...existingLinks.map((link) => operation('task_artifact.unlink', {}, { id: link.id })),
      operation('artifact.delete', {}, { id: state.artifact.id, version: state.artifact.version }),
    ])
    onClose()
  }
  return <Dialog open={state.open} onClose={onClose} title={state.artifact ? 'Edit artifact' : 'Link an artifact'} description="Research Monitor stores only the locator and never modifies the underlying file."><form className="form-stack" onSubmit={submit}><div className="segmented wide"><button type="button" className={kind === 'local' ? 'active' : ''} onClick={() => setKind('local')}><Folder size={15} />Local path</button><button type="button" className={kind === 'url' ? 'active' : ''} onClick={() => setKind('url')}><ExternalLink size={15} />External URL</button></div><Field label="Label"><input required value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Baseline results" /></Field>{kind === 'local' && <Field label="Approved root"><select value={rootId} onChange={(e) => setRootId(e.target.value)}>{roots.map((root) => <option value={root.id} key={root.id}>{root.name} · {shortPath(root.canonical_path)}</option>)}</select></Field>}<Field label={kind === 'local' ? 'Relative path' : 'HTTP or HTTPS URL'} hint={kind === 'local' ? 'Absolute paths and escaping symlinks are rejected.' : 'The app records this link but does not fetch it.'}><input required value={locator} onChange={(e) => setLocator(e.target.value)} placeholder={kind === 'local' ? 'results/baseline/metrics.json' : 'https://wandb.ai/…'} spellCheck={false} /></Field>{kind === 'url' && <Field label="Provider"><input value={provider} onChange={(e) => setProvider(e.target.value)} placeholder="W&B, MLflow, GitHub, paper…" /></Field>}<Field label="Notes"><textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field><div className="form-grid two"><Field label="Add task association"><select value={taskId} onChange={(e) => setTaskId(e.target.value)}><option value="">No new association</option>{taskOptions.map((task) => <option value={task.id} key={task.id}>{labelTask(task)}</option>)}</select></Field><Field label="Association role"><select value={role} onChange={(e) => setRole(e.target.value as ArtifactRole)}>{ARTIFACT_ROLES.map((item) => <option value={item} key={item}>{humanize(item)}</option>)}</select></Field></div>{state.artifact && existingLinks.length > 0 && <div className="association-editor"><strong>Existing task associations</strong>{existingLinks.map((link) => <div key={link.id}><span><Badge tone="neutral">{humanize(link.role)}</Badge>{labelTask(snapshot.tasks.find((task) => task.id === link.task_id))}</span><Button type="button" size="icon" variant="ghost" onClick={() => unlink(link.id)} aria-label="Remove association"><Unlink size={14} /></Button></div>)}</div>}{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<Notice><ShieldCheck size={16} />Preview access repeats path containment checks and blocks secrets, executables, and unsafe formats.</Notice><div className="dialog-actions split">{state.artifact ? <Button type="button" variant="danger" onClick={remove}><Trash2 size={15} />Delete link</Button> : <span />}<div className="button-row"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? 'Saving…' : state.artifact ? 'Save artifact' : 'Link artifact'}</Button></div></div></form></Dialog>
}

function ArtifactPreview({ artifact, onClose }: { artifact: Artifact | null; onClose: () => void }) {
  if (!artifact) return null
  const url = `/api/v1/artifacts/${artifact.id}/preview`
  const mime = artifact.mime_type ?? ''
  return <Dialog open={Boolean(artifact)} onClose={onClose} title={artifact.label} description={artifact.locator} wide>{artifact.previewable === false ? <Notice tone="warning"><TriangleAlert size={16} />{artifact.preview_reason || 'This artifact cannot be previewed safely.'}</Notice> : <div className="preview-frame">{artifact.preview_mode === 'image' || mime.startsWith('image/') ? <img src={url} alt={artifact.label} /> : <iframe src={url} title={`Preview of ${artifact.label}`} sandbox="" />}</div>}<div className="dialog-actions"><span className="muted-copy">Read-only safe preview</span><Button variant="secondary" onClick={onClose}>Close</Button></div></Dialog>
}

function iconForArtifact(artifact: Artifact) {
  const value = `${artifact.mime_type ?? ''} ${artifact.locator}`.toLowerCase()
  if (/image|\.png$|\.jpe?g$|\.webp$/.test(value)) return <FileImage size={18} />
  if (/json|csv|log|txt|markdown|md$|pdf/.test(value)) return <FileText size={18} />
  if (/py$|ts$|js$|code/.test(value)) return <FileCode2 size={18} />
  return <File size={18} />
}

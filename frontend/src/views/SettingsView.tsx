import { useEffect, useRef, useState } from 'react'
import { Archive, CheckCircle2, DatabaseBackup, FolderCog, FolderPlus, KeyRound, LockKeyhole, RotateCcw, Save, Shield, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { ProjectSnapshot, ScanPolicy } from '../types'
import { operation } from '../lib/api'
import { useProjectMutation } from '../lib/hooks'
import { shortPath } from '../lib/format'
import { Badge, Button, Dialog, Field, Notice } from '../components/ui'

export function SettingsView({ snapshot }: { snapshot: ProjectSnapshot }) {
  return (
    <div className="view-page settings-view">
      <header className="view-toolbar"><div><h2>Project settings</h2><p>Configure monitor metadata, approved roots, and the exact boundaries Codex must respect.</p></div></header>
      <ProjectDetails snapshot={snapshot} />
      <ScanPolicyEditor snapshot={snapshot} />
      <ArtifactRoots snapshot={snapshot} />
      <SecuritySummary />
      <Lifecycle snapshot={snapshot} />
    </div>
  )
}

function ProjectDetails({ snapshot }: { snapshot: ProjectSnapshot }) {
  const project = snapshot.project
  const incoming = { name: project.name, description: project.description ?? '', research_goal: project.research_goal ?? '', success_criteria: project.success_criteria ?? '', color: project.color }
  const incomingSignature = JSON.stringify(incoming)
  const [form, setForm] = useState(incoming)
  const [dirty, setDirty] = useState(false)
  const [externalUpdate, setExternalUpdate] = useState(false)
  const source = useRef({ projectId: project.id, signature: incomingSignature })
  const mutation = useProjectMutation(snapshot)
  useEffect(() => {
    if (source.current.projectId !== project.id) {
      source.current = { projectId: project.id, signature: incomingSignature }
      setForm(incoming); setDirty(false); setExternalUpdate(false)
    } else if (source.current.signature !== incomingSignature) {
      if (dirty) setExternalUpdate(true)
      else { source.current.signature = incomingSignature; setForm(incoming); setExternalUpdate(false) }
    }
  }, [dirty, incomingSignature, project.id])
  const change = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => { setForm((current) => ({ ...current, [key]: value })); setDirty(true) }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try { await mutation.mutateAsync(operation('project.update', { ...form }, { id: project.id })); setDirty(false); setExternalUpdate(false) } catch { /* rendered by the mutation notice */ }
  }
  return <section className="settings-section"><header><span className="settings-icon"><FolderCog size={18} /></span><div><h3>Project details</h3><p>Describe the research objective separately from its task status.</p></div></header><form className="settings-form" onSubmit={save}><div className="form-grid two"><Field label="Project name"><input required value={form.name} onChange={(e) => change('name', e.target.value)} /></Field><Field label="Color"><div className="color-input"><input type="color" value={form.color} onChange={(e) => change('color', e.target.value)} /><span>{form.color}</span></div></Field></div><Field label="Description"><textarea rows={2} value={form.description} onChange={(e) => change('description', e.target.value)} /></Field><Field label="Research goal"><textarea rows={3} value={form.research_goal} onChange={(e) => change('research_goal', e.target.value)} /></Field><Field label="Success criteria"><textarea rows={3} value={form.success_criteria} onChange={(e) => change('success_criteria', e.target.value)} placeholder="What evidence would make this project scientifically successful?" /></Field>{externalUpdate && <Notice tone="warning">Project details changed in another UI or CLI action while you were editing. Your draft is preserved; saving may report a version conflict.</Notice>}{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<div className="settings-actions"><Button type="submit" disabled={mutation.isPending}><Save size={16} />{mutation.isPending ? 'Saving…' : 'Save details'}</Button></div></form></section>
}

function ScanPolicyEditor({ snapshot }: { snapshot: ProjectSnapshot }) {
  const [policy, setPolicy] = useState(snapshot.scan_policy)
  const [dirty, setDirty] = useState(false)
  const [externalUpdate, setExternalUpdate] = useState(false)
  const incomingSignature = JSON.stringify(snapshot.scan_policy)
  const source = useRef({ projectId: snapshot.project.id, signature: incomingSignature })
  const mutation = useProjectMutation(snapshot)
  useEffect(() => {
    if (source.current.projectId !== snapshot.project.id) {
      source.current = { projectId: snapshot.project.id, signature: incomingSignature }
      setPolicy(snapshot.scan_policy); setDirty(false); setExternalUpdate(false)
    } else if (source.current.signature !== incomingSignature) {
      if (dirty) setExternalUpdate(true)
      else { source.current.signature = incomingSignature; setPolicy(snapshot.scan_policy); setExternalUpdate(false) }
    }
  }, [dirty, incomingSignature, snapshot.project.id])
  const changePolicy = (next: ScanPolicy) => { setPolicy(next); setDirty(true) }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try { await mutation.mutateAsync(operation('scan_policy.update', policy as unknown as Record<string, unknown>, { id: snapshot.project.id, version: policy.version })); setDirty(false); setExternalUpdate(false) } catch { /* rendered by the mutation notice */ }
  }
  const lines = (value: string) => value.split('\n').map((line) => line.trim()).filter(Boolean)
  return <section className="settings-section"><header><span className="settings-icon"><Shield size={18} /></span><div><h3>Codex scan policy</h3><p>Codex receives these boundaries through the companion skill. It cannot modify them.</p></div><Badge tone="green"><LockKeyhole size={12} />Human controlled</Badge></header><form className="settings-form" onSubmit={save}><Field label="Preferred source-of-truth files" hint="One project-relative path per line, in priority order."><textarea rows={4} value={policy.preferred_sources.join('\n')} onChange={(e) => changePolicy({ ...policy, preferred_sources: lines(e.target.value) })} placeholder={'EXPERIMENT_PLAN.md\nrefine-logs/EXPERIMENT_TRACKER.md'} spellCheck={false} /></Field><div className="form-grid two"><Field label="Include globs"><textarea rows={4} value={policy.include_globs.join('\n')} onChange={(e) => changePolicy({ ...policy, include_globs: lines(e.target.value) })} spellCheck={false} /></Field><Field label="Exclude globs"><textarea rows={4} value={policy.exclude_globs.join('\n')} onChange={(e) => changePolicy({ ...policy, exclude_globs: lines(e.target.value) })} spellCheck={false} /></Field></div><Field label="Sensitive path patterns"><textarea rows={3} value={policy.sensitive_patterns.join('\n')} onChange={(e) => changePolicy({ ...policy, sensitive_patterns: lines(e.target.value) })} spellCheck={false} /></Field><div className="form-grid two"><Field label="Max readable text file size (bytes)"><input type="number" min={1024} max={10_485_760} value={policy.max_text_file_size} onChange={(e) => changePolicy({ ...policy, max_text_file_size: Number(e.target.value) })} /></Field><Field label="Git history limit"><input type="number" min={0} max={1000} value={policy.git_history_limit} onChange={(e) => changePolicy({ ...policy, git_history_limit: Number(e.target.value) })} /></Field></div><div className="checkbox-stack"><label><input type="checkbox" checked={policy.allow_git_metadata} onChange={(e) => changePolicy({ ...policy, allow_git_metadata: e.target.checked })} /><span><strong>Allow bounded Git metadata</strong><small>Status, log, diff-stat, and tracked-file metadata only.</small></span></label><label className="disabled"><input type="checkbox" checked={false} disabled /><span><strong>Follow symlinks</strong><small>Always disabled in v1.</small></span></label><label className="disabled"><input type="checkbox" checked={false} disabled /><span><strong>Read source files outside the project</strong><small>Disabled by the v1 safety boundary.</small></span></label></div>{externalUpdate && <Notice tone="warning">The scan policy changed in another UI or CLI action while you were editing. Your draft is preserved; review it before retrying a conflicted save.</Notice>}{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<div className="settings-actions"><Button type="submit" disabled={mutation.isPending}><Save size={16} />{mutation.isPending ? 'Saving…' : 'Save scan policy'}</Button></div></form></section>
}

function ArtifactRoots({ snapshot }: { snapshot: ProjectSnapshot }) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const mutation = useProjectMutation(snapshot)
  const add = async (event: React.FormEvent) => { event.preventDefault(); await mutation.mutateAsync(operation('artifact_root.create', { name, canonical_path: path }, { id: crypto.randomUUID() })); setName(''); setPath(''); setOpen(false) }
  return <section className="settings-section">
    <header><span className="settings-icon"><FolderPlus size={18} /></span><div><h3>Approved artifact roots</h3><p>Local artifacts must remain inside one of these human-approved folders.</p></div><Button variant="secondary" size="sm" onClick={() => setOpen(true)}><FolderPlus size={15} />Add root</Button></header>
    <div className="root-list">{snapshot.artifact_roots.map((root) => <div key={root.id}><span className="root-icon"><FolderCog size={16} /></span><div><strong>{root.name}</strong><small title={root.canonical_path}>{shortPath(root.canonical_path, 74)}</small></div>{root.is_project_root ? <Badge tone="green">Project root</Badge> : <Button variant="ghost" size="sm" onClick={() => window.confirm(`Remove approved root “${root.name}”? Linked artifacts must be removed first.`) && mutation.mutate(operation('artifact_root.delete', {}, { id: root.id, version: root.version }))}>Remove</Button>}</div>)}</div>
    <Dialog open={open} onClose={() => setOpen(false)} title="Approve an artifact root" description="This expands which local paths can be linked and previewed for this project."><form className="form-stack" onSubmit={add}><Field label="Display name"><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Shared results" /></Field><Field label="Absolute folder path"><input required value={path} onChange={(e) => setPath(e.target.value)} placeholder="/home/me/shared-results" spellCheck={false} /></Field><Notice tone="warning">Approve only folders whose contents you are comfortable exposing through local artifact previews.</Notice>{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<div className="dialog-actions"><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit">Approve root</Button></div></form></Dialog>
  </section>
}

function SecuritySummary() {
  return <section className="settings-section"><header><span className="settings-icon"><KeyRound size={18} /></span><div><h3>Local security</h3><p>Runtime protections are fixed for v1.</p></div></header><div className="security-grid"><div><CheckCircle2 size={17} /><span><strong>Loopback only</strong><small>Server binds to 127.0.0.1 with strict Host and Origin checks.</small></span></div><div><CheckCircle2 size={17} /><span><strong>No remote model</strong><small>The dashboard contains no LLM, embeddings, or network scans.</small></span></div><div><CheckCircle2 size={17} /><span><strong>Safe previews</strong><small>Secrets, unsafe formats, traversal, and escaping symlinks are blocked.</small></span></div><div><CheckCircle2 size={17} /><span><strong>Research files stay untouched</strong><small>Monitor data is central SQLite metadata only.</small></span></div></div></section>
}

function Lifecycle({ snapshot }: { snapshot: ProjectSnapshot }) {
  const navigate = useNavigate()
  const [relink, setRelink] = useState(false)
  const [path, setPath] = useState(snapshot.project.root_path)
  const mutation = useProjectMutation(snapshot)
  const act = async (type: string, data: Record<string, unknown> = {}) => { await mutation.mutateAsync(operation(type, data, { id: snapshot.project.id })); if (type === 'project.trash') navigate('/?show=trash'); if (type === 'project.restore' && snapshot.project.trashed) navigate(`/projects/${snapshot.project.id}/overview`) }
  if (snapshot.project.trashed) return <section className="settings-section danger-section"><header><span className="settings-icon"><DatabaseBackup size={18} /></span><div><h3>Recoverable trash</h3><p>This monitor is hidden but all UUIDs, tasks, artifacts, and history remain intact.</p></div></header><div className="lifecycle-actions"><div><span><strong>Restore monitor</strong><small>Return this project to the active portfolio without touching its research folder.</small></span><Button variant="secondary" onClick={() => act('project.restore')}><RotateCcw size={15} />Restore</Button></div><div><span><strong>Permanent purge</strong><small>CLI-only: stop the server, then confirm the exact project UUID. A verified backup is created first.</small></span></div></div>{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}</section>
  return <section className="settings-section danger-section"><header><span className="settings-icon"><DatabaseBackup size={18} /></span><div><h3>Project lifecycle</h3><p>These actions affect only the monitor record, never the enrolled research folder.</p></div></header><div className="lifecycle-actions"><div><span><strong>Relink project folder</strong><small>Choose a replacement if the enrolled root has moved.</small></span><Button variant="secondary" onClick={() => setRelink(true)}><RotateCcw size={15} />Relink</Button></div><div><span><strong>{snapshot.project.archived ? 'Restore from archive' : 'Archive monitor'}</strong><small>Keep all data while hiding this project from the active portfolio.</small></span><Button variant="secondary" onClick={() => act(snapshot.project.archived ? 'project.restore' : 'project.archive')}>{snapshot.project.archived ? <RotateCcw size={15} /> : <Archive size={15} />}{snapshot.project.archived ? 'Restore' : 'Archive'}</Button></div><div><span><strong>Move monitor to trash</strong><small>The monitor remains recoverable. Permanent purge is CLI-only and requires a fresh backup.</small></span><Button variant="danger" onClick={() => window.confirm(`Move ${snapshot.project.name} to recoverable trash?`) && act('project.trash')}><Trash2 size={15} />Move to trash</Button></div></div>{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<Dialog open={relink} onClose={() => setRelink(false)} title="Relink project folder" description="Monitor UUIDs, tasks, history, and artifact records remain unchanged."><form className="form-stack" onSubmit={async (e) => { e.preventDefault(); await act('project.relink', { root_path: path }); setRelink(false) }}><Field label="New absolute folder path"><input required value={path} onChange={(e) => setPath(e.target.value)} spellCheck={false} /></Field><Notice tone="warning">Artifact paths will be revalidated against the replacement root. Missing files create warnings but do not alter task status.</Notice><div className="dialog-actions"><Button type="button" variant="ghost" onClick={() => setRelink(false)}>Cancel</Button><Button type="submit">Relink folder</Button></div></form></Dialog></section>
}

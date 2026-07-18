import { useEffect, useMemo, useRef, useState } from 'react'
import { Archive, BrainCircuit, CheckCircle2, DatabaseBackup, FolderCog, FolderPlus, KeyRound, LockKeyhole, RotateCcw, Save, Shield, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { PlanningProfile, ProjectSnapshot, ScanPolicy } from '../types'
import { operation } from '../lib/api'
import { useProjectMutation } from '../lib/hooks'
import { shortPath } from '../lib/format'
import { createTaskLabeler } from '../lib/taskLabels'
import { Badge, Button, Dialog, Field, Notice } from '../components/ui'

export function SettingsView({ snapshot }: { snapshot: ProjectSnapshot }) {
  return (
    <div className="view-page settings-view">
      <header className="view-toolbar"><div><h2>Project settings</h2><p>Configure monitor metadata, approved roots, and the exact boundaries Codex must respect.</p></div></header>
      <ProjectDetails snapshot={snapshot} />
      <PlanningProfileEditor snapshot={snapshot} />
      <ScanPolicyEditor snapshot={snapshot} />
      <ArtifactRoots snapshot={snapshot} />
      <SecuritySummary />
      <Lifecycle snapshot={snapshot} />
    </div>
  )
}

const defaultPlanningProfile: PlanningProfile = {
  task_granularity: 'balanced',
  max_nesting_depth: 3,
  planning_horizon: 'current_milestone',
  inference_policy: 'cautious_gaps',
  max_new_tasks_per_proposal: 30,
  preferred_pipeline_names: [],
  terminology_notes: '',
  additional_instructions: '',
  protected_pipeline_ids: [],
  protected_task_ids: [],
  version: 1,
}

const normalizeLines = (value: string) => value.split('\n').map((line) => line.trim()).filter(Boolean)

function normalizeUniqueLines(value: string, caseInsensitive = false) {
  const seen = new Set<string>()
  return normalizeLines(value).filter((line) => {
    const key = caseInsensitive ? line.toLocaleLowerCase() : line
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function normalizePreferredNames(value: string) {
  const seen = new Set<string>()
  return normalizeLines(value).filter((line) => {
    const key = line.toLocaleLowerCase()
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function normalizePlanningProfile(profile: PlanningProfile, preferredNames: string): PlanningProfile {
  return { ...profile, preferred_pipeline_names: normalizePreferredNames(preferredNames) }
}

function projectVersion(snapshot: ProjectSnapshot) {
  return snapshot.project.version ?? snapshot.project.entity_version
}

function normalizeProjectDetails<T extends { name: string; description: string; research_goal: string; success_criteria: string; color: string }>(value: T) {
  return {
    ...value,
    name: value.name.trim(),
    description: value.description.trim(),
    research_goal: value.research_goal.trim(),
    success_criteria: value.success_criteria.trim(),
    color: value.color.toLocaleLowerCase(),
  }
}

function staleAutomationSummary(snapshot: ProjectSnapshot) {
  const intentCount = snapshot.automation_state?.unexpired_unconsumed_intent_count
    ?? snapshot.automation_state?.active_intent_count
    ?? 0
  const draftCount = snapshot.automation_state?.open_draft_count ?? 0
  const parts = [
    intentCount ? `${intentCount} active guided request${intentCount === 1 ? '' : 's'}` : '',
    draftCount ? `${draftCount} open proposal draft${draftCount === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
  return parts.length ? `Saving will make ${parts.join(' and ')} stale.` : ''
}

function PlanningProfileEditor({ snapshot }: { snapshot: ProjectSnapshot }) {
  const incoming = snapshot.planning_profile ?? defaultPlanningProfile
  const incomingNames = incoming.preferred_pipeline_names.join('\n')
  const incomingSignature = JSON.stringify(normalizePlanningProfile(incoming, incomingNames))
  const [profile, setProfile] = useState<PlanningProfile>(incoming)
  const [preferredNames, setPreferredNames] = useState(incomingNames)
  const [externalUpdate, setExternalUpdate] = useState(false)
  const source = useRef({ projectId: snapshot.project.id, signature: incomingSignature })
  const mutation = useProjectMutation(snapshot)
  const normalizedProfile = useMemo(() => normalizePlanningProfile(profile, preferredNames), [preferredNames, profile])
  const normalizedSignature = JSON.stringify(normalizedProfile)
  const dirty = normalizedSignature !== source.current.signature
  useEffect(() => {
    if (source.current.projectId !== snapshot.project.id) {
      source.current = { projectId: snapshot.project.id, signature: incomingSignature }
      setProfile(incoming); setPreferredNames(incomingNames); setExternalUpdate(false)
    } else if (source.current.signature !== incomingSignature) {
      if (dirty) setExternalUpdate(true)
      else { source.current.signature = incomingSignature; setProfile(incoming); setPreferredNames(incomingNames); setExternalUpdate(false) }
    }
  }, [dirty, incomingNames, incomingSignature, snapshot.project.id])
  const change = <K extends keyof PlanningProfile>(key: K, value: PlanningProfile[K]) => {
    setProfile((current) => ({ ...current, [key]: value }))
  }
  const toggleId = (key: 'protected_pipeline_ids' | 'protected_task_ids', id: string, checked: boolean) => {
    const next = checked ? [...new Set([...profile[key], id])] : profile[key].filter((item) => item !== id)
    change(key, next)
  }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await mutation.mutateAsync(operation('planning_profile.update', normalizedProfile as unknown as Record<string, unknown>, { id: snapshot.project.id, version: profile.version }))
      source.current.signature = normalizedSignature
      setProfile(normalizedProfile); setPreferredNames(normalizedProfile.preferred_pipeline_names.join('\n')); setExternalUpdate(false)
    } catch { /* rendered below */ }
  }
  const activePipelines = snapshot.pipelines.filter((pipeline) => !pipeline.deleted_at && !pipeline.archived)
  const activePipelineIds = new Set(activePipelines.map((pipeline) => pipeline.id))
  const activeTasks = snapshot.tasks.filter((task) => !task.deleted_at && activePipelineIds.has(task.pipeline_id))
  const labelTask = createTaskLabeler(activePipelines, activeTasks)
  return <section className="settings-section">
    <header><span className="settings-icon"><BrainCircuit size={18} /></span><div><h3>Codex planning profile</h3><p>Define how much future structure Codex may propose and which monitor areas it must not touch.</p></div><Badge tone="green"><LockKeyhole size={12} />Human controlled</Badge></header>
    <form className="settings-form" onSubmit={save}>
      <div className="form-grid two">
        <Field label="Task granularity"><select value={profile.task_granularity} onChange={(event) => change('task_granularity', event.target.value as PlanningProfile['task_granularity'])}><option value="coarse">Coarse</option><option value="balanced">Balanced</option><option value="detailed">Detailed</option></select></Field>
        <Field label="Planning horizon"><select value={profile.planning_horizon} onChange={(event) => change('planning_horizon', event.target.value as PlanningProfile['planning_horizon'])}><option value="immediate">Immediate work</option><option value="current_milestone">Current milestone</option><option value="whole_project">Whole project</option></select></Field>
        <Field label="Inference policy"><select value={profile.inference_policy} onChange={(event) => change('inference_policy', event.target.value as PlanningProfile['inference_policy'])}><option value="sources_only">Sources only</option><option value="cautious_gaps">Cautious gaps</option><option value="broad_roadmap">Broad roadmap</option></select></Field>
        <Field label="Maximum nesting depth"><input type="number" min={1} max={6} value={profile.max_nesting_depth} onChange={(event) => change('max_nesting_depth', Number(event.target.value))} /></Field>
        <Field label="Maximum new tasks per proposal"><input type="number" min={1} max={100} value={profile.max_new_tasks_per_proposal} onChange={(event) => change('max_new_tasks_per_proposal', Number(event.target.value))} /></Field>
      </div>
      <Field label="Preferred pipeline names" hint="One name per line; at most 20. Names are normalized when saved."><textarea rows={4} value={preferredNames} onChange={(event) => setPreferredNames(event.target.value)} /></Field>
      <Field label="Project terminology" hint="Explain domain-specific names or abbreviations; up to 4 KiB."><textarea rows={3} maxLength={4096} value={profile.terminology_notes} onChange={(event) => change('terminology_notes', event.target.value)} /></Field>
      <Field label="Additional planning instructions" hint="Human-owned guidance included in guided contexts; up to 8 KiB."><textarea rows={4} maxLength={8192} value={profile.additional_instructions} onChange={(event) => change('additional_instructions', event.target.value)} /></Field>
      <div className="protection-grid">
        <fieldset><legend>Protected pipelines</legend><p>Protection covers current and future tasks in the pipeline.</p>{activePipelines.map((pipeline) => <label key={pipeline.id}><input type="checkbox" checked={profile.protected_pipeline_ids.includes(pipeline.id)} onChange={(event) => toggleId('protected_pipeline_ids', pipeline.id, event.target.checked)} /><span>{pipeline.title}</span></label>)}{!activePipelines.length && <small>No active pipelines.</small>}</fieldset>
        <fieldset><legend>Protected task subtrees</legend><p>Protection follows every current and future descendant.</p>{activeTasks.map((task) => <label key={task.id}><input type="checkbox" checked={profile.protected_task_ids.includes(task.id)} onChange={(event) => toggleId('protected_task_ids', task.id, event.target.checked)} /><span>{labelTask(task)}</span></label>)}{!activeTasks.length && <small>No active tasks.</small>}</fieldset>
      </div>
      {dirty && staleAutomationSummary(snapshot) && <Notice tone="warning">{staleAutomationSummary(snapshot)}</Notice>}
      {externalUpdate && <Notice tone="warning">The planning profile changed elsewhere while you were editing. Your draft is preserved; review it before saving.</Notice>}
      {mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}
      <div className="settings-actions"><Button type="submit" disabled={!dirty || mutation.isPending}><Save size={16} />{mutation.isPending ? 'Saving…' : 'Save planning profile'}</Button></div>
    </form>
  </section>
}

function ProjectDetails({ snapshot }: { snapshot: ProjectSnapshot }) {
  const project = snapshot.project
  const incoming = { name: project.name, description: project.description ?? '', research_goal: project.research_goal ?? '', success_criteria: project.success_criteria ?? '', color: project.color }
  const incomingSignature = JSON.stringify(normalizeProjectDetails(incoming))
  const [form, setForm] = useState(incoming)
  const [externalUpdate, setExternalUpdate] = useState(false)
  const source = useRef({ projectId: project.id, signature: incomingSignature })
  const mutation = useProjectMutation(snapshot)
  const normalizedForm = normalizeProjectDetails(form)
  const normalizedSignature = JSON.stringify(normalizedForm)
  const dirty = normalizedSignature !== source.current.signature
  useEffect(() => {
    if (source.current.projectId !== project.id) {
      source.current = { projectId: project.id, signature: incomingSignature }
      setForm(incoming); setExternalUpdate(false)
    } else if (source.current.signature !== incomingSignature) {
      if (dirty) setExternalUpdate(true)
      else { source.current.signature = incomingSignature; setForm(incoming); setExternalUpdate(false) }
    }
  }, [dirty, incomingSignature, project.id])
  const change = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => { setForm((current) => ({ ...current, [key]: value })) }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await mutation.mutateAsync(operation('project.update', normalizedForm, { id: project.id, version: projectVersion(snapshot) }))
      source.current.signature = normalizedSignature
      setForm(normalizedForm); setExternalUpdate(false)
    } catch { /* rendered by the mutation notice */ }
  }
  return <section className="settings-section"><header><span className="settings-icon"><FolderCog size={18} /></span><div><h3>Project details</h3><p>Describe the research objective separately from its task status.</p></div></header><form className="settings-form" onSubmit={save}><div className="form-grid two"><Field label="Project name"><input required value={form.name} onChange={(e) => change('name', e.target.value)} /></Field><Field label="Color"><div className="color-input"><input type="color" value={form.color} onChange={(e) => change('color', e.target.value)} /><span>{form.color}</span></div></Field></div><Field label="Description"><textarea rows={2} value={form.description} onChange={(e) => change('description', e.target.value)} /></Field><Field label="Research goal"><textarea rows={3} value={form.research_goal} onChange={(e) => change('research_goal', e.target.value)} /></Field><Field label="Success criteria"><textarea rows={3} value={form.success_criteria} onChange={(e) => change('success_criteria', e.target.value)} placeholder="What evidence would make this project scientifically successful?" /></Field>{externalUpdate && <Notice tone="warning">Project details changed in another UI or CLI action while you were editing. Your draft is preserved; saving may report a version conflict.</Notice>}{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<div className="settings-actions"><Button type="submit" disabled={!dirty || mutation.isPending}><Save size={16} />{mutation.isPending ? 'Saving…' : 'Save details'}</Button></div></form></section>
}

function ScanPolicyEditor({ snapshot }: { snapshot: ProjectSnapshot }) {
  const normalizedIncoming: ScanPolicy = {
    ...snapshot.scan_policy,
    readable_source_root_ids: snapshot.scan_policy.readable_source_root_ids ?? [],
    max_files_per_scan: snapshot.scan_policy.max_files_per_scan ?? 500,
    max_total_text_bytes: snapshot.scan_policy.max_total_text_bytes ?? 10_485_760,
  }
  const incomingDrafts = {
    preferred_sources: normalizedIncoming.preferred_sources.join('\n'),
    include_globs: normalizedIncoming.include_globs.join('\n'),
    exclude_globs: normalizedIncoming.exclude_globs.join('\n'),
    sensitive_patterns: normalizedIncoming.sensitive_patterns.join('\n'),
  }
  const [policy, setPolicy] = useState(normalizedIncoming)
  const [drafts, setDrafts] = useState(incomingDrafts)
  const [externalUpdate, setExternalUpdate] = useState(false)
  const incomingSignature = JSON.stringify(normalizedIncoming)
  const source = useRef({ projectId: snapshot.project.id, signature: incomingSignature })
  const mutation = useProjectMutation(snapshot)
  const normalizedPolicy = useMemo<ScanPolicy>(() => ({
    ...policy,
    preferred_sources: normalizeUniqueLines(drafts.preferred_sources),
    include_globs: normalizeUniqueLines(drafts.include_globs),
    exclude_globs: normalizeUniqueLines(drafts.exclude_globs),
    sensitive_patterns: normalizeUniqueLines(drafts.sensitive_patterns, true),
  }), [drafts, policy])
  const normalizedSignature = JSON.stringify(normalizedPolicy)
  const dirty = normalizedSignature !== source.current.signature
  useEffect(() => {
    if (source.current.projectId !== snapshot.project.id) {
      source.current = { projectId: snapshot.project.id, signature: incomingSignature }
      setPolicy(normalizedIncoming); setDrafts(incomingDrafts); setExternalUpdate(false)
    } else if (source.current.signature !== incomingSignature) {
      if (dirty) setExternalUpdate(true)
      else { source.current.signature = incomingSignature; setPolicy(normalizedIncoming); setDrafts(incomingDrafts); setExternalUpdate(false) }
    }
  }, [dirty, incomingSignature, snapshot.project.id])
  const changePolicy = (next: ScanPolicy) => { setPolicy(next) }
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await mutation.mutateAsync(operation('scan_policy.update', normalizedPolicy as unknown as Record<string, unknown>, { id: snapshot.project.id, version: policy.version }))
      source.current.signature = normalizedSignature
      setPolicy(normalizedPolicy)
      setDrafts({
        preferred_sources: normalizedPolicy.preferred_sources.join('\n'),
        include_globs: normalizedPolicy.include_globs.join('\n'),
        exclude_globs: normalizedPolicy.exclude_globs.join('\n'),
        sensitive_patterns: normalizedPolicy.sensitive_patterns.join('\n'),
      })
      setExternalUpdate(false)
    } catch { /* rendered by the mutation notice */ }
  }
  const changeDraft = (key: keyof typeof drafts, value: string) => setDrafts((current) => ({ ...current, [key]: value }))
  const toggleReadableRoot = (id: string, checked: boolean) => {
    const current = policy.readable_source_root_ids ?? []
    changePolicy({ ...policy, readable_source_root_ids: checked ? [...new Set([...current, id])] : current.filter((item) => item !== id) })
  }
  return <section className="settings-section">
    <header><span className="settings-icon"><Shield size={18} /></span><div><h3>Codex scan policy</h3><p>Codex receives these boundaries through the companion skill. It cannot modify them.</p></div><Badge tone="green"><LockKeyhole size={12} />Human controlled</Badge></header>
    <form className="settings-form" onSubmit={save}>
      <Field label="Preferred source-of-truth files" hint="One project-relative path per line, in priority order. Blank lines are removed when saved."><textarea rows={4} value={drafts.preferred_sources} onChange={(e) => changeDraft('preferred_sources', e.target.value)} placeholder={'EXPERIMENT_PLAN.md\nrefine-logs/EXPERIMENT_TRACKER.md'} spellCheck={false} /></Field>
      <div className="form-grid two"><Field label="Include globs"><textarea rows={4} value={drafts.include_globs} onChange={(e) => changeDraft('include_globs', e.target.value)} spellCheck={false} /></Field><Field label="Exclude globs"><textarea rows={4} value={drafts.exclude_globs} onChange={(e) => changeDraft('exclude_globs', e.target.value)} spellCheck={false} /></Field></div>
      <Field label="Sensitive path patterns"><textarea rows={3} value={drafts.sensitive_patterns} onChange={(e) => changeDraft('sensitive_patterns', e.target.value)} spellCheck={false} /></Field>
      <div className="form-grid two">
        <Field label="Maximum files per scan"><input type="number" min={1} max={5000} value={policy.max_files_per_scan} onChange={(e) => changePolicy({ ...policy, max_files_per_scan: Number(e.target.value) })} /></Field>
        <Field label="Maximum total text bytes"><input type="number" min={1024} max={104_857_600} value={policy.max_total_text_bytes} onChange={(e) => changePolicy({ ...policy, max_total_text_bytes: Number(e.target.value) })} /></Field>
        <Field label="Maximum bytes per text file"><input type="number" min={1024} max={10_485_760} value={policy.max_text_file_size} onChange={(e) => changePolicy({ ...policy, max_text_file_size: Number(e.target.value) })} /></Field>
        <Field label="Git history limit"><input type="number" min={0} max={1000} value={policy.git_history_limit} onChange={(e) => changePolicy({ ...policy, git_history_limit: Number(e.target.value) })} /></Field>
      </div>
      <fieldset className="readable-roots"><legend>Readable source roots</legend><p>The enrolled project root is always readable. Artifact-root approval alone does not grant Codex read access.</p><label className="disabled"><input type="checkbox" checked disabled /><span><strong>Project root</strong><small>{snapshot.project.root_path}</small></span></label>{snapshot.artifact_roots.filter((root) => !root.is_project_root).map((root) => <label key={root.id}><input type="checkbox" checked={(policy.readable_source_root_ids ?? []).includes(root.id)} onChange={(event) => toggleReadableRoot(root.id, event.target.checked)} /><span><strong>{root.name}</strong><small>{root.canonical_path}</small></span></label>)}</fieldset>
      <div className="checkbox-stack"><label><input type="checkbox" checked={policy.allow_git_metadata} onChange={(e) => changePolicy({ ...policy, allow_git_metadata: e.target.checked })} /><span><strong>Allow bounded Git metadata</strong><small>Status, log, diff-stat, and tracked-file metadata from the project root only.</small></span></label><label className="disabled"><input type="checkbox" checked={false} disabled /><span><strong>Follow symlinks</strong><small>Always disabled.</small></span></label></div>
      {dirty && staleAutomationSummary(snapshot) && <Notice tone="warning">{staleAutomationSummary(snapshot)}</Notice>}
      {externalUpdate && <Notice tone="warning">The scan policy changed in another UI or CLI action while you were editing. Your draft is preserved; review it before retrying a conflicted save.</Notice>}
      {mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}
      <div className="settings-actions"><Button type="submit" disabled={!dirty || mutation.isPending}><Save size={16} />{mutation.isPending ? 'Saving…' : 'Save scan policy'}</Button></div>
    </form>
  </section>
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
  const act = async (type: string, data: Record<string, unknown> = {}) => { await mutation.mutateAsync(operation(type, data, { id: snapshot.project.id, version: projectVersion(snapshot) })); if (type === 'project.trash') navigate('/?show=trash'); if (type === 'project.restore' && snapshot.project.trashed) navigate(`/projects/${snapshot.project.id}/overview`) }
  if (snapshot.project.trashed) return <section className="settings-section danger-section"><header><span className="settings-icon"><DatabaseBackup size={18} /></span><div><h3>Recoverable trash</h3><p>This monitor is hidden but all UUIDs, tasks, artifacts, and history remain intact.</p></div></header><div className="lifecycle-actions"><div><span><strong>Restore monitor</strong><small>Return this project to the active portfolio without touching its research folder.</small></span><Button variant="secondary" disabled={mutation.isPending} onClick={() => act('project.restore')}><RotateCcw size={15} />Restore</Button></div><div><span><strong>Permanent purge</strong><small>CLI-only: stop the server, then confirm the exact project UUID. A verified backup is created first.</small></span></div></div>{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}</section>
  return <section className="settings-section danger-section"><header><span className="settings-icon"><DatabaseBackup size={18} /></span><div><h3>Project lifecycle</h3><p>These actions affect only the monitor record, never the enrolled research folder.</p></div></header><div className="lifecycle-actions"><div><span><strong>Relink project folder</strong><small>Choose a replacement if the enrolled root has moved.</small></span><Button variant="secondary" disabled={mutation.isPending} onClick={() => setRelink(true)}><RotateCcw size={15} />Relink</Button></div><div><span><strong>{snapshot.project.archived ? 'Restore from archive' : 'Archive monitor'}</strong><small>Keep all data while hiding this project from the active portfolio.</small></span><Button variant="secondary" disabled={mutation.isPending} onClick={() => act(snapshot.project.archived ? 'project.restore' : 'project.archive')}>{snapshot.project.archived ? <RotateCcw size={15} /> : <Archive size={15} />}{snapshot.project.archived ? 'Restore' : 'Archive'}</Button></div><div><span><strong>Move monitor to trash</strong><small>The monitor remains recoverable. Permanent purge is CLI-only and requires a fresh backup.</small></span><Button variant="danger" disabled={mutation.isPending} onClick={() => window.confirm(`Move ${snapshot.project.name} to recoverable trash?`) && act('project.trash')}><Trash2 size={15} />Move to trash</Button></div></div>{mutation.error && <Notice tone="danger">{mutation.error.message}</Notice>}<Dialog open={relink} onClose={() => setRelink(false)} title="Relink project folder" description="Monitor UUIDs, tasks, history, and artifact records remain unchanged."><form className="form-stack" onSubmit={async (e) => { e.preventDefault(); await act('project.relink', { root_path: path }); setRelink(false) }}><Field label="New absolute folder path"><input required value={path} onChange={(e) => setPath(e.target.value)} spellCheck={false} /></Field><Notice tone="warning">Artifact paths will be revalidated against the replacement root. Missing files create warnings but do not alter task status.</Notice><div className="dialog-actions"><Button type="button" variant="ghost" onClick={() => setRelink(false)}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>Relink folder</Button></div></form></Dialog></section>
}

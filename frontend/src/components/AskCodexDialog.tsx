import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, Clipboard, FileSearch, Plus, RefreshCw, ShieldCheck, Sparkles, Trash2 } from 'lucide-react'
import type {
  AgentArtifactLocator,
  AgentPrompt,
  AgentPromptRequest,
  AgentScopeType,
  GuidedWorkflowMode,
  ProjectSnapshot,
} from '../types'
import { GUIDED_WORKFLOW_MODES } from '../types'
import { api } from '../lib/api'
import { humanize } from '../lib/format'
import { createTaskLabeler } from '../lib/taskLabels'
import { Badge, Button, Dialog, Field, Notice } from './ui'

export type GuidedRequestSeed = {
  mode?: GuidedWorkflowMode
  scopeType?: AgentScopeType
  scopeId?: string | null
  regenerateProposalId?: string | null
  instructions?: string
  allowCompletion?: boolean
  artifactLocators?: AgentArtifactLocator[]
}

type GuidedIntentDraft = Pick<GuidedRequestSeed, 'instructions' | 'allowCompletion' | 'artifactLocators'>

const guidedIntentDraftKey = (intentId: string) => `research-monitor-guided-intent-draft:${intentId}`

export function readGuidedIntentDraft(intentId?: string | null): GuidedIntentDraft {
  if (!intentId) return {}
  try {
    const value = JSON.parse(window.localStorage.getItem(guidedIntentDraftKey(intentId)) ?? '{}') as GuidedIntentDraft
    return {
      instructions: typeof value.instructions === 'string' ? value.instructions.slice(0, 8192) : undefined,
      allowCompletion: typeof value.allowCompletion === 'boolean' ? value.allowCompletion : undefined,
      artifactLocators: Array.isArray(value.artifactLocators) ? value.artifactLocators.slice(0, 50) : undefined,
    }
  } catch {
    return {}
  }
}

function writeGuidedIntentDraft(intentId: string, value: GuidedIntentDraft) {
  try {
    window.localStorage.setItem(guidedIntentDraftKey(intentId), JSON.stringify(value))
  } catch {
    // Private browsing and constrained webviews may deny storage; regeneration still keeps mode and scope.
  }
}

function locatorDrafts(values: AgentArtifactLocator[] = []): LocatorDraft[] {
  return values.slice(0, 50).map((value) => ({ ...value, clientId: crypto.randomUUID() }))
}

type LocatorDraft = AgentArtifactLocator & { clientId: string }

type PromptMutationRequest = {
  projectId: string
  promptContextSignature: string
  requestSignature: string
  payload: AgentPromptRequest
  forceFresh: boolean
}

const modeCopy: Record<GuidedWorkflowMode, { title: string; description: string; scopes: AgentScopeType[] }> = {
  initialize_structure: {
    title: 'Initialize structure',
    description: 'Draft pipelines and top-level planned tasks for an empty monitor.',
    scopes: ['project'],
  },
  expand_task: {
    title: 'Expand a task',
    description: 'Draft planned descendants and internal dependencies for one task.',
    scopes: ['task'],
  },
  reconcile_progress: {
    title: 'Reconcile progress',
    description: 'Compare permitted sources with existing tasks and propose evidence-backed progress.',
    scopes: ['project', 'pipeline', 'task'],
  },
  suggest_next_work: {
    title: 'Suggest next work',
    description: 'Draft future planned work without recording progress.',
    scopes: ['project', 'pipeline'],
  },
  record_update: {
    title: 'Record an update',
    description: 'Turn your note into a journal proposal for exactly one task.',
    scopes: ['task'],
  },
  link_artifacts: {
    title: 'Link artifacts',
    description: 'Propose links from explicit local paths or HTTP(S) URLs to one task.',
    scopes: ['task'],
  },
}

const emptyLocator = (artifactRootId?: string): LocatorDraft => ({
  clientId: crypto.randomUUID(),
  kind: 'local',
  locator: '',
  artifact_root_id: artifactRootId ?? null,
  label: '',
  provider: '',
})

export function modeEligibility(snapshot: ProjectSnapshot, mode: GuidedWorkflowMode): string | null {
  if (snapshot.project.archived) return 'Archived projects cannot issue guided requests.'
  if (snapshot.project.trashed) return 'Projects in trash cannot issue guided requests.'
  if (snapshot.project.unavailable) return 'Relink the unavailable project folder first.'
  const activePipelines = snapshot.pipelines.filter((pipeline) => !pipeline.archived && !pipeline.deleted_at)
  const activePipelineIds = new Set(activePipelines.map((pipeline) => pipeline.id))
  const activeTasks = snapshot.tasks.filter((task) => !task.deleted_at && activePipelineIds.has(task.pipeline_id))
  const eligibleTasks = activeTasks.filter((task) => !scopeProtectionReason(snapshot, 'task', task.id))
  const eligibleExpansionTasks = eligibleTasks.filter((task) => !['done', 'dropped'].includes(task.status))
  if (mode === 'initialize_structure' && (activePipelines.length || activeTasks.length)) {
    return 'Initialization is available only when the monitor has no active pipelines or tasks.'
  }
  if (mode === 'expand_task' && !eligibleExpansionTasks.length) {
    return 'Create or restore an active, unprotected task before using this workflow.'
  }
  if (['record_update', 'link_artifacts'].includes(mode) && !eligibleTasks.length) return 'Create or restore an unprotected task before using this workflow.'
  return null
}

function scopeProtectionReason(snapshot: ProjectSnapshot, scopeType: AgentScopeType, scopeId: string): string | null {
  if (!scopeId || scopeType === 'project') return null
  const profile = snapshot.planning_profile
  if (!profile) return null
  const protectedPipelines = new Set(profile.protected_pipeline_ids ?? [])
  if (scopeType === 'pipeline') {
    return protectedPipelines.has(scopeId) ? 'Choose an unprotected pipeline.' : null
  }
  const tasksById = new Map(snapshot.tasks.map((task) => [task.id, task]))
  const protectedTasks = new Set(profile.protected_task_ids ?? [])
  let current = tasksById.get(scopeId)
  const visited = new Set<string>()
  while (current && !visited.has(current.id)) {
    if (protectedTasks.has(current.id) || protectedPipelines.has(current.pipeline_id)) {
      return 'Choose a task outside protected pipelines and task subtrees.'
    }
    visited.add(current.id)
    current = current.parent_id ? tasksById.get(current.parent_id) : undefined
  }
  return null
}

function scopeIneligibilityReason(snapshot: ProjectSnapshot, scopeType: AgentScopeType, scopeId: string, mode?: GuidedWorkflowMode): string | null {
  if (mode === 'expand_task' && scopeType === 'task' && scopeId) {
    const task = snapshot.tasks.find((item) => item.id === scopeId)
    if (task && ['done', 'dropped'].includes(task.status)) return 'Choose an active, nonterminal task.'
  }
  return scopeProtectionReason(snapshot, scopeType, scopeId)
}

export function AskCodexDialog({ open, onClose, snapshot, seed = {} }: {
  open: boolean
  onClose: () => void
  snapshot: ProjectSnapshot
  seed?: GuidedRequestSeed
}) {
  const defaultMode = seed.mode ?? (snapshot.tasks.length ? 'reconcile_progress' : 'initialize_structure')
  const [mode, setMode] = useState<GuidedWorkflowMode>(defaultMode)
  const [scopeType, setScopeType] = useState<AgentScopeType>(seed.scopeType ?? modeCopy[defaultMode].scopes[0])
  const [scopeId, setScopeId] = useState(seed.scopeId ?? '')
  const [scopeSearch, setScopeSearch] = useState('')
  const [instructions, setInstructions] = useState(seed.instructions ?? '')
  const [allowCompletion, setAllowCompletion] = useState(seed.allowCompletion ?? false)
  const [locators, setLocators] = useState<LocatorDraft[]>(() => locatorDrafts(seed.artifactLocators))
  const [generated, setGenerated] = useState<AgentPrompt | null>(null)
  const [generatedSignature, setGeneratedSignature] = useState('')
  const [copyStatus, setCopyStatus] = useState('')
  const [promptClock, setPromptClock] = useState(() => Date.now())
  const promptRef = useRef<HTMLTextAreaElement>(null)
  const seedSignature = JSON.stringify(seed)
  const lastSeed = useRef(seedSignature)
  const promptContextSignature = JSON.stringify({
    project_id: snapshot.project.id,
    semantic_revision: snapshot.project.semantic_revision,
    planning_profile_version: snapshot.planning_profile?.version ?? null,
  })
  const promptContextRef = useRef(promptContextSignature)
  const lastProjectId = useRef(snapshot.project.id)
  const lastPromptContext = useRef(promptContextSignature)
  promptContextRef.current = promptContextSignature
  const activePipelines = useMemo(
    () => snapshot.pipelines.filter((pipeline) => !pipeline.deleted_at && !pipeline.archived).sort((a, b) => a.position - b.position),
    [snapshot.pipelines],
  )
  const activePipelineIds = useMemo(() => new Set(activePipelines.map((pipeline) => pipeline.id)), [activePipelines])
  const activeTasks = useMemo(
    () => snapshot.tasks.filter((task) => !task.deleted_at && activePipelineIds.has(task.pipeline_id)).sort((a, b) => a.title.localeCompare(b.title)),
    [activePipelineIds, snapshot.tasks],
  )
  const labelTask = useMemo(() => createTaskLabeler(activePipelines, activeTasks), [activePipelines, activeTasks])
  const normalizedScopeSearch = scopeSearch.trim().toLocaleLowerCase()
  const visiblePipelines = activePipelines.filter((pipeline) => !normalizedScopeSearch || pipeline.title.toLocaleLowerCase().includes(normalizedScopeSearch) || pipeline.id === scopeId)
  const visibleTasks = activeTasks
    .filter((task) => !normalizedScopeSearch || labelTask(task).toLocaleLowerCase().includes(normalizedScopeSearch) || task.id === scopeId)
    .sort((left, right) => labelTask(left).localeCompare(labelTask(right)))
  const projectRootId = snapshot.artifact_roots.find((root) => root.is_project_root)?.id
  const skill = useQuery({
    queryKey: ['skill-status'],
    queryFn: api.getSkillStatus,
    enabled: open,
    retry: false,
  })
  const proposals = useQuery({
    queryKey: ['proposals', snapshot.project.id, 'summary-count', 'open'],
    queryFn: () => api.getProposalPage(snapshot.project.id, { status: 'open', limit: 1, summary: true }),
    enabled: open,
    retry: false,
  })
  const openDraftCount = proposals.data?.total ?? proposals.data?.draft_count ?? 0

  useLayoutEffect(() => {
    const projectChanged = lastProjectId.current !== snapshot.project.id
    const promptContextChanged = lastPromptContext.current !== promptContextSignature
    if (!projectChanged && !promptContextChanged) return
    lastProjectId.current = snapshot.project.id
    lastPromptContext.current = promptContextSignature
    setGenerated(null)
    setGeneratedSignature('')
    setCopyStatus('')
    if (!projectChanged) return

    const nextMode = snapshot.tasks.length ? 'reconcile_progress' : 'initialize_structure'
    setMode(nextMode)
    setScopeType(modeCopy[nextMode].scopes[0])
    setScopeId('')
    setScopeSearch('')
    setInstructions('')
    setAllowCompletion(false)
    setLocators([])
    // A seed can carry task IDs and instructions from the prior project. Do not
    // let the ordinary seed effect reapply it after this project-bound reset.
    lastSeed.current = seedSignature
  }, [promptContextSignature, seedSignature, snapshot.project.id, snapshot.tasks.length])
  useEffect(() => {
    if (!open || lastSeed.current === seedSignature) return
    lastSeed.current = seedSignature
    const nextMode = seed.mode ?? (snapshot.tasks.length ? 'reconcile_progress' : 'initialize_structure')
    setMode(nextMode)
    setScopeType(seed.scopeType ?? modeCopy[nextMode].scopes[0])
    setScopeId(seed.scopeId ?? '')
    setScopeSearch('')
    if (seed.instructions !== undefined) setInstructions(seed.instructions)
    if (seed.allowCompletion !== undefined) setAllowCompletion(seed.allowCompletion)
    if (seed.artifactLocators !== undefined) setLocators(locatorDrafts(seed.artifactLocators))
    setGenerated(null)
    setGeneratedSignature('')
    setCopyStatus('')
  }, [open, seed.mode, seed.scopeId, seed.scopeType, seedSignature, snapshot.tasks.length])

  const allowedScopes = modeCopy[mode].scopes
  const payload: AgentPromptRequest = {
    mode,
    scope_type: scopeType,
    scope_id: scopeType === 'project' ? null : scopeId || null,
    instructions: instructions.trim(),
    allow_completion: mode === 'record_update' ? allowCompletion : false,
    artifact_locators: locators
      .filter((item) => item.locator.trim())
      .map(({ clientId: _clientId, ...item }) => ({ ...item, locator: item.locator.trim() })),
    regenerate_proposal_id: seed.regenerateProposalId ?? null,
  }
  const payloadSignature = JSON.stringify({ prompt_context: JSON.parse(promptContextSignature), payload })
  const reason = modeEligibility(snapshot, mode)
  const validationError = (scopeType !== 'project' && !scopeId ? 'Choose a scope.' : null)
    ?? scopeIneligibilityReason(snapshot, scopeType, scopeId, mode)
    ?? reason
    ?? (mode === 'record_update' && !instructions.trim() ? 'Enter the update you want recorded.' : null)
    ?? (mode === 'link_artifacts' && !payload.artifact_locators?.length ? 'Add at least one artifact locator.' : null)
    ?? (payload.artifact_locators?.some((locator) => locator.kind === 'local' && !locator.artifact_root_id) ? 'Local artifact locators require an approved root.' : null)
  const createPrompt = useMutation({
    mutationFn: (request: PromptMutationRequest) => api.createAgentPrompt(request.projectId, { ...request.payload, force_fresh: request.forceFresh }),
    onSuccess: (value, request) => {
      // Ignore a response issued for a project/revision that is no longer on
      // screen. This also closes the async race where project A completes after
      // the user has navigated to project B.
      if (request.promptContextSignature !== promptContextRef.current) return
      setGenerated(value)
      setGeneratedSignature(request.requestSignature)
      setPromptClock(Date.now())
      setCopyStatus('')
      writeGuidedIntentDraft(value.intent_id, {
        instructions: request.payload.instructions,
        allowCompletion: request.payload.allow_completion,
        artifactLocators: request.payload.artifact_locators,
      })
    },
  })
  const issuePrompt = (forceFresh = false) => createPrompt.mutate({
    projectId: snapshot.project.id,
    promptContextSignature,
    requestSignature: payloadSignature,
    payload,
    forceFresh,
  })

  const chooseMode = (next: GuidedWorkflowMode) => {
    const scopes = modeCopy[next].scopes
    setMode(next)
    if (!scopes.includes(scopeType)) {
      setScopeType(scopes[0])
      setScopeId('')
    }
    setScopeSearch('')
    if (next !== 'record_update') setAllowCompletion(false)
    if (!['record_update', 'link_artifacts'].includes(next)) setLocators([])
    setCopyStatus('')
  }
  const changePayload = (change: () => void) => {
    change()
    setCopyStatus('')
  }
  const copyPrompt = async () => {
    if (!effectivePrompt?.prompt || promptExpired) return
    try {
      await navigator.clipboard.writeText(effectivePrompt.prompt)
      setCopyStatus('Prompt copied. Running it in Codex—not copying it—is the disclosure step.')
    } catch {
      promptRef.current?.focus()
      promptRef.current?.select()
      setCopyStatus('Clipboard access was denied. The full prompt is selected; press Ctrl+C.')
    }
  }
  const selectPrompt = () => {
    if (promptExpired) return
    promptRef.current?.focus()
    promptRef.current?.select()
    setCopyStatus('Full prompt selected.')
  }
  const addLocator = () => setLocators((current) => [...current, emptyLocator(projectRootId)])
  const updateLocator = <K extends keyof LocatorDraft>(id: string, key: K, value: LocatorDraft[K]) => {
    changePayload(() => setLocators((current) => current.map((item) => item.clientId === id ? { ...item, [key]: value } : item)))
  }
  const changeLocatorKind = (id: string, kind: 'local' | 'url') => {
    changePayload(() => setLocators((current) => current.map((item) => item.clientId === id
      ? { ...item, kind, artifact_root_id: kind === 'local' ? projectRootId ?? null : null }
      : item)))
  }
  const resolvedSkill = generated?.skill_status ?? skill.data
  const promptIsCurrent = generated && generatedSignature === payloadSignature
  const effectivePrompt = promptIsCurrent ? generated : null
  const expiresAt = effectivePrompt ? Date.parse(effectivePrompt.expires_at) : Number.NaN
  const promptExpired = Boolean(effectivePrompt && (!Number.isFinite(expiresAt) || expiresAt <= promptClock))
  const skillCommand = resolvedSkill?.setup_command ?? resolvedSkill?.command ?? (resolvedSkill?.status === 'current'
    ? 'research-monitor skill status'
    : resolvedSkill?.status === 'blocked'
      ? 'CODEX_HOME=/safe/codex-home research-monitor skill install'
      : resolvedSkill?.status === 'missing'
        ? 'research-monitor skill install'
        : 'research-monitor skill update')
  useEffect(() => {
    if (!effectivePrompt || !Number.isFinite(expiresAt)) return
    const remaining = expiresAt - Date.now()
    if (remaining <= 0) {
      setPromptClock(Date.now())
      return
    }
    const timer = window.setTimeout(() => {
      setPromptClock(Date.now())
      if (Date.now() >= expiresAt) setCopyStatus('This bound request expired. Create a fresh request before copying it.')
    }, Math.min(remaining + 10, 2_147_483_647))
    return () => window.clearTimeout(timer)
  }, [effectivePrompt?.intent_id, effectivePrompt?.expires_at, expiresAt, promptClock])

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Ask Codex"
      description="Create a server-bound, review-only request. Research Monitor never applies the result automatically."
      wide
    >
      <div className="ask-codex-layout">
        <div className="ask-codex-form">
          <fieldset className="mode-picker">
            <legend>What should Codex do?</legend>
            {GUIDED_WORKFLOW_MODES.map((item) => {
              const disabledReason = modeEligibility(snapshot, item)
              return (
                <label key={item} className={mode === item ? 'selected' : ''} title={disabledReason ?? undefined}>
                  <input type="radio" name="guided-mode" value={item} checked={mode === item} disabled={Boolean(disabledReason)} onChange={() => chooseMode(item)} />
                  <span><strong>{modeCopy[item].title}</strong><small>{disabledReason ?? modeCopy[item].description}</small></span>
                </label>
              )
            })}
          </fieldset>

          <div className="form-grid two">
            <Field label="Scope type">
              <select value={scopeType} onChange={(event) => changePayload(() => { setScopeType(event.target.value as AgentScopeType); setScopeId(''); setScopeSearch('') })}>
                {allowedScopes.map((scope) => <option key={scope} value={scope}>{humanize(scope)}</option>)}
              </select>
            </Field>
            {scopeType !== 'project' && <Field label={`Search ${scopeType} scopes`}>
              <input type="search" value={scopeSearch} onChange={(event) => setScopeSearch(event.target.value)} placeholder={`Filter ${scopeType}s by name or hierarchy…`} />
            </Field>}
            {scopeType === 'pipeline' && <Field label="Pipeline">
              <select required value={scopeId} onChange={(event) => changePayload(() => setScopeId(event.target.value))}>
                <option value="">Choose a pipeline…</option>
                {visiblePipelines.map((pipeline) => {
                  const ineligibleReason = scopeIneligibilityReason(snapshot, 'pipeline', pipeline.id, mode)
                  return <option key={pipeline.id} value={pipeline.id} disabled={Boolean(ineligibleReason)}>{pipeline.title}{ineligibleReason ? ' — protected' : ''}</option>
                })}
              </select>
            </Field>}
            {scopeType === 'task' && <Field label="Task">
              <select required value={scopeId} onChange={(event) => changePayload(() => setScopeId(event.target.value))}>
                <option value="">Choose a task…</option>
                {visibleTasks.map((task) => {
                  const ineligibleReason = scopeIneligibilityReason(snapshot, 'task', task.id, mode)
                  const suffix = ineligibleReason ? ` — ${mode === 'expand_task' && ['done', 'dropped'].includes(task.status) ? 'terminal' : 'protected'}` : ''
                  return <option key={task.id} value={task.id} disabled={Boolean(ineligibleReason)}>{labelTask(task)}{suffix}</option>
                })}
              </select>
            </Field>}
          </div>

          <Field
            label={mode === 'record_update' ? 'Update to record' : 'Instructions for Codex'}
            hint={mode === 'record_update' ? 'Required. This note is bound to the guided request.' : 'Optional guidance; up to 8 KiB.'}
          >
            <textarea rows={4} maxLength={8192} required={mode === 'record_update'} value={instructions} onChange={(event) => changePayload(() => setInstructions(event.target.value))} placeholder={mode === 'record_update' ? 'What changed, and what evidence supports it?' : 'Add any context or emphasis for this request…'} />
          </Field>

          {mode === 'record_update' && <label className="guarded-checkbox">
            <input type="checkbox" checked={allowCompletion} onChange={(event) => changePayload(() => setAllowCompletion(event.target.checked))} />
            <span><strong>Allow this request to propose completion</strong><small>Completion still needs explicit evidence and your graphical approval.</small></span>
          </label>}

          {['record_update', 'link_artifacts'].includes(mode) && <section className="locator-editor">
            <header><div><strong>Explicit artifact locators</strong><small>{mode === 'link_artifacts' ? 'At least one is required.' : 'Optional evidence named by you.'}</small></div><Button type="button" variant="secondary" size="sm" onClick={addLocator}><Plus size={14} />Add locator</Button></header>
            {locators.map((locator) => <div className={`locator-row ${locator.kind}`} key={locator.clientId}>
              <select aria-label="Artifact locator type" value={locator.kind} onChange={(event) => changeLocatorKind(locator.clientId, event.target.value as 'local' | 'url')}>
                <option value="local">Local path</option><option value="url">HTTP(S) URL</option>
              </select>
              {locator.kind === 'local' && <select aria-label="Approved root" value={locator.artifact_root_id ?? ''} onChange={(event) => updateLocator(locator.clientId, 'artifact_root_id', event.target.value || null)}>
                {!snapshot.artifact_roots.length && <option value="">No approved roots</option>}
                {snapshot.artifact_roots.map((root) => <option key={root.id} value={root.id}>{root.is_project_root ? 'Project root' : root.name}</option>)}
              </select>}
              <input aria-label="Artifact locator" value={locator.locator} onChange={(event) => updateLocator(locator.clientId, 'locator', event.target.value)} placeholder={locator.kind === 'local' ? 'results/summary.json' : 'https://wandb.ai/…'} spellCheck={false} />
              <Button type="button" variant="ghost" size="icon" aria-label="Remove artifact locator" onClick={() => setLocators((current) => current.filter((item) => item.clientId !== locator.clientId))}><Trash2 size={15} /></Button>
            </div>)}
          </section>}

          <div className="guided-disclosure">
            <div><ShieldCheck size={16} /><span><strong>Scan policy</strong><small>{snapshot.scan_policy.max_files_per_scan ?? 500} files · {formatBytes(snapshot.scan_policy.max_total_text_bytes ?? 10 * 1024 * 1024)} total text · symlinks never followed</small></span></div>
            <div><FileSearch size={16} /><span><strong>Readable roots</strong><small>Project root{snapshot.scan_policy.readable_source_root_ids?.length ? ` + ${snapshot.scan_policy.readable_source_root_ids.length} explicitly approved` : ' only'}</small></span></div>
            <div><Sparkles size={16} /><span><strong>Planning policy</strong><small>{humanize(snapshot.planning_profile?.inference_policy ?? 'cautious_gaps')} · depth {snapshot.planning_profile?.max_nesting_depth ?? 3} · at most {snapshot.planning_profile?.max_new_tasks_per_proposal ?? 30} new tasks</small></span></div>
          </div>

          {resolvedSkill && <Notice tone={resolvedSkill.status === 'current' ? 'success' : 'warning'}>
            <strong>Optional companion skill: {resolvedSkill.label ?? humanize(resolvedSkill.status)}</strong>
            <p>{resolvedSkill.status === 'current'
              ? 'The optional Codex integration is ready. Manual monitoring works independently.'
              : `Prompt generation remains available, but this ${humanize(resolvedSkill.status)} skill may not run the guided workflow correctly. Manual monitoring is unaffected.`}</p>
            {resolvedSkill.blocking_reason && <p>{resolvedSkill.blocking_reason}</p>}
            {skillCommand && <p>{resolvedSkill.status === 'current' ? 'Verify' : 'Set up'} with <code>{skillCommand}</code>.</p>}
          </Notice>}
          {openDraftCount > 0 && <Notice tone="warning">
            <strong>{openDraftCount} open proposal draft{openDraftCount === 1 ? '' : 's'}</strong>
            <p>A fresh guided request is still allowed. <a href={`/projects/${snapshot.project.id}/proposals`}>Review existing drafts</a> to avoid overlapping work.</p>
          </Notice>}
          {skill.error && !resolvedSkill && <Notice tone="warning">Skill status is unavailable. You can still generate and copy the prompt; verify the skill with <code>research-monitor skill status</code>.</Notice>}
          {validationError && <Notice tone="warning">{validationError}</Notice>}
          {createPrompt.error && <Notice tone="danger">{createPrompt.error.message}</Notice>}

          <div className="dialog-actions">
            <Button type="button" variant="ghost" onClick={onClose}>Close</Button>
            <Button type="button" disabled={Boolean(validationError) || createPrompt.isPending} onClick={() => issuePrompt(Boolean(effectivePrompt))}>
              <Sparkles size={16} />{createPrompt.isPending ? 'Creating bound request…' : promptExpired ? 'Create fresh request' : effectivePrompt ? 'Fresh scan request' : 'Generate prompt'}
            </Button>
          </div>
        </div>

        <aside className="prompt-preview" aria-label="Generated Codex prompt">
          <header><div><strong>Server-generated prompt</strong><small>{effectivePrompt ? promptExpired ? `Expired ${new Date(effectivePrompt.expires_at).toLocaleString()}` : `Expires ${new Date(effectivePrompt.expires_at).toLocaleString()}` : 'Generate a request to preview it.'}</small></div>{effectivePrompt && !promptExpired && <Badge tone="green">Bound request</Badge>}{effectivePrompt && promptExpired && <Badge tone="red">Expired</Badge>}</header>
          <textarea ref={promptRef} readOnly rows={22} value={effectivePrompt?.prompt ?? ''} placeholder="The complete, review-only Codex prompt will appear here." aria-label="Complete generated Codex prompt" />
          {effectivePrompt?.context_command && <p className="context-command"><code>{effectivePrompt.context_command}</code></p>}
          {effectivePrompt?.warnings?.map((warning) => <Notice key={warning.code} tone="warning">{warning.message}{warning.proposal_ids?.length ? <> <a href={`/projects/${snapshot.project.id}/proposals`}>Review drafts</a></> : null}</Notice>)}
          {promptExpired && <Notice tone="warning"><strong>This guided request expired.</strong><p>Create a fresh request at the current semantic revision before copying or running it.</p></Notice>}
          <Notice tone="warning">{effectivePrompt?.disclosure ?? 'Copying sends nothing. Running this prompt in Codex may send the disclosed monitor context and scan-policy-permitted project text to OpenAI.'}</Notice>
          <div className="button-row">
            <Button type="button" variant="secondary" disabled={!effectivePrompt || promptExpired} onClick={selectPrompt}>Select all</Button>
            <Button type="button" disabled={!effectivePrompt || promptExpired} onClick={copyPrompt}>{copyStatus.startsWith('Prompt copied') ? <Check size={16} /> : <Clipboard size={16} />}Copy prompt</Button>
            {effectivePrompt && <Button type="button" variant="ghost" title="Mint a fresh intent at the current semantic revision" onClick={() => issuePrompt(true)}><RefreshCw size={15} />{promptExpired ? 'Create fresh request' : 'Fresh scan'}</Button>}
          </div>
          <p className="copy-status" aria-live="polite">{copyStatus}</p>
        </aside>
      </div>
    </Dialog>
  )
}

function formatBytes(value: number) {
  if (value >= 1024 * 1024) return `${Math.round(value / (1024 * 1024))} MiB`
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`
  return `${value} bytes`
}

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, CheckCircle2, ChevronDown, ChevronRight, Clipboard, Edit3, FileSearch, GitMerge, ShieldCheck, Sparkles, X } from 'lucide-react'
import type { EvidenceItem, ProjectSnapshot, Proposal, ProposalOperation } from "../types"
import { api } from '../lib/api'
import { formatDate, humanize } from '../lib/format'
import { Badge, Button, Dialog, EmptyState, ErrorState, Field, Notice, Spinner } from "../components/ui"
import { ProposedOutline } from "./proposals/ProposedOutline"

export function ProposalsView({ snapshot }: { snapshot: ProjectSnapshot }) {
  const query = useQuery({ queryKey: ['proposals', snapshot.project.id], queryFn: () => api.getProposals(snapshot.project.id) })
  if (query.isLoading) return <div className="content-loading"><Spinner label="Loading Codex proposals…" /></div>
  if (query.error) return <ErrorState error={query.error} retry={() => query.refetch()} />
  const proposals = query.data ?? []
  const drafts = proposals.filter((proposal) => proposal.status === 'draft')
  const closed = proposals.filter((proposal) => !drafts.includes(proposal))
  return (
    <div className="view-page proposals-view">
      <header className="view-toolbar"><div><h2>Codex proposals</h2><p>Every agent-generated change stays a draft until you inspect and accept it here.</p></div><Badge tone="green"><ShieldCheck size={13} />Review required</Badge></header>
      <Notice>Codex can inspect permitted project content and propose monitor updates. It cannot enroll folders, approve roots, edit research files, or apply its own changes.</Notice>
      {!proposals.length ? <EmptyState icon={<FileSearch size={28} />} title="No proposals yet" description="Copy the Codex prompt from the project header, run it from your project folder, and the resulting draft will appear here." /> : <div className="proposal-sections">
        <section><div className="section-heading"><div><h3>Awaiting review</h3><p>{drafts.length} proposal{drafts.length === 1 ? '' : 's'} need your decision.</p></div></div>{drafts.length ? <div className="proposal-list">{drafts.map((proposal) => <ProposalCard key={proposal.id} snapshot={snapshot} proposal={proposal} />)}</div> : <div className="mini-empty"><CheckCircle2 size={20} /><p>All proposals have been reviewed.</p></div>}</section>
        {closed.length > 0 && <section><div className="section-heading"><div><h3>Review history</h3><p>Applied, rejected, conflicted, or superseded proposals retain their proposal-time diffs.</p></div></div><div className="proposal-list">{closed.map((proposal) => <ProposalCard key={proposal.id} snapshot={snapshot} proposal={proposal} />)}</div></section>}
      </div>}
    </div>
  )
}

function ProposalCard({ snapshot, proposal }: { snapshot: ProjectSnapshot; proposal: Proposal }) {
  const client = useQueryClient()
  const reviewable = proposal.status === "draft"
  const originalOperations = useMemo(
    () => proposal.operations.map(cleanProposalOperation),
    [proposal.operations],
  )
  const originalSignature = useMemo(() => proposalOperationsSignature(originalOperations), [originalOperations])
  const originalById = useMemo(
    () => new Map(originalOperations.map((operation) => [operation.id, operation])),
    [originalOperations],
  )
  const storedById = useMemo(
    () => new Map(proposal.operations.map((operation) => [operation.id, operation])),
    [proposal.operations],
  )
  const recoveryKey = proposalRecoveryKey(snapshot.project.id, proposal.id)
  const [stagedOperations, setStagedOperations] = useState<ProposalOperation[]>(() => (
    readProposalRecovery(recoveryKey, snapshot.project.id, proposal.id, originalSignature) ?? originalOperations
  ))
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<ProposalOperation | null>(null)
  const [activeTab, setActiveTab] = useState<"outline" | "audit">(reviewable && proposal.base_semantic_revision === snapshot.project.semantic_revision ? "outline" : "audit")
  const [selectionMessage, setSelectionMessage] = useState("")
  const [copied, setCopied] = useState(false)
  const [stagedJsonCopied, setStagedJsonCopied] = useState(false)
  const [copyError, setCopyError] = useState<string | null>(null)
  const revisionRequestIds = useRef<Map<string, string>>(new Map())
  const applyRequestIds = useRef<Map<string, string>>(new Map())
  const rejectRequestIds = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    setStagedOperations(
      readProposalRecovery(recoveryKey, snapshot.project.id, proposal.id, originalSignature)
        ?? originalOperations.map(cleanProposalOperation),
    )
    setSelected(new Set())
    setExpanded(new Set())
    setEditing(null)
    setActiveTab(reviewable && proposal.base_semantic_revision === snapshot.project.semantic_revision ? "outline" : "audit")
    setSelectionMessage("")
    revisionRequestIds.current.clear()
    applyRequestIds.current.clear()
    rejectRequestIds.current.clear()
  }, [proposal.id, proposal.status, originalSignature, recoveryKey, snapshot.project.id])

  const stale = proposal.base_semantic_revision !== snapshot.project.semantic_revision
  const stagedSignature = proposalOperationsSignature(stagedOperations)
  const hasStagedChanges = stagedSignature !== originalSignature
  const dirty = reviewable && hasStagedChanges
  const recoveryConflict = !reviewable && hasStagedChanges
  const showReviewWorkspace = reviewable || recoveryConflict
  const emptyStagedDraft = dirty && stagedOperations.length === 0
  const selectedOperationIds = [...selected].sort()
  const applyPayloadSignature = stableJson({
    action: "proposal.apply",
    project_id: snapshot.project.id,
    proposal_id: proposal.id,
    selected_operation_ids: selectedOperationIds,
    operation_overrides: [],
  })
  const revisionPayloadSignature = stableJson({
    action: "proposal.revise",
    project_id: snapshot.project.id,
    proposal_id: proposal.id,
    base_semantic_revision: proposal.base_semantic_revision,
    summary: proposal.summary,
    rationale: proposal.rationale ?? "",
    operations: stagedOperations,
  })
  useEffect(() => {
    if (stale) setActiveTab("audit")
  }, [stale])
  useEffect(() => {
    if (hasStagedChanges) {
      writeProposalRecovery(recoveryKey, snapshot.project.id, proposal.id, originalSignature, stagedOperations)
    } else {
      clearProposalRecovery(recoveryKey)
    }
  }, [hasStagedChanges, originalSignature, proposal.id, recoveryKey, snapshot.project.id, stagedOperations, stagedSignature])
  useEffect(() => {
    if (!hasStagedChanges) return
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", warnBeforeUnload)
    return () => window.removeEventListener("beforeunload", warnBeforeUnload)
  }, [hasStagedChanges])

  const invalidateProposalData = () => Promise.all([
    client.invalidateQueries({ queryKey: ["proposals", snapshot.project.id] }),
    client.invalidateQueries({ queryKey: ["snapshot", snapshot.project.id] }),
    client.invalidateQueries({ queryKey: ["history", snapshot.project.id] }),
    client.invalidateQueries({ queryKey: ["projects"] }),
    client.invalidateQueries({ queryKey: ["project-search", snapshot.project.id] }),
  ])
  const apply = useMutation({
    mutationFn: () => {
      let requestId = applyRequestIds.current.get(applyPayloadSignature)
      if (!requestId) {
        requestId = crypto.randomUUID()
        applyRequestIds.current.set(applyPayloadSignature, requestId)
      }
      return api.applyProposal(snapshot.project.id, proposal.id, selectedOperationIds, [], requestId)
    },
    onSuccess: invalidateProposalData,
  })
  const saveRevision = useMutation({
    mutationFn: () => {
      let requestId = revisionRequestIds.current.get(revisionPayloadSignature)
      if (!requestId) {
        requestId = crypto.randomUUID()
        revisionRequestIds.current.set(revisionPayloadSignature, requestId)
      }
      return api.reviseProposal(
        snapshot.project.id,
        proposal.id,
        proposal.base_semantic_revision,
        proposal.summary,
        proposal.rationale ?? "",
        stagedOperations,
        requestId,
      )
    },
    onSuccess: async () => {
      clearProposalRecovery(recoveryKey)
      setStagedOperations(originalOperations.map(cleanProposalOperation))
      setSelected(new Set())
      setEditing(null)
      await invalidateProposalData()
    },
  })
  const reject = useMutation({
    mutationFn: (reason?: string) => {
      const normalizedReason = reason ?? ""
      const payloadSignature = stableJson({
        action: "proposal.reject",
        project_id: snapshot.project.id,
        proposal_id: proposal.id,
        reason: normalizedReason,
      })
      let requestId = rejectRequestIds.current.get(payloadSignature)
      if (!requestId) {
        requestId = crypto.randomUUID()
        rejectRequestIds.current.set(payloadSignature, requestId)
      }
      return api.rejectProposal(snapshot.project.id, proposal.id, normalizedReason, requestId)
    },
    onSuccess: invalidateProposalData,
  })

  const pendingIds = reviewable ? stagedOperations.map((operation) => operation.id) : []
  const updateStagedOperations = (operations: ProposalOperation[]) => {
    const cleaned = operations.map(cleanProposalOperation)
    const retainedIds = new Set(cleaned.map((operation) => operation.id))
    setStagedOperations(cleaned)
    setSelected((current) => new Set([...current].filter((id) => retainedIds.has(id))))
    setEditing((current) => current && retainedIds.has(current.id)
      ? cleaned.find((operation) => operation.id === current.id) ?? null
      : null)
  }
  const discardEdits = () => {
    clearProposalRecovery(recoveryKey)
    setStagedOperations(originalOperations.map(cleanProposalOperation))
    setSelected(new Set())
    setEditing(null)
    setSelectionMessage("Discarded staged edits; approval selection was reset.")
  }
  const toggleOperation = (operationId: string, checked: boolean) => {
    if (stale || dirty) return
    const operation = stagedOperations.find((item) => item.id === operationId)
    if (!operation) return
    setSelected((current) => {
      const next = checked
        ? selectionClosure(stagedOperations, current, operationId)
        : deselectionClosure(stagedOperations, current, operationId)
      const affected = Math.max(0, Math.abs(next.size - current.size) - 1)
      setSelectionMessage(affected
        ? `${checked ? "Selected" : "Deselected"} ${operationTitle(operation)} and ${affected} required or dependent operation${affected === 1 ? "" : "s"}.`
        : `${checked ? "Selected" : "Deselected"} ${operationTitle(operation)}.`)
      return next
    })
  }
  const allSelected = pendingIds.length > 0 && pendingIds.every((id) => selected.has(id))
  const auditOperations = stagedOperations.map((operation) => mergeProposalHistory(operation, storedById.get(operation.id)))
  const confidence = stagedOperations.filter((operation) => operation.confidence != null)
  const averageConfidence = confidence.length
    ? confidence.reduce((sum, operation) => sum + Number(operation.confidence), 0) / confidence.length
    : null
  const mutationError = apply.error || saveRevision.error || reject.error

  const copyRegenerationPrompt = async () => {
    const text = `Use $research-monitor for project ${snapshot.project.id} at ${snapshot.project.root_path}. Regenerate proposal ${proposal.id} against current semantic revision ${snapshot.project.semantic_revision}; the existing draft is stale. Inspect permitted project content read-only, submit a new reviewable proposal, and do not apply it.`
    try {
      await navigator.clipboard.writeText(text)
      setCopyError(null)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch (error) {
      setCopyError(error instanceof Error ? error.message : "Clipboard access failed.")
    }
  }

  const copyStagedJson = async () => {
    const text = JSON.stringify({
      schema_version: "1",
      project_id: snapshot.project.id,
      proposal_id: proposal.id,
      base_semantic_revision: proposal.base_semantic_revision,
      operations: stagedOperations,
    }, null, 2)
    try {
      await navigator.clipboard.writeText(text)
      setCopyError(null)
      setStagedJsonCopied(true)
      window.setTimeout(() => setStagedJsonCopied(false), 1800)
    } catch (error) {
      setCopyError(error instanceof Error ? error.message : "Clipboard access failed.")
    }
  }

  const applyLabel = recoveryConflict
    ? "Draft closed — cannot apply"
    : stale
    ? "Regeneration required"
    : dirty
      ? "Save draft before applying"
      : `Apply ${selected.size} selected`
  const handleTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    const tablist = event.currentTarget.closest('[role="tablist"]')
    const tabs = tablist ? [...tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]')] : []
    const currentIndex = tabs.indexOf(event.currentTarget)
    if (currentIndex < 0 || !tabs.length) return
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : event.key === 'ArrowRight'
          ? (currentIndex + 1) % tabs.length
          : (currentIndex - 1 + tabs.length) % tabs.length
    event.preventDefault()
    tabs[nextIndex].focus()
    tabs[nextIndex].click()
  }

  return (
    <article className="proposal-card">
      <header className="proposal-header">
        <span className="proposal-agent"><Bot size={20} /></span>
        <div>
          <div className="proposal-title-line">
            <h3>{proposal.summary}</h3>
            {!reviewable && <Badge tone={proposal.status === "applied" ? "green" : proposal.status === "conflict" ? "red" : "muted"}>{humanize(proposal.status)}</Badge>}
          </div>
          <p>{proposal.rationale || "Codex submitted a structured set of monitor updates for review."}</p>
          <small>
            {proposal.actor_label || "Codex"} · {formatDate(proposal.created_at, true)} · base revision {proposal.base_semantic_revision}
            {proposal.supersedes_proposal_id && <> · revised from <code>{shortProposalId(proposal.supersedes_proposal_id)}</code></>}
          </small>
        </div>
        {averageConfidence != null && <div className="confidence"><span>{Math.round(averageConfidence * 100)}%</span><small>avg. confidence</small></div>}
      </header>

      {proposal.supersedes_proposal_id && <Notice>
        <strong>Human-reviewed replacement draft.</strong>
        <p>This proposal supersedes <code>{proposal.supersedes_proposal_id}</code>. The server revalidated the complete draft and assigned canonical operation IDs.</p>
      </Notice>}
      {proposal.status === "superseded" && <Notice>
        <strong>This draft was superseded without changing its recorded operations.</strong>
        <p>{proposal.superseded_by_proposal_id
          ? <>Continue review in replacement <code>{proposal.superseded_by_proposal_id}</code>.</>
          : "A newer reviewed draft replaced this version."}</p>
      </Notice>}
      {reviewable && stale && <Notice tone="warning"><div><strong>This proposal is stale and cannot be applied.</strong><p>Regenerate it against semantic revision {snapshot.project.semantic_revision}, then review the new draft.</p><Button type="button" size="sm" variant="secondary" onClick={copyRegenerationPrompt}><Clipboard size={13} />{copied ? "Prompt copied" : "Copy regeneration prompt"}</Button>{copyError && <small>{copyError}</small>}</div></Notice>}
      {reviewable && dirty && !emptyStagedDraft && <Notice tone="warning"><div><strong>You have unsaved staging edits.</strong><p>Save a reviewed replacement draft to validate the complete outline before selecting operations to apply.</p></div></Notice>}
      {emptyStagedDraft && <Notice tone="warning"><div><strong>No operations remain in this staged draft.</strong><p>A reviewed replacement must contain at least one operation. Add work in the proposed outline, or restore the original operations and then reject the proposal.</p><Button type="button" size="sm" variant="secondary" disabled={saveRevision.isPending} onClick={discardEdits}>Restore original operations</Button></div></Notice>}
      {recoveryConflict && <Notice tone="danger"><div><strong>Recovered staged edits conflict with a closed proposal.</strong><p>The server marked this draft {humanize(proposal.status)} while you had local edits. Your staged outline is preserved below in read-only mode; copy it before discarding if you need to recreate the work in a new draft.</p>{copyError && <small>{copyError}</small>}</div></Notice>}

      {showReviewWorkspace && <div className="proposal-select-all">
        <span role="tablist" aria-label={recoveryConflict ? "Recovered proposal review mode" : "Proposal review mode"}>
          <Button id={`proposal-outline-tab-${proposal.id}`} type="button" size="sm" variant={activeTab === "outline" ? "secondary" : "ghost"} role="tab" aria-selected={activeTab === "outline"} aria-controls={`proposal-outline-${proposal.id}`} tabIndex={activeTab === "outline" ? 0 : -1} onKeyDown={handleTabKeyDown} onClick={() => setActiveTab("outline")}>Proposed outline</Button>
          <Button id={`proposal-audit-tab-${proposal.id}`} type="button" size="sm" variant={activeTab === "audit" ? "secondary" : "ghost"} role="tab" aria-selected={activeTab === "audit"} aria-controls={`proposal-audit-${proposal.id}`} tabIndex={activeTab === "audit" ? 0 : -1} onKeyDown={handleTabKeyDown} onClick={() => setActiveTab("audit")}>Operation audit</Button>
        </span>
        <span>{recoveryConflict ? "Recovered edits · read-only" : dirty ? "Unsaved staged changes" : `${stagedOperations.length} validated operations`}</span>
      </div>}

      {showReviewWorkspace && activeTab === "outline" ? (
        <div id={`proposal-outline-${proposal.id}`} role="tabpanel" aria-labelledby={`proposal-outline-tab-${proposal.id}`}>
          <ProposedOutline
            snapshot={snapshot}
            operations={stagedOperations}
            onChange={updateStagedOperations}
            disabled={stale || recoveryConflict}
          />
        </div>
      ) : (
        <div id={`proposal-audit-${proposal.id}`} role={showReviewWorkspace ? "tabpanel" : undefined} aria-labelledby={showReviewWorkspace ? `proposal-audit-tab-${proposal.id}` : undefined}>
          {reviewable && <div className="proposal-select-all"><label><input type="checkbox" disabled={stale || dirty || !pendingIds.length} checked={allSelected} onChange={(event) => { const next = event.target.checked ? new Set(pendingIds) : new Set<string>(); setSelected(next); setSelectionMessage(event.target.checked ? `Selected all ${pendingIds.length} staged operations.` : "Deselected all staged operations.") }} />Select all staged operations</label><span>{selected.size} of {pendingIds.length} selected</span></div>}
          {reviewable && <p className="selection-message" aria-live="polite">{selectionMessage}</p>}
          <div className="operation-list">{auditOperations.map((operation) => {
            const isExpanded = expanded.has(operation.id)
            const changed = operationChanged(operation, originalById.get(operation.id))
            const detailsId = `proposal-operation-${operation.id}`
            return <div className={`operation-row ${selected.has(operation.id) ? "selected" : ""}`} key={operation.id}>
              <div className="operation-summary">
                {reviewable && <input type="checkbox" disabled={stale || dirty} checked={selected.has(operation.id)} onChange={(event) => toggleOperation(operation.id, event.target.checked)} aria-label={`Select ${operationTitle(operation)} (${humanize(operation.type)})`} />}
                <span className="operation-icon"><GitMerge size={16} /></span>
                <button aria-expanded={isExpanded} aria-controls={detailsId} onClick={() => setExpanded((previous) => toggleSet(previous, operation.id))}>
                  <strong>{operationTitle(operation)}{changed && <Badge tone="amber">Staged edit</Badge>}</strong>
                  <small><Badge tone={operationTone(operation.type)}>{humanize(operation.type)}</Badge>{operation.rationale || "No rationale supplied"}{operation.prerequisite_operation_ids?.length ? ` · ${operation.prerequisite_operation_ids.length} prerequisite` : ""}</small>
                </button>
                <span className="operation-meta">{operation.confidence != null && `${Math.round(operation.confidence * 100)}%`}{isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
              </div>
              {isExpanded && <OperationDetails id={detailsId} snapshot={snapshot} operation={operation} isEdited={changed} onEdit={reviewable && !stale && !recoveryConflict ? () => setEditing(cleanProposalOperation(operation)) : undefined} />}
            </div>
          })}</div>
        </div>
      )}

      {mutationError && <Notice tone="danger">{mutationError.message}</Notice>}
      {showReviewWorkspace && <footer className="proposal-actions">
        <Button variant="danger" disabled={!reviewable || dirty || reject.isPending || saveRevision.isPending} onClick={() => { if (!reviewable || dirty) return; const reason = window.prompt("Optional reason for rejecting this proposal:"); if (reason !== null) reject.mutate(reason || undefined) }}><X size={16} />Reject proposal</Button>
        {recoveryConflict && <Button type="button" variant="secondary" onClick={copyStagedJson}><Clipboard size={16} />{stagedJsonCopied ? "Staged JSON copied" : "Copy staged JSON"}</Button>}
        {hasStagedChanges && !emptyStagedDraft && <Button type="button" variant="ghost" disabled={saveRevision.isPending} onClick={discardEdits}>{recoveryConflict ? "Discard recovered edits" : "Discard edits"}</Button>}
        {hasStagedChanges && <Button type="button" variant="secondary" disabled={!reviewable || stale || emptyStagedDraft || saveRevision.isPending} onClick={() => { if (!emptyStagedDraft) saveRevision.mutate() }}><ShieldCheck size={16} />{saveRevision.isPending ? "Saving reviewed draft…" : emptyStagedDraft ? "No operations to save" : "Save reviewed draft"}</Button>}
        <Button disabled={!reviewable || recoveryConflict || stale || dirty || !selected.size || apply.isPending || saveRevision.isPending} onClick={() => apply.mutate()}><Check size={16} />{apply.isPending ? "Applying…" : applyLabel}</Button>
      </footer>}
      {reviewable && <OperationEditor operation={editing} onClose={() => setEditing(null)} onSave={(operation) => {
        setStagedOperations((current) => current.map((item) => item.id === operation.id ? cleanProposalOperation(operation) : item))
        setEditing(null)
      }} />}
    </article>
  )
}

function cleanProposalOperation(operation: ProposalOperation): ProposalOperation {
  const {
    disposition: _disposition,
    before: _before,
    after: _after,
    ...transport
  } = operation
  return {
    ...transport,
    data: { ...transport.data },
    prerequisite_operation_ids: transport.prerequisite_operation_ids
      ? [...transport.prerequisite_operation_ids]
      : [],
    evidence: transport.evidence?.map((item) => typeof item === "string" ? item : { ...item }),
    source_references: transport.source_references?.map((item) => ({ ...item })),
  }
}

function stableJson(value: unknown): string {
  const normalize = (item: unknown): unknown => {
    if (Array.isArray(item)) return item.map(normalize)
    if (!item || typeof item !== "object") return item
    return Object.fromEntries(
      Object.entries(item as Record<string, unknown>)
        .filter(([, child]) => child !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, child]) => [key, normalize(child)]),
    )
  }
  return JSON.stringify(normalize(value))
}

function proposalOperationsSignature(operations: ProposalOperation[]): string {
  return stableJson([...operations].sort((left, right) => left.id.localeCompare(right.id)))
}

type ProposalRecoveryRecord = {
  version: 1
  project_id: string
  proposal_id: string
  original_signature: string
  operations: ProposalOperation[]
}

const proposalRecoveryMemory = new Map<string, ProposalRecoveryRecord>()

function proposalRecoveryKey(projectId: string, proposalId: string) {
  return `research-monitor:proposal-staging:v1:${projectId}:${proposalId}`
}

function readProposalRecovery(
  key: string,
  projectId: string,
  proposalId: string,
  originalSignature: string,
): ProposalOperation[] | null {
  if (typeof window === "undefined") return null
  let parsed: Partial<ProposalRecoveryRecord> | undefined = proposalRecoveryMemory.get(key)
  try {
    const serialized = window.sessionStorage.getItem(key)
    if (serialized) {
      try {
        parsed = JSON.parse(serialized) as Partial<ProposalRecoveryRecord>
      } catch {
        clearProposalRecovery(key)
        return null
      }
    }
  } catch {
    // Fall back to the same-tab in-memory record.
  }
  if (!parsed) return null
  if (
    parsed.version !== 1
    || parsed.project_id !== projectId
    || parsed.proposal_id !== proposalId
    || parsed.original_signature !== originalSignature
    || !Array.isArray(parsed.operations)
    || !parsed.operations.every(isStoredProposalOperation)
  ) {
    clearProposalRecovery(key)
    return null
  }
  const record = parsed as ProposalRecoveryRecord
  proposalRecoveryMemory.set(key, record)
  return record.operations.map(cleanProposalOperation)
}

function writeProposalRecovery(
  key: string,
  projectId: string,
  proposalId: string,
  originalSignature: string,
  operations: ProposalOperation[],
) {
  if (typeof window === "undefined") return
  const record: ProposalRecoveryRecord = {
    version: 1,
    project_id: projectId,
    proposal_id: proposalId,
    original_signature: originalSignature,
    operations: operations.map(cleanProposalOperation),
  }
  proposalRecoveryMemory.set(key, record)
  try {
    window.sessionStorage.setItem(key, JSON.stringify(record))
  } catch {
    // The module-scoped record preserves same-tab route recovery.
  }
}

function clearProposalRecovery(key: string) {
  proposalRecoveryMemory.delete(key)
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // A storage failure must not block an explicit discard or successful save.
  }
}

function isStoredProposalOperation(value: unknown): value is ProposalOperation {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false
  const operation = value as Partial<ProposalOperation>
  return (
    typeof operation.id === "string"
    && typeof operation.type === "string"
    && Boolean(operation.data)
    && typeof operation.data === "object"
    && !Array.isArray(operation.data)
  )
}

function mergeProposalHistory(operation: ProposalOperation, stored?: ProposalOperation): ProposalOperation {
  if (!stored) return operation
  const result = { ...operation }
  if (stored.disposition !== undefined) result.disposition = stored.disposition
  if (Object.prototype.hasOwnProperty.call(stored, "before")) result.before = stored.before
  if (Object.prototype.hasOwnProperty.call(stored, "after")) result.after = stored.after
  return result
}

function operationChanged(operation: ProposalOperation, original?: ProposalOperation) {
  return !original || stableJson(cleanProposalOperation(operation)) !== stableJson(original)
}

function shortProposalId(value: string) {
  return value.slice(0, 8)
}

function displayString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined
}

function OperationReference({ item, source = false }: { item: EvidenceItem; source?: boolean }) {
  if (typeof item === "string") return <span className="operation-reference"><strong>{item}</strong></span>
  const summary = displayString(item.summary) ?? displayString(item.description)
  const path = displayString(item.path) ?? displayString(item.source_path)
  const anchor = displayString(item.anchor)
  const locator = source
    ? path ? path + (anchor ? "#" + anchor : "") : displayString(item.locator)
    : displayString(item.locator)
  const opaqueKey = displayString(item.opaque_key)
  const referenceId = displayString(item.monitor_reference_id) ?? displayString(item.id)
  const fingerprint = displayString(item.fingerprint) ?? displayString(item.content_hash)
  const details = [locator, opaqueKey && "key " + opaqueKey, referenceId && "reference " + referenceId, fingerprint && "fingerprint " + fingerprint].filter((value): value is string => Boolean(value))
  const kind = displayString(item.kind)
  const metadata = [source && details.length ? "source" : !source && kind ? humanize(kind) : undefined, ...details].filter((value): value is string => Boolean(value)).join(" · ")
  return (
    <span className="operation-reference">
      {summary && <strong>{summary}</strong>}
      {metadata && <small>{metadata}</small>}
      {!summary && !metadata && <small>{JSON.stringify(item) || "Unrecognized reference"}</small>}
    </span>
  )
}

function OperationDetails({ id, snapshot, operation, isEdited, onEdit }: { id: string; snapshot: ProjectSnapshot; operation: ProposalOperation; isEdited: boolean; onEdit?: () => void }) {
  const hasStoredDiff = Object.prototype.hasOwnProperty.call(operation, 'before') && Object.prototype.hasOwnProperty.call(operation, 'after')
  const before = hasStoredDiff ? operation.before ?? null : currentOperationData(snapshot, operation)
  const after = isEdited ? operation.data : hasStoredDiff ? operation.after ?? null : operation.data
  const beforeTitle = hasStoredDiff ? 'Before (proposal time)' : 'Current data (legacy fallback)'
  const afterTitle = isEdited ? 'Staged operation data (save draft to validate)' : hasStoredDiff ? 'After (proposed)' : 'Proposed change (legacy fallback)'
  return <div id={id} className="operation-details"><div>{!hasStoredDiff && <p className="muted-copy">Legacy proposal — no proposal-time snapshot was stored. Current data is live and may have changed.</p>}<h4>{beforeTitle}</h4>{before ? <pre>{JSON.stringify(before, null, 2)}</pre> : <p className="muted-copy">New entity — no monitor record existed at proposal time.</p>}<div className="section-heading compact"><h4>{afterTitle}</h4>{onEdit && <Button size="sm" variant="secondary" onClick={onEdit}><Edit3 size={13} />Edit</Button>}</div>{after ? <pre>{JSON.stringify(after, null, 2)}</pre> : <p className="muted-copy">This operation removes the entity.</p>}</div><div><h4>Evidence</h4>{operation.evidence?.length ? <ul>{operation.evidence.map((item, index) => <li key={index}><Sparkles size={13} /><OperationReference item={item} /></li>)}</ul> : <p className="muted-copy">No evidence attached.</p>}<h4>Source references</h4>{operation.source_references?.length ? <ul>{operation.source_references.map((item, index) => <li key={index}><FileSearch size={13} /><OperationReference item={item} source /></li>)}</ul> : <p className="muted-copy">No source references attached.</p>}</div></div>
}

function currentOperationData(snapshot: ProjectSnapshot, operation: ProposalOperation): Record<string, unknown> | null {
  if (operation.type.endsWith('.create')) return null
  const id = operation.entity_id ?? (typeof operation.data.id === 'string' ? operation.data.id : null)
  if (!id) return null
  const collection = operation.type.startsWith('pipeline.')
    ? snapshot.pipelines
    : operation.type.startsWith('task.')
      ? snapshot.tasks
      : operation.type.startsWith('edge.')
        ? snapshot.edges
        : operation.type.startsWith('journal.')
          ? snapshot.journals
          : operation.type.startsWith('artifact.')
            ? snapshot.artifacts
            : operation.type.startsWith('task_artifact.')
              ? snapshot.task_artifacts
              : []
  const entity = collection.find((item) => item.id === id)
  if (!entity) return null
  const source = entity as unknown as Record<string, unknown>
  const fields = new Set(['id', ...Object.keys(operation.data)])
  return Object.fromEntries([...fields].filter((field) => field in source).map((field) => [field, source[field]]))
}

function OperationEditor({ operation, onClose, onSave }: { operation: ProposalOperation | null; onClose: () => void; onSave: (operation: ProposalOperation) => void }) {
  const [data, setData] = useState('{}')
  const [rationale, setRationale] = useState('')
  const [confidence, setConfidence] = useState('')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    setData(JSON.stringify(operation?.data ?? {}, null, 2))
    setRationale(operation?.rationale ?? '')
    setConfidence(operation?.confidence == null ? '' : String(operation.confidence))
    setError(null)
  }, [operation])
  if (!operation) return null
  const save = () => {
    try {
      const parsed = JSON.parse(data)
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Operation data must be a JSON object.')
      const numericConfidence = Number(confidence)
      if (!rationale.trim()) throw new Error('Rationale is required.')
      if (!Number.isFinite(numericConfidence) || numericConfidence < 0 || numericConfidence > 1) throw new Error('Confidence must be between 0 and 1.')
      onSave({ ...operation, data: parsed as Record<string, unknown>, rationale: rationale.trim(), confidence: numericConfidence })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Invalid operation edit')
    }
  }
  return <Dialog open onClose={onClose} title={`Edit ${humanize(operation.type)}`} description="Edit operation data, rationale, and confidence. The complete staged draft is revalidated when saved; identity and dependency fields remain locked in this raw editor."><div className="form-stack"><Field label="Operation data (JSON)"><textarea className="code-textarea" rows={12} value={data} onChange={(event) => setData(event.target.value)} spellCheck={false} /></Field><Field label="Rationale"><textarea rows={2} value={rationale} onChange={(event) => setRationale(event.target.value)} /></Field><Field label="Confidence"><input type="number" min="0" max="1" step="0.01" value={confidence} onChange={(event) => setConfidence(event.target.value)} /></Field>{error && <Notice tone="danger">{error}</Notice>}<div className="dialog-actions"><Button variant="ghost" onClick={onClose}>Cancel</Button><Button onClick={save}>Save operation edit</Button></div></div></Dialog>
}

function selectionClosure(operations: ProposalOperation[], current: Set<string>, id: string) {
  const next = new Set(current)
  const byId = new Map(operations.map((op) => [op.id, op]))
  const add = (operationId: string) => {
    if (next.has(operationId)) return
    next.add(operationId)
    const op = byId.get(operationId)
    op?.prerequisite_operation_ids?.forEach(add)
    if (op?.atomic_group_id) operations.filter((item) => item.atomic_group_id === op.atomic_group_id).forEach((item) => add(item.id))
  }
  add(id)
  return next
}

function deselectionClosure(operations: ProposalOperation[], current: Set<string>, id: string) {
  const next = new Set(current)
  const remove = (operationId: string) => {
    if (!next.has(operationId)) return
    next.delete(operationId)
    operations.filter((op) => op.prerequisite_operation_ids?.includes(operationId)).forEach((op) => remove(op.id))
    const source = operations.find((op) => op.id === operationId)
    if (source?.atomic_group_id) operations.filter((op) => op.atomic_group_id === source.atomic_group_id).forEach((op) => remove(op.id))
  }
  remove(id)
  return next
}

function operationTitle(operation: ProposalOperation) {
  const data = operation.data
  return String(data.title || data.label || data.content || data.name || humanize(operation.type))
}

function operationTone(type: string) {
  if (type.includes('delete')) return 'red'
  if (type.includes('create') || type.includes('link')) return 'green'
  if (type.includes('update') || type.includes('move')) return 'blue'
  return 'neutral'
}

function toggleSet(previous: Set<string>, id: string) {
  const next = new Set(previous)
  next.has(id) ? next.delete(id) : next.add(id)
  return next
}

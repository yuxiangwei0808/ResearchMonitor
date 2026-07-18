import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bot, Check, CheckCircle2, ChevronDown, ChevronRight, Clipboard, Edit3, FileSearch, GitMerge, ShieldCheck, Sparkles, X } from 'lucide-react'
import type { AgentPrompt, EvidenceItem, ProjectSnapshot, Proposal, ProposalOperation } from "../types"
import { GUIDED_WORKFLOW_MODES } from "../types"
import { api } from '../lib/api'
import { formatDate, humanize } from '../lib/format'
import { createTaskLabeler } from '../lib/taskLabels'
import { Badge, Button, Dialog, EmptyState, ErrorState, Field, Notice, Spinner } from "../components/ui"
import { ProposedOutline } from "./proposals/ProposedOutline"
import { readGuidedIntentDraft, type GuidedRequestSeed } from "../components/AskCodexDialog"

const OPEN_PROPOSAL_PAGE_SIZE = 100
const HISTORY_PAGE_SIZE = 20

export function ProposalsView({ snapshot, onAskCodex }: { snapshot: ProjectSnapshot; onAskCodex?: (seed: GuidedRequestSeed) => void }) {
  const [modeFilter, setModeFilter] = useState('all')
  const [scopeFilter, setScopeFilter] = useState('all')
  const workflowMode = modeFilter === 'all' ? undefined : modeFilter
  const scopeType = scopeFilter === 'all' ? undefined : scopeFilter
  const draftsQuery = useInfiniteQuery({
    queryKey: ['proposals', snapshot.project.id, 'summary', 'open', modeFilter, scopeFilter],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api.getProposalPage(snapshot.project.id, {
      cursor: pageParam ?? undefined,
      status: 'open',
      workflowMode,
      scopeType,
      limit: OPEN_PROPOSAL_PAGE_SIZE,
      summary: true,
    }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })
  const historyQuery = useInfiniteQuery({
    queryKey: ['proposals', snapshot.project.id, 'summary', 'closed', modeFilter, scopeFilter],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => api.getProposalPage(snapshot.project.id, {
      cursor: pageParam ?? undefined,
      status: 'closed',
      workflowMode,
      scopeType,
      limit: HISTORY_PAGE_SIZE,
      summary: true,
    }),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  })

  useEffect(() => {
    if (draftsQuery.hasNextPage && !draftsQuery.isFetchingNextPage) {
      void draftsQuery.fetchNextPage()
    }
  }, [draftsQuery.data?.pages.length, draftsQuery.fetchNextPage, draftsQuery.hasNextPage, draftsQuery.isFetchingNextPage])

  if (draftsQuery.isPending || historyQuery.isPending) {
    return <div className="content-loading"><Spinner label="Loading Codex proposal summaries…" /></div>
  }
  const queryError = draftsQuery.error || historyQuery.error
  if (queryError) {
    return <ErrorState error={queryError} retry={() => {
      void draftsQuery.refetch()
      void historyQuery.refetch()
    }} />
  }
  const drafts = flattenProposalPages(draftsQuery.data?.pages)
  const closed = flattenProposalPages(historyQuery.data?.pages)
  const draftTotal = draftsQuery.data?.pages[0]?.total ?? drafts.length
  const closedTotal = historyQuery.data?.pages[0]?.total ?? closed.length
  const hasAnyProposal = draftTotal + closedTotal > 0
  return (
    <div className="view-page proposals-view">
      <header className="view-toolbar"><div><h2>Codex proposals</h2><p>Every agent-generated change stays a draft until you inspect and accept it here.</p></div><div className="button-row"><label className="compact-filter"><span>Workflow</span><select value={modeFilter} onChange={(event) => setModeFilter(event.target.value)}><option value="all">All workflows</option>{GUIDED_WORKFLOW_MODES.map((mode) => <option key={mode} value={mode}>{humanize(mode)}</option>)}<option value="legacy_custom">Legacy custom</option></select></label><label className="compact-filter"><span>Scope</span><select value={scopeFilter} onChange={(event) => setScopeFilter(event.target.value)}><option value="all">All scopes</option><option value="project">Project</option><option value="pipeline">Pipeline</option><option value="task">Task subtree</option></select></label><Badge tone="green"><ShieldCheck size={13} />Review required</Badge></div></header>
      <Notice>Codex can inspect permitted project content and propose monitor updates. It cannot enroll folders, approve roots, edit research files, or apply its own changes.</Notice>
      {!hasAnyProposal ? <EmptyState icon={<FileSearch size={28} />} title={modeFilter === 'all' && scopeFilter === 'all' ? 'No proposals yet' : 'No proposals match these filters'} description={modeFilter === 'all' && scopeFilter === 'all' ? 'Copy the Codex prompt from the project header, run it from your project folder, and the resulting draft will appear here.' : 'Choose a different workflow or scope filter to see other proposal results.'} /> : <div className="proposal-sections">
        <section><div className="section-heading"><div><h3>Awaiting review</h3><p>{draftTotal} proposal{draftTotal === 1 ? '' : 's'} need your decision.</p></div></div>{drafts.length ? <div className="proposal-list">{drafts.map((proposal) => <ProposalCard key={proposal.id} snapshot={snapshot} proposal={proposal} onAskCodex={onAskCodex} />)}</div> : <div className="mini-empty"><CheckCircle2 size={20} /><p>All matching proposals have been reviewed.</p></div>}{draftsQuery.isFetchingNextPage && <div className="proposal-page-status" role="status"><Spinner label={`Loading remaining open drafts (${drafts.length} of ${draftTotal})…`} /></div>}</section>
        {closedTotal > 0 && <section><div className="section-heading"><div><h3>Review history</h3><p>Applied, rejected, no-change, conflicted, or superseded results retain their evidence and proposal-time diffs.</p></div></div><div className="proposal-list">{closed.map((proposal) => <ProposalCard key={proposal.id} snapshot={snapshot} proposal={proposal} onAskCodex={onAskCodex} />)}</div>{historyQuery.hasNextPage && <div className="load-older"><Button variant="secondary" disabled={historyQuery.isFetchingNextPage} onClick={() => void historyQuery.fetchNextPage()}>{historyQuery.isFetchingNextPage ? 'Loading older results…' : 'Load 20 older results'}</Button><small>{closed.length} of {closedTotal} history results loaded</small></div>}</section>}
      </div>}
    </div>
  )
}

function flattenProposalPages(pages: { proposals: Proposal[] }[] | undefined): Proposal[] {
  const seen = new Set<string>()
  return (pages ?? []).flatMap((page) => page.proposals).filter((proposal) => {
    if (seen.has(proposal.id)) return false
    seen.add(proposal.id)
    return true
  })
}

function ProposalCard({ snapshot, proposal, onAskCodex }: { snapshot: ProjectSnapshot; proposal: Proposal; onAskCodex?: (seed: GuidedRequestSeed) => void }) {
  const hasInlineDetail = proposal.detail_loaded === true || (
    proposal.detail_loaded !== false
    && (proposal.operations.length > 0 || Boolean(proposal.top_level_evidence?.length || proposal.source_references?.length))
  )
  const [detailsOpen, setDetailsOpen] = useState(hasInlineDetail)
  const detailQuery = useQuery({
    queryKey: ['proposal', proposal.id],
    queryFn: () => api.getProposal(snapshot.project.id, proposal.id),
    enabled: detailsOpen && !hasInlineDetail,
    staleTime: 30_000,
  })
  const loaded = hasInlineDetail ? proposal : detailQuery.data
  const detailedProposal = useMemo(() => loaded ? {
    // The list summary is refreshed by outbox events and therefore owns the
    // proposal lifecycle. A cached detail response may predate that refresh,
    // so it must contribute only the fields omitted from summary mode.
    ...loaded,
    ...proposal,
    operations: loaded.operations,
    evidence: loaded.evidence,
    top_level_evidence: loaded.top_level_evidence,
    source_references: loaded.source_references,
    detail_loaded: true,
  } : undefined, [loaded, proposal])

  if (!detailsOpen || !detailedProposal) {
    return <ProposalSummaryCard
      snapshot={snapshot}
      proposal={proposal}
      expanded={detailsOpen}
      loading={detailQuery.isFetching}
      error={detailQuery.error}
      onToggle={() => setDetailsOpen((current) => !current)}
      onRetry={() => void detailQuery.refetch()}
    />
  }
  return <ProposalDetailCard snapshot={snapshot} proposal={detailedProposal} onAskCodex={onAskCodex} onCollapse={() => setDetailsOpen(false)} />
}

function ProposalSummaryCard({ snapshot, proposal, expanded, loading, error, onToggle, onRetry }: {
  snapshot: ProjectSnapshot
  proposal: Proposal
  expanded: boolean
  loading: boolean
  error: Error | null
  onToggle: () => void
  onRetry: () => void
}) {
  const reviewable = proposal.status === 'draft'
  const scopeLabel = proposalScopeLabel(snapshot, proposal)
  const operationCount = proposal.operation_count ?? proposal.operations.length
  const highRiskCount = proposal.risk_counts?.high ?? 0
  const basisCounts = proposal.basis_counts ?? {}
  const detailId = `proposal-detail-${proposal.id}`
  const actionLabel = expanded ? 'Collapse details' : reviewable ? 'Review proposal' : proposal.result_kind === 'no_changes' ? 'View report' : 'View details'
  return <article className="proposal-card proposal-summary-card">
    <header className="proposal-header">
      <span className="proposal-agent"><Bot size={20} /></span>
      <div>
        <div className="proposal-title-line"><h3>{proposal.summary}</h3>{!reviewable && <Badge tone={proposal.status === 'applied' ? 'green' : proposal.status === 'conflict' ? 'red' : 'muted'}>{humanize(proposal.status)}</Badge>}</div>
        <div className="proposal-classification">
          <Badge tone={proposal.workflow_mode === 'legacy_custom' || !proposal.workflow_mode ? 'amber' : 'blue'}>{humanize(proposal.workflow_mode ?? 'legacy_custom')}</Badge>
          <Badge tone="neutral">{scopeLabel}</Badge>
          {proposal.result_kind === 'no_changes' && <Badge tone="green">No changes</Badge>}
          {basisCounts.source_evidence ? <Badge tone="neutral">Source evidence · {basisCounts.source_evidence}</Badge> : null}
          {basisCounts.user_instruction ? <Badge tone="neutral">User instruction · {basisCounts.user_instruction}</Badge> : null}
          {basisCounts.inference ? <Badge tone="amber">Inferred · {basisCounts.inference}</Badge> : null}
          {highRiskCount > 0 && <Badge tone="red">High risk · {highRiskCount}</Badge>}
        </div>
        <p>{proposal.rationale || 'Codex submitted a structured monitor result for review.'}</p>
        <small>{proposal.actor_label || 'Codex'} · {formatDate(proposal.created_at, true)} · {operationCount} operation{operationCount === 1 ? '' : 's'}{proposal.intent_id && <> · intent <code>{shortProposalId(proposal.intent_id)}</code></>}{proposal.regenerates_proposal_id && <> · regenerated from <code>{shortProposalId(proposal.regenerates_proposal_id)}</code></>}</small>
      </div>
    </header>
    {proposal.result_kind === 'no_changes' && <Notice tone="success"><strong>{proposal.no_change_reason ? humanize(proposal.no_change_reason) : 'No monitor changes proposed'}</strong><p>{formatScanSummary(proposal.scan_summary)}</p></Notice>}
    {loading && <div className="proposal-page-status" role="status"><Spinner label="Loading complete proposal details…" /></div>}
    {error && <Notice tone="danger"><div><strong>Unable to load proposal details.</strong><p>{error.message}</p><Button size="sm" variant="secondary" onClick={onRetry}>Try again</Button></div></Notice>}
    <footer className="proposal-actions proposal-summary-actions">
      <span>{proposal.evidence_count ? `${proposal.evidence_count} top-level evidence item${proposal.evidence_count === 1 ? '' : 's'}` : 'Details load only when opened'}</span>
      <Button variant="secondary" aria-expanded={expanded} aria-controls={detailId} onClick={onToggle}>{expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}{actionLabel}</Button>
    </footer>
  </article>
}

function ProposalDetailCard({ snapshot, proposal, onAskCodex, onCollapse }: { snapshot: ProjectSnapshot; proposal: Proposal; onAskCodex?: (seed: GuidedRequestSeed) => void; onCollapse: () => void }) {
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
  const initialExplicitSelection = defaultProposalSelection(proposal, reviewable)
  const [explicitSelected, setExplicitSelected] = useState<Set<string>>(() => new Set(initialExplicitSelection))
  const [selected, setSelected] = useState<Set<string>>(() => selectionForExplicit(originalOperations, initialExplicitSelection))
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<ProposalOperation | null>(null)
  const [activeTab, setActiveTab] = useState<"outline" | "audit">(reviewable && proposal.base_semantic_revision === snapshot.project.semantic_revision ? "outline" : "audit")
  const [selectionMessage, setSelectionMessage] = useState("")
  const [copied, setCopied] = useState(false)
  const [stagedJsonCopied, setStagedJsonCopied] = useState(false)
  const [copyError, setCopyError] = useState<string | null>(null)
  const [applyConfirmation, setApplyConfirmation] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [rejectionReason, setRejectionReason] = useState('')
  const revisionRequestIds = useRef<Map<string, string>>(new Map())
  const applyRequestIds = useRef<Map<string, string>>(new Map())
  const rejectRequestIds = useRef<Map<string, string>>(new Map())

  useEffect(() => {
    setStagedOperations(
      readProposalRecovery(recoveryKey, snapshot.project.id, proposal.id, originalSignature)
        ?? originalOperations.map(cleanProposalOperation),
    )
    const nextExplicit = defaultProposalSelection(proposal, reviewable)
    setExplicitSelected(nextExplicit)
    setSelected(selectionForExplicit(originalOperations, nextExplicit))
    setExpanded(new Set())
    setEditing(null)
    setActiveTab(reviewable && proposal.base_semantic_revision === snapshot.project.semantic_revision ? "outline" : "audit")
    setSelectionMessage("")
    setApplyConfirmation(false)
    setRejectOpen(false)
    setRejectionReason('')
    revisionRequestIds.current.clear()
    applyRequestIds.current.clear()
    rejectRequestIds.current.clear()
  }, [proposal.id, proposal.status, originalSignature, recoveryKey, snapshot.project.id])

  const stale = proposal.base_semantic_revision !== snapshot.project.semantic_revision
  const guidedConflict = proposal.status === "conflict" && Boolean(proposal.intent_id && guidedSeedForProposal(proposal))
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
    client.invalidateQueries({ queryKey: ["proposal", proposal.id] }),
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
    onSuccess: async () => {
      setApplyConfirmation(false)
      await invalidateProposalData()
    },
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
        stagedOperations.map(proposalOperationForTransport),
        requestId,
      )
    },
    onSuccess: async () => {
      clearProposalRecovery(recoveryKey)
      setStagedOperations(originalOperations.map(cleanProposalOperation))
      setExplicitSelected(new Set())
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
    onSuccess: async () => {
      setRejectOpen(false)
      setRejectionReason('')
      await invalidateProposalData()
    },
  })

  const pendingIds = reviewable ? stagedOperations.map((operation) => operation.id) : []
  const updateStagedOperations = (operations: ProposalOperation[]) => {
    const cleaned = operations.map(cleanProposalOperation)
    const retainedIds = new Set(cleaned.map((operation) => operation.id))
    setStagedOperations(cleaned)
    setSelected((current) => new Set([...current].filter((id) => retainedIds.has(id))))
    setExplicitSelected((current) => new Set([...current].filter((id) => retainedIds.has(id))))
    setEditing((current) => current && retainedIds.has(current.id)
      ? cleaned.find((operation) => operation.id === current.id) ?? null
      : null)
  }
  const discardEdits = () => {
    clearProposalRecovery(recoveryKey)
    setStagedOperations(originalOperations.map(cleanProposalOperation))
    setExplicitSelected(new Set())
    setSelected(new Set())
    setEditing(null)
    setSelectionMessage("Discarded staged edits; approval selection was reset.")
  }
  const toggleOperation = (operationId: string, checked: boolean) => {
    if (stale || dirty) return
    const operation = stagedOperations.find((item) => item.id === operationId)
    if (!operation) return
    const nextExplicit = new Set(explicitSelected)
    let nextSelected: Set<string>
    if (checked) {
      nextExplicit.add(operationId)
      nextSelected = selectionForExplicit(stagedOperations, nextExplicit)
    } else {
      const withoutOperation = deselectionClosure(stagedOperations, selected, operationId)
      nextExplicit.delete(operationId)
      ;[...nextExplicit].forEach((id) => { if (!withoutOperation.has(id)) nextExplicit.delete(id) })
      nextSelected = selectionForExplicit(stagedOperations, nextExplicit)
    }
    const affected = Math.max(0, Math.abs(nextSelected.size - selected.size) - 1)
    setSelectionMessage(affected
      ? `${checked ? "Selected" : "Deselected"} ${operationTitle(operation)} and ${affected} required or dependent operation${affected === 1 ? "" : "s"}.`
      : `${checked ? "Selected" : "Deselected"} ${operationTitle(operation)}.`)
    setExplicitSelected(nextExplicit)
    setSelected(nextSelected)
  }
  const allSelected = pendingIds.length > 0 && pendingIds.every((id) => selected.has(id))
  const auditOperations = stagedOperations.map((operation) => mergeProposalHistory(operation, storedById.get(operation.id)))
  const operationGroups = groupOperations(auditOperations)
  const confidence = stagedOperations.filter((operation) => operation.confidence != null)
  const averageConfidence = confidence.length
    ? confidence.reduce((sum, operation) => sum + Number(operation.confidence), 0) / confidence.length
    : null
  const mutationError = apply.error || saveRevision.error || reject.error
  const selectedOperations = stagedOperations.filter((operation) => selected.has(operation.id))
  const explicitSelectedOperations = selectedOperations.filter((operation) => explicitSelected.has(operation.id))
  const automaticallySelectedOperations = selectedOperations.filter((operation) => !explicitSelected.has(operation.id))
  const automaticClosureCounts = automaticClosureReasonCounts(stagedOperations, selected, explicitSelected)
  const selectedTypeCounts = operationTypeCounts(selectedOperations)
  const inferredSelected = selectedOperations.filter((operation) => operation.basis === 'inference').length
  const highRiskSelected = selectedOperations.filter(isHighRiskOperation).length
  const scopeLabel = proposalScopeLabel(snapshot, proposal)
  const basisCounts = stagedOperations.reduce<Record<string, number>>((counts, operation) => {
    if (operation.basis) counts[operation.basis] = (counts[operation.basis] ?? 0) + 1
    return counts
  }, {})
  const highRiskCount = stagedOperations.filter(isHighRiskOperation).length

  const copyRegenerationPrompt = async () => {
    let storedIntent: AgentPrompt | undefined
    if (proposal.intent_id) {
      try {
        storedIntent = await api.getAgentPrompt(snapshot.project.id, proposal.intent_id)
      } catch (error) {
        setCopyError(error instanceof Error ? error.message : "Stored guided request is unavailable.")
        return
      }
    }
    const guidedSeed = guidedSeedForProposal(proposal, storedIntent)
    if (onAskCodex && guidedSeed) {
      onAskCodex({ ...guidedSeed, regenerateProposalId: proposal.id })
      return
    }
    const regenerationReason = proposal.status === "conflict" ? "the prior proposal conflicted during validation or application" : "the existing draft is stale"
    const text = `Use $research-monitor for project ${snapshot.project.id} at ${snapshot.project.root_path}. Regenerate proposal ${proposal.id} against current semantic revision ${snapshot.project.semantic_revision}; ${regenerationReason}. Inspect permitted project content read-only, submit a new reviewable proposal, and do not apply it.`
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
    <article className="proposal-card" id={`proposal-detail-${proposal.id}`}>
      <header className="proposal-header">
        <span className="proposal-agent"><Bot size={20} /></span>
        <div>
          <div className="proposal-title-line">
            <h3>{proposal.summary}</h3>
            {!reviewable && <Badge tone={proposal.status === "applied" ? "green" : proposal.status === "conflict" ? "red" : "muted"}>{humanize(proposal.status)}</Badge>}
          </div>
          <div className="proposal-classification">
            <Badge tone={proposal.workflow_mode === 'legacy_custom' || !proposal.workflow_mode ? 'amber' : 'blue'}>{humanize(proposal.workflow_mode ?? 'legacy_custom')}</Badge>
            <Badge tone="neutral">{scopeLabel}</Badge>
            {proposal.result_kind === 'no_changes' && <Badge tone="green">No changes</Badge>}
            {basisCounts.source_evidence ? <Badge tone="neutral">Source evidence · {basisCounts.source_evidence}</Badge> : null}
            {basisCounts.user_instruction ? <Badge tone="neutral">User instruction · {basisCounts.user_instruction}</Badge> : null}
            {basisCounts.inference ? <Badge tone="amber">Inferred · {basisCounts.inference}</Badge> : null}
            {highRiskCount > 0 && <Badge tone="red">High risk · {highRiskCount}</Badge>}
            {proposal.proposal_contract_version && <span>Contract v{proposal.proposal_contract_version}</span>}
          </div>
          <p>{proposal.rationale || "Codex submitted a structured set of monitor updates for review."}</p>
          <small>
            {proposal.actor_label || "Codex"} · {formatDate(proposal.created_at, true)} · base revision {proposal.base_semantic_revision}
            {proposal.intent_id && <> · intent <code>{shortProposalId(proposal.intent_id)}</code></>}
            {proposal.supersedes_proposal_id && <> · revised from <code>{shortProposalId(proposal.supersedes_proposal_id)}</code></>}
            {proposal.regenerates_proposal_id && <> · regenerated from <code>{shortProposalId(proposal.regenerates_proposal_id)}</code></>}
          </small>
        </div>
        <div className="proposal-header-actions">
          {averageConfidence != null && <div className="confidence"><span>{Math.round(averageConfidence * 100)}%</span><small>avg. confidence</small></div>}
          <Button type="button" size="sm" variant="ghost" onClick={onCollapse}><ChevronDown size={14} />Collapse</Button>
        </div>
      </header>

      {(proposal.workflow_mode === 'legacy_custom' || !proposal.workflow_mode) && <Notice tone="warning">
        <strong>Legacy custom proposal.</strong>
        <p>This draft is not bound to a typed guided intent. Every operation starts unselected; review its scope and evidence carefully.</p>
      </Notice>}
      {proposal.result_kind === 'no_changes' && <Notice tone="success">
        <strong>{proposal.no_change_reason ? humanize(proposal.no_change_reason) : 'No monitor changes proposed'}</strong>
        <p>{formatScanSummary(proposal.scan_summary)}</p>
      </Notice>}
      {proposal.result_kind === 'no_changes' && <ProposalEvidenceBlock proposal={proposal} />}
      {proposal.supersedes_proposal_id && <Notice>
        <strong>Human-reviewed replacement draft.</strong>
        <p>This proposal supersedes <code>{proposal.supersedes_proposal_id}</code>. The server revalidated the complete draft and assigned canonical operation IDs.</p>
      </Notice>}
      {proposal.regenerates_proposal_id && <Notice><strong>Regenerated from <code>{proposal.regenerates_proposal_id}</code>.</strong><p>This result came from a fresh guided intent; the earlier proposal remains unchanged in history.</p></Notice>}
      {proposal.status === "superseded" && <Notice>
        <strong>This draft was superseded without changing its recorded operations.</strong>
        <p>{proposal.superseded_by_proposal_id
          ? <>Continue review in replacement <code>{proposal.superseded_by_proposal_id}</code>.</>
          : "A newer reviewed draft replaced this version."}</p>
      </Notice>}
      {reviewable && stale && <Notice tone="warning"><div><strong>This proposal is stale and cannot be applied.</strong><p>Regenerate it against semantic revision {snapshot.project.semantic_revision}, then review the new draft.</p><Button type="button" size="sm" variant="secondary" onClick={copyRegenerationPrompt}><Clipboard size={13} />{onAskCodex && guidedSeedForProposal(proposal) ? "Regenerate guided request" : copied ? "Prompt copied" : "Copy regeneration prompt"}</Button>{copyError && <small>{copyError}</small>}</div></Notice>}
      {guidedConflict && <Notice tone="warning"><div><strong>This guided proposal conflicted and was not applied.</strong><p>Regenerate the original mode and scope against the current monitor state, then review the replacement draft.</p><Button type="button" size="sm" variant="secondary" onClick={copyRegenerationPrompt}><Clipboard size={13} />Regenerate guided request</Button>{copyError && <small>{copyError}</small>}</div></Notice>}
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
          {reviewable && <div className="proposal-select-all"><label><input type="checkbox" disabled={stale || dirty || !pendingIds.length} checked={allSelected} onChange={(event) => { const next = event.target.checked ? new Set(pendingIds) : new Set<string>(); setExplicitSelected(next); setSelected(next); setSelectionMessage(event.target.checked ? `Selected all ${pendingIds.length} staged operations.` : "Deselected all staged operations.") }} />Select all staged operations</label><span>{selected.size} of {pendingIds.length} selected{highRiskSelected ? ` · ${highRiskSelected} high risk` : ''}</span></div>}
          {reviewable && <p className="selection-message" aria-live="polite">{selectionMessage}</p>}
          <div className="operation-list">{operationGroups.map(([group, operations]) => <section className="operation-group" key={group}>
            <h4>{group}<span>{operations.length}</span></h4>
            {operations.map((operation) => {
              const isExpanded = expanded.has(operation.id)
              const changed = operationChanged(operation, originalById.get(operation.id))
              const detailsId = `proposal-operation-${operation.id}`
              return <div className={`operation-row ${selected.has(operation.id) ? "selected" : ""}`} key={operation.id}>
                <div className="operation-summary">
                  {reviewable && <input type="checkbox" disabled={stale || dirty} checked={selected.has(operation.id)} onChange={(event) => toggleOperation(operation.id, event.target.checked)} aria-label={`Select ${operationTitle(operation)} (${humanize(operation.type)})`} />}
                  <span className="operation-icon"><GitMerge size={16} /></span>
                  <button aria-expanded={isExpanded} aria-controls={detailsId} onClick={() => setExpanded((previous) => toggleSet(previous, operation.id))}>
                    <strong>{operationTitle(operation)}{changed && <Badge tone="amber">Staged edit</Badge>}{operation.basis === 'inference' && <Badge tone="amber">Inferred</Badge>}{isHighRiskOperation(operation) && <Badge tone="red">High risk</Badge>}</strong>
                    <small><Badge tone={operationTone(operation.type)}>{humanize(operation.type)}</Badge>{operation.basis && <Badge tone="neutral">{humanize(operation.basis)}</Badge>}{operation.rationale || "No rationale supplied"}{operation.prerequisite_operation_ids?.length ? ` · ${operation.prerequisite_operation_ids.length} prerequisite` : ""}</small>
                  </button>
                  <span className="operation-meta">{operation.confidence != null && `${Math.round(operation.confidence * 100)}%`}{isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
                </div>
                {isExpanded && <OperationDetails id={detailsId} snapshot={snapshot} operation={operation} isEdited={changed} onEdit={reviewable && !stale && !recoveryConflict ? () => setEditing(cleanProposalOperation(operation)) : undefined} />}
              </div>
            })}
          </section>)}</div>
        </div>
      )}

      {mutationError && <Notice tone="danger">{mutationError.message}</Notice>}
      {showReviewWorkspace && <footer className="proposal-actions">
        <Button variant="danger" disabled={!reviewable || dirty || reject.isPending || saveRevision.isPending} onClick={() => setRejectOpen(true)}><X size={16} />Reject proposal</Button>
        {recoveryConflict && <Button type="button" variant="secondary" onClick={copyStagedJson}><Clipboard size={16} />{stagedJsonCopied ? "Staged JSON copied" : "Copy staged JSON"}</Button>}
        {hasStagedChanges && !emptyStagedDraft && <Button type="button" variant="ghost" disabled={saveRevision.isPending} onClick={discardEdits}>{recoveryConflict ? "Discard recovered edits" : "Discard edits"}</Button>}
        {hasStagedChanges && <Button type="button" variant="secondary" disabled={!reviewable || stale || emptyStagedDraft || saveRevision.isPending} onClick={() => { if (!emptyStagedDraft) saveRevision.mutate() }}><ShieldCheck size={16} />{saveRevision.isPending ? "Saving reviewed draft…" : emptyStagedDraft ? "No operations to save" : "Save reviewed draft"}</Button>}
        <Button disabled={!reviewable || recoveryConflict || stale || dirty || !selected.size || apply.isPending || saveRevision.isPending} onClick={() => setApplyConfirmation(true)}><Check size={16} />{apply.isPending ? "Applying…" : applyLabel}</Button>
      </footer>}
      {reviewable && <OperationEditor operation={editing} onClose={() => setEditing(null)} onSave={(operation) => {
        setStagedOperations((current) => current.map((item) => item.id === operation.id ? cleanProposalOperation(operation) : item))
        setEditing(null)
      }} />}
      <Dialog open={rejectOpen} onClose={() => setRejectOpen(false)} title="Reject proposal" description="The proposal and its evidence remain in review history. No monitor changes are applied.">
        <div className="form-stack">
          <Field label="Reason (optional)"><textarea rows={4} value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} placeholder="Record why this proposal should not be applied…" /></Field>
          {reject.error && <Notice tone="danger">{reject.error.message}</Notice>}
          <div className="dialog-actions"><Button variant="ghost" onClick={() => setRejectOpen(false)}>Cancel</Button><Button variant="danger" disabled={reject.isPending} onClick={() => reject.mutate(rejectionReason.trim() || undefined)}>{reject.isPending ? 'Rejecting…' : 'Reject proposal'}</Button></div>
        </div>
      </Dialog>
      <Dialog open={applyConfirmation} onClose={() => setApplyConfirmation(false)} title="Apply selected proposal changes" description="The server will revalidate and apply this exact selection atomically.">
        <div className="form-stack">
          <dl className="apply-summary">
            <div><dt>Selected operations</dt><dd>{selectedOperations.length}</dd></div>
            <div><dt>Explicit selections</dt><dd>{explicitSelectedOperations.length}</dd></div>
            <div><dt>Automatically included</dt><dd>{automaticallySelectedOperations.length}{automaticallySelectedOperations.length ? <small>{` · ${automaticClosureCounts.prerequisite} prerequisite closure · ${automaticClosureCounts.atomic} atomic-group closure`}</small> : null}</dd></div>
            <div><dt>Selected by operation type</dt><dd>{selectedTypeCounts.map(([type, count]) => `${humanize(type)}: ${count}`).join(' · ')}</dd></div>
            <div><dt>Inferred operations</dt><dd>{inferredSelected}</dd></div>
            <div><dt>High-risk operations</dt><dd>{highRiskSelected}</dd></div>
            <div><dt>Affected scope</dt><dd>{scopeLabel}</dd></div>
          </dl>
          {(inferredSelected > 0 || highRiskSelected > 0) && <Notice tone="warning">This selection contains {inferredSelected > 0 ? 'inferred' : ''}{inferredSelected > 0 && highRiskSelected > 0 ? ' and ' : ''}{highRiskSelected > 0 ? 'high-risk' : ''} operations. Confirm their evidence before applying.</Notice>}
          <Notice>Application is atomic. Undo remains available only until a later edit changes one of the affected entities.</Notice>
          {apply.error && <Notice tone="danger">{apply.error.message}</Notice>}
          <div className="dialog-actions"><Button variant="ghost" onClick={() => setApplyConfirmation(false)}>Cancel</Button><Button disabled={apply.isPending} onClick={() => apply.mutate()}><Check size={16} />{apply.isPending ? 'Applying atomically…' : `Apply ${selectedOperations.length} selected`}</Button></div>
        </div>
      </Dialog>
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

function proposalOperationForTransport(operation: ProposalOperation): ProposalOperation {
  const {
    disposition: _disposition,
    before: _before,
    after: _after,
    risk: _risk,
    default_selected: _defaultSelected,
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

function defaultProposalSelection(proposal: Proposal, reviewable: boolean) {
  if (!reviewable || proposal.proposal_contract_version !== '2') return new Set<string>()
  return new Set(proposal.operations
    .filter((operation) => operation.default_selected === true && operation.disposition !== 'applied' && operation.disposition !== 'rejected' && operation.disposition !== 'conflict')
    .map((operation) => operation.id))
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
  return stableJson(operations.map(proposalOperationForTransport).sort((left, right) => left.id.localeCompare(right.id)))
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
  if (result.risk === undefined) result.risk = stored.risk
  if (result.default_selected === undefined) result.default_selected = stored.default_selected
  return result
}

function operationChanged(operation: ProposalOperation, original?: ProposalOperation) {
  return !original || stableJson(proposalOperationForTransport(operation)) !== stableJson(proposalOperationForTransport(original))
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

function ProposalEvidenceBlock({ proposal }: { proposal: Proposal }) {
  const evidence = proposal.top_level_evidence ?? proposal.evidence ?? []
  const sourceReferences = proposal.source_references ?? []
  return (
    <section className="operation-details" aria-label="No-change evidence">
      <div><h4>Top-level evidence</h4>{evidence.length ? <ul>{evidence.map((item, index) => <li key={index}><Sparkles size={13} /><OperationReference item={item} /></li>)}</ul> : <p className="muted-copy">No top-level evidence was attached.</p>}</div>
      <div><h4>Source references</h4>{sourceReferences.length ? <ul>{sourceReferences.map((item, index) => <li key={index}><FileSearch size={13} /><OperationReference item={item} source /></li>)}</ul> : <p className="muted-copy">No source references were attached.</p>}</div>
    </section>
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


function selectionForExplicit(operations: ProposalOperation[], explicit: Set<string>) {
  let selected = new Set<string>()
  ;[...explicit].sort().forEach((id) => { selected = selectionClosure(operations, selected, id) })
  return selected
}

function automaticClosureReasonCounts(operations: ProposalOperation[], selected: Set<string>, explicit: Set<string>) {
  const automaticIds = new Set([...selected].filter((id) => !explicit.has(id)))
  const selectedOperations = operations.filter((operation) => selected.has(operation.id))
  let prerequisite = 0
  let atomic = 0
  automaticIds.forEach((id) => {
    if (selectedOperations.some((operation) => operation.prerequisite_operation_ids?.includes(id))) prerequisite += 1
    const group = operations.find((operation) => operation.id === id)?.atomic_group_id
    if (group && selectedOperations.some((operation) => operation.id !== id && operation.atomic_group_id === group)) atomic += 1
  })
  return { prerequisite, atomic }
}

function operationTypeCounts(operations: ProposalOperation[]): Array<[string, number]> {
  const counts = operations.reduce<Record<string, number>>((result, operation) => ({ ...result, [operation.type]: (result[operation.type] ?? 0) + 1 }), {})
  return Object.entries(counts).sort(([left], [right]) => left.localeCompare(right))
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

function groupOperations(operations: ProposalOperation[]): Array<[string, ProposalOperation[]]> {
  const groups = new Map<string, ProposalOperation[]>([
    ['Planning', []],
    ['Recorded progress', []],
    ['Artifacts', []],
    ['Other / legacy', []],
  ])
  operations.forEach((operation) => {
    const group = operation.type.startsWith('artifact.') || operation.type.startsWith('task_artifact.')
      ? 'Artifacts'
      : operation.type.startsWith('journal.') || (
          operation.type.startsWith('task.')
          && ['status', 'outcome', 'completion_summary', 'blocker_reason', 'completed_at'].some((key) => Object.prototype.hasOwnProperty.call(operation.data, key))
        )
        ? 'Recorded progress'
        : operation.type.startsWith('pipeline.') || operation.type.startsWith('task.') || operation.type.startsWith('edge.')
          ? 'Planning'
          : 'Other / legacy'
    groups.get(group)?.push(operation)
  })
  return [...groups.entries()].filter(([, items]) => items.length)
}

export function isHighRiskOperation(operation: ProposalOperation) {
  if (operation.risk === 'high') return true
  if (new Set(['pipeline.archive', 'pipeline.update', 'task.move', 'edge.update', 'journal.update', 'artifact.update']).has(operation.type)) return true
  const status = operation.data.status
  const highRiskTaskFields = new Set([
    'pipeline_id',
    'parent_id',
    'position',
    'child_flow_mode',
    'outcome',
    'completion_summary',
    'completion_source',
    'completion_actor',
    'completion_provenance',
    'completion_override_reason',
    'completed_at',
  ])
  if (operation.type === 'task.create') {
    const outcome = String(operation.data.outcome ?? '')
    const completionFields = [
      'completion_summary',
      'completion_source',
      'completion_actor',
      'completion_provenance',
      'completion_override_reason',
      'completed_at',
    ]
    return status === 'done' || status === 'dropped'
      || (outcome !== '' && outcome !== 'not_applicable')
      || completionFields.some((key) => operation.data[key] !== undefined && operation.data[key] !== null && operation.data[key] !== '')
  }
  if (operation.type === 'task.update') {
    return status === 'done' || status === 'dropped'
      || Object.keys(operation.data).some((key) => highRiskTaskFields.has(key))
  }
  if (operation.type === 'edge.create') {
    return Boolean(operation.data.disabled)
      || String(operation.data.waiver_reason ?? '').trim().length > 0
  }
  return false
}

function formatScanSummary(summary: Proposal['scan_summary']) {
  if (!summary) return 'Codex completed the bounded check without producing operations.'
  if (typeof summary === 'string') return summary
  const entries = Object.entries(summary)
  if (!entries.length) return 'Codex completed the bounded check without producing operations.'
  return entries.map(([key, value]) => `${humanize(key)}: ${String(value)}`).join(' · ')
}

function proposalScopeLabel(snapshot: ProjectSnapshot, proposal: Proposal) {
  if (proposal.scope_type === 'pipeline' && proposal.scope_id) {
    const pipeline = snapshot.pipelines.find((item) => item.id === proposal.scope_id)
    return pipeline ? `Pipeline · ${pipeline.title}` : `Pipeline · ${shortProposalId(proposal.scope_id)}`
  }
  if (proposal.scope_type === 'task' && proposal.scope_id) {
    const task = snapshot.tasks.find((item) => item.id === proposal.scope_id)
    if (!task) return `Task · ${shortProposalId(proposal.scope_id)}`
    return `Task · ${createTaskLabeler(snapshot.pipelines, snapshot.tasks)(task)}`
  }
  return `Project · ${snapshot.project.name}`
}

function guidedSeedForProposal(proposal: Proposal, intent?: AgentPrompt): GuidedRequestSeed | null {
  if (!proposal.workflow_mode || !GUIDED_WORKFLOW_MODES.includes(proposal.workflow_mode as (typeof GUIDED_WORKFLOW_MODES)[number])) return null
  if (!proposal.scope_type || !['project', 'pipeline', 'task'].includes(proposal.scope_type)) return null
  const local = readGuidedIntentDraft(proposal.intent_id)
  return {
    ...local,
    mode: proposal.workflow_mode as GuidedRequestSeed['mode'],
    scopeType: proposal.scope_type as GuidedRequestSeed['scopeType'],
    scopeId: proposal.scope_id ?? null,
    instructions: intent?.instructions ?? local.instructions,
    allowCompletion: intent?.allow_completion ?? local.allowCompletion,
    artifactLocators: intent?.artifact_locators ?? local.artifactLocators,
  }
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

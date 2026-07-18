import type {
  AgentPrompt,
  AgentPromptRequest,
  AuditEvent,
  MutationOperation,
  MutationResult,
  Project,
  ProjectSnapshot,
  Proposal,
  ProposalPage,
  ProposalOperation,
  OutboxReplay,
  SearchResponse,
  SkillStatus,
} from '../types'

export const API_VERSION = '1'
export const SCHEMA_VERSION = '1'

export class ApiError extends Error {
  status: number
  details: unknown

  constructor(message: string, status: number, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

const csrfToken = () => {
  const match = document.cookie.match(/(?:^|; )research_monitor_csrf=([^;]*)/)
  return match ? decodeURIComponent(match[1]) : undefined
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  if (init.body) headers.set('Content-Type', 'application/json')
  const csrf = csrfToken()
  if (csrf && init.method && init.method !== 'GET') headers.set('X-CSRF-Token', csrf)

  let response: Response
  try {
    response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  } catch (error) {
    window.dispatchEvent(new CustomEvent('research-monitor:transport-failure', { detail: { path } }))
    throw error
  }
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('research-monitor:authentication-required', { detail: { path } }))
  } else if ([502, 503, 504].includes(response.status)) {
    window.dispatchEvent(new CustomEvent('research-monitor:transport-failure', { detail: { path } }))
  } else {
    window.dispatchEvent(new CustomEvent('research-monitor:request-success', { detail: { path } }))
  }
  if (!response.ok) {
    let body: unknown
    try {
      body = await response.json()
    } catch {
      body = await response.text()
    }
    const detail = typeof body === 'object' && body && 'detail' in body
      ? (body as { detail: unknown }).detail
      : undefined
    const message = typeof detail === 'string'
      ? detail
      : typeof detail === 'object' && detail && 'message' in detail
        ? String((detail as { message: unknown }).message)
        : `Request failed (${response.status})`
    throw new ApiError(message, response.status, body)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function unpack<T>(value: T | { data: T }): T {
  return value && typeof value === 'object' && 'data' in value ? (value as { data: T }).data : value as T
}

export const api = {
  async listProjects(includeArchived = true, includeTrashed = false): Promise<Project[]> {
    const value = await request<{ projects: Project[] } | Project[]>(`/api/v1/projects?include_archived=${includeArchived}&include_trashed=${includeTrashed}`)
    const result = unpack(value)
    return Array.isArray(result) ? result : result.projects
  },

  async createProject(input: {
    name: string
    root_path: string
    description?: string
    research_goal?: string
    success_criteria?: string
    color?: string
  }): Promise<Project> {
    const value = await request<{ project: Project } | Project>('/api/v1/projects', {
      method: 'POST',
      body: JSON.stringify(input),
    })
    const result = unpack(value)
    return 'project' in result ? result.project : result
  },

  async getSnapshot(projectId: string, sections: string[] = []): Promise<ProjectSnapshot> {
    const query = sections.length ? `?sections=${encodeURIComponent(sections.join(','))}` : ''
    return unpack(await request<ProjectSnapshot | { data: ProjectSnapshot }>(`/api/v1/projects/${projectId}/snapshot${query}`))
  },

  async getHistory(projectId: string): Promise<AuditEvent[]> {
    const value = unpack(await request<{ events: AuditEvent[] } | AuditEvent[]>(`/api/v1/projects/${projectId}/history`))
    return Array.isArray(value) ? value : value.events
  },

  async getProposals(projectId: string): Promise<Proposal[]> {
    const value = unpack(await request<{ proposals: Proposal[] } | Proposal[]>(`/api/v1/projects/${projectId}/proposals`))
    return Array.isArray(value) ? value : value.proposals
  },

  async getProposalPage(projectId: string, options: {
    cursor?: string
    status?: string
    workflowMode?: string
    scopeType?: string
    limit?: number
    summary?: boolean
  } = {}): Promise<ProposalPage> {
    const params = new URLSearchParams()
    if (options.cursor) params.set('cursor', options.cursor)
    if (options.status) params.set('status', options.status)
    if (options.workflowMode) params.set('workflow_mode', options.workflowMode)
    if (options.scopeType) params.set('scope_type', options.scopeType)
    params.set('limit', String(options.limit ?? 20))
    if (options.summary !== false) params.set('summary', 'true')
    const value = unpack(await request<ProposalPage | Proposal[] | { proposals: Proposal[] }>(`/api/v1/projects/${projectId}/proposals?${params}`))
    if (Array.isArray(value)) return { proposals: value, next_cursor: null, total: value.length }
    return {
      proposals: value.proposals,
      next_cursor: 'next_cursor' in value ? value.next_cursor : null,
      total: 'total' in value ? value.total : value.proposals.length,
      draft_count: 'draft_count' in value ? value.draft_count : undefined,
      closed_count: 'closed_count' in value ? value.closed_count : undefined,
      has_more: 'has_more' in value ? value.has_more : Boolean('next_cursor' in value && value.next_cursor),
      status_counts: 'status_counts' in value ? value.status_counts : undefined,
      result_kind_counts: 'result_kind_counts' in value ? value.result_kind_counts : undefined,
      workflow_mode_counts: 'workflow_mode_counts' in value ? value.workflow_mode_counts : undefined,
    }
  },

  async getProposal(_projectId: string, proposalId: string): Promise<Proposal> {
    return unpack(await request<Proposal | { data: Proposal }>(`/api/v1/proposals/${proposalId}`))
  },

  async createAgentPrompt(projectId: string, input: AgentPromptRequest): Promise<AgentPrompt> {
    const value = unpack(await request<AgentPrompt | { data: AgentPrompt }>(`/api/v1/projects/${projectId}/agent-prompts`, {
      method: 'POST',
      body: JSON.stringify(input),
    }))
    const compatible = value as AgentPrompt & { id?: string; mode?: AgentPrompt['workflow_mode'] }
    return {
      ...compatible,
      intent_id: compatible.intent_id ?? compatible.id ?? '',
      workflow_mode: compatible.workflow_mode ?? compatible.mode ?? input.mode,
      scope_type: compatible.scope_type ?? input.scope_type,
      scope_id: compatible.scope_id ?? input.scope_id,
    }
  },

  async getAgentPrompt(projectId: string, intentId: string): Promise<AgentPrompt> {
    return unpack(await request<AgentPrompt | { data: AgentPrompt }>(`/api/v1/projects/${projectId}/agent-prompts/${intentId}`))
  },

  async getSkillStatus(): Promise<SkillStatus> {
    const value = unpack(await request<SkillStatus | { data: SkillStatus }>('/api/v1/skill-status'))
    const displayStatus = String(value.status || '').trim()
    const rawStatus = String(value.normalized_status || displayStatus).trim()
    const normalized = rawStatus.toLocaleLowerCase()
    const status = normalized === 'installed and current' || normalized === 'current'
      ? 'current'
      : normalized === 'missing'
        ? 'missing'
        : normalized === 'modified'
          ? 'modified'
          : normalized === 'outdated'
            ? 'outdated'
            : normalized === 'blocked'
              ? 'blocked'
              : rawStatus
    return { ...value, status, normalized_status: status, label: value.label ?? displayStatus }
  },

  async getArtifactMetadata(artifactId: string): Promise<import('../types').Artifact> {
    return unpack(await request<import('../types').Artifact | { data: import('../types').Artifact }>(`/api/v1/artifacts/${artifactId}/metadata`))
  },

  async getEvents(after: number, streamId?: string): Promise<OutboxReplay> {
    const params = new URLSearchParams({ after: String(after) })
    if (streamId) params.set('stream_id', streamId)
    return unpack(await request<OutboxReplay | { data: OutboxReplay }>(`/api/v1/events?${params}`))
  },

  async searchProject(projectId: string, query: string): Promise<SearchResponse> {
    const params = new URLSearchParams({ q: query, limit: '200' })
    return unpack(await request<SearchResponse | { data: SearchResponse }>(`/api/v1/projects/${projectId}/search?${params}`))
  },

  async mutate(projectId: string, baseRevision: number, operations: MutationOperation[], actorLabel = 'Research Monitor UI'): Promise<MutationResult> {
    const requestId = crypto.randomUUID()
    return unpack(await request<MutationResult | { data: MutationResult }>(`/api/v1/projects/${projectId}/mutations`, {
      method: 'POST',
      body: JSON.stringify({
        api_version: API_VERSION,
        schema_version: SCHEMA_VERSION,
        request_id: requestId,
        project_id: projectId,
        base_semantic_revision: baseRevision,
        actor_type: 'ui',
        actor_label: actorLabel,
        operations,
      }),
    }))
  },

  async undoMutation(projectId: string, targetRequestId: string, baseRevision: number): Promise<MutationResult> {
    return unpack(await request<MutationResult | { data: MutationResult }>(`/api/v1/projects/${projectId}/mutations/${targetRequestId}/undo`, {
      method: 'POST',
      body: JSON.stringify({
        request_id: crypto.randomUUID(),
        base_semantic_revision: baseRevision,
      }),
    }))
  },

  async mutateLayout(projectId: string, baseRevision: number, operations: MutationOperation[]): Promise<MutationResult> {
    const requestId = crypto.randomUUID()
    return unpack(await request<MutationResult | { data: MutationResult }>(`/api/v1/projects/${projectId}/layout-mutations`, {
      method: 'POST',
      body: JSON.stringify({
        api_version: API_VERSION,
        schema_version: SCHEMA_VERSION,
        request_id: requestId,
        project_id: projectId,
        base_layout_revision: baseRevision,
        actor_type: 'ui',
        actor_label: 'Research Monitor graph',
        operations,
      }),
    }))
  },

  async applyProposal(
    projectId: string,
    proposalId: string,
    selectedOperationIds: string[],
    operationOverrides: ProposalOperation[] = [],
    requestId: string = crypto.randomUUID(),
  ): Promise<MutationResult> {
    const cleanOverrides = operationOverrides.map(({
      disposition: _disposition,
      before: _before,
      after: _after,
      risk: _risk,
      default_selected: _defaultSelected,
      ...operation
    }) => operation)
    return unpack(await request<MutationResult | { data: MutationResult }>(`/api/v1/projects/${projectId}/proposals/${proposalId}/apply`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, selected_operation_ids: selectedOperationIds, operation_overrides: cleanOverrides }),
    }))
  },

  async reviseProposal(projectId: string, proposalId: string, baseRevision: number, summary: string, rationale: string, operations: ProposalOperation[], requestId: string): Promise<Proposal> {
    const cleanOperations = operations.map(({
      disposition: _disposition,
      before: _before,
      after: _after,
      risk: _risk,
      default_selected: _defaultSelected,
      ...operation
    }) => operation)
    return unpack(await request<Proposal | { data: Proposal }>(`/api/v1/projects/${projectId}/proposals/${proposalId}/revisions`, {
      method: "POST",
      body: JSON.stringify({
        api_version: API_VERSION,
        schema_version: SCHEMA_VERSION,
        request_id: requestId,
        project_id: projectId,
        base_semantic_revision: baseRevision,
        actor_type: "ui",
        actor_label: "Research Monitor staging",
        summary,
        rationale,
        operations: cleanOperations,
      }),
    }))
  },

  async rejectProposal(projectId: string, proposalId: string, reason = '', requestId: string = crypto.randomUUID()): Promise<void> {
    await request(`/api/v1/projects/${projectId}/proposals/${proposalId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ request_id: requestId, reason }),
    })
  },

  artifactPreviewUrl(artifactId: string) {
    return `/api/v1/artifacts/${artifactId}/preview`
  },
}

export function operation(type: string, data: Record<string, unknown>, entity?: { id: string; version?: number }): MutationOperation {
  return {
    id: crypto.randomUUID(),
    type,
    data,
    entity_id: entity?.id,
    expected_version: entity?.version,
  }
}

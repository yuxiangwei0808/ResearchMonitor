import type {
  AuditEvent,
  MutationOperation,
  MutationResult,
  Project,
  ProjectSnapshot,
  Proposal,
  ProposalOperation,
  OutboxReplay,
  SearchResponse,
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

  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
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

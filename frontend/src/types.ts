export const TASK_STATUSES = ['planned', 'in_progress', 'blocked', 'review', 'done', 'dropped'] as const
export const TASK_PRIORITIES = ['required', 'recommended', 'optional', 'conditional'] as const
export const TASK_OUTCOMES = ['successful', 'negative', 'inconclusive', 'failed', 'not_applicable'] as const
export const TASK_KINDS = ['task', 'milestone', 'gate'] as const
export const ARTIFACT_ROLES = ['input', 'code', 'document', 'log', 'result', 'checkpoint', 'figure', 'dataset', 'evidence', 'reference', 'external_run'] as const
export const GUIDED_WORKFLOW_MODES = ['initialize_structure', 'expand_task', 'reconcile_progress', 'suggest_next_work', 'record_update', 'link_artifacts'] as const

export type TaskStatus = (typeof TASK_STATUSES)[number]
export type TaskPriority = (typeof TASK_PRIORITIES)[number]
export type TaskOutcome = (typeof TASK_OUTCOMES)[number]
export type TaskKind = (typeof TASK_KINDS)[number]
export type ArtifactRole = (typeof ARTIFACT_ROLES)[number]
export type GuidedWorkflowMode = (typeof GUIDED_WORKFLOW_MODES)[number]
export type AgentScopeType = 'project' | 'pipeline' | 'task'
export type Readiness = 'ready' | 'waiting' | 'blocked' | 'inconsistent'

export interface ProjectProgress {
  leaf_total: number
  leaf_done: number
  ready: number
  waiting: number
  blocked: number
  review: number
  by_status?: Record<string, number>
  by_outcome?: Record<string, number>
}

export interface Project {
  id: string
  name: string
  root_path: string
  description?: string | null
  research_goal?: string | null
  success_criteria?: string | null
  color: string
  archived: boolean
  trashed?: boolean
  unavailable?: boolean
  semantic_revision: number
  layout_revision: number
  /** Compatibility aliases emitted by v0.2 snapshots. */
  version?: number
  entity_version?: number
  updated_at?: string
  last_manual_update?: string | null
  last_proposal_at?: string | null
  last_agent_check_at?: string | null
  progress?: ProjectProgress
}

export interface ScanPolicy {
  preferred_sources: string[]
  include_globs: string[]
  exclude_globs: string[]
  max_text_file_size: number
  allow_git_metadata: boolean
  git_history_limit: number
  sensitive_patterns: string[]
  allow_outside_sources: boolean
  readable_source_root_ids?: string[]
  max_files_per_scan?: number
  max_total_text_bytes?: number
  follow_symlinks: false
  version?: number
}

export interface PlanningProfile {
  project_id?: string
  task_granularity: 'coarse' | 'balanced' | 'detailed'
  max_nesting_depth: number
  planning_horizon: 'immediate' | 'current_milestone' | 'whole_project'
  inference_policy: 'sources_only' | 'cautious_gaps' | 'broad_roadmap'
  max_new_tasks_per_proposal: number
  preferred_pipeline_names: string[]
  terminology_notes: string
  additional_instructions: string
  protected_pipeline_ids: string[]
  protected_task_ids: string[]
  version: number
}

export interface ArtifactRoot {
  id: string
  project_id: string
  name: string
  canonical_path: string
  is_project_root?: boolean
  version?: number
}

export interface Pipeline {
  id: string
  project_id: string
  title: string
  description?: string | null
  flow_mode: 'sequential' | 'freeform'
  position: number
  archived: boolean
  deleted_at?: string | null
  version: number
}

export interface Task {
  id: string
  project_id: string
  pipeline_id: string
  parent_id?: string | null
  user_key?: string | null
  kind: TaskKind
  title: string
  description?: string | null
  status: TaskStatus
  outcome?: TaskOutcome | null
  priority: TaskPriority
  labels: string[]
  target_date?: string | null
  position: number
  completion_criteria?: string | null
  blocker_reason?: string | null
  completion_summary?: string | null
  completion_actor?: string | null
  completion_source?: string | null
  completion_provenance?: 'manual' | 'agent' | null
  completion_override_reason?: string | null
  consistency_warning?: string | null
  incomplete_descendant_ids?: string[]
  child_flow_mode: 'sequential' | 'freeform'
  readiness: Readiness
  unsatisfied_predecessor_ids: string[]
  completed_at?: string | null
  created_at?: string
  updated_at?: string
  deleted_at?: string | null
  version: number
}

export interface TaskEdge {
  id: string
  project_id: string
  source_task_id: string
  target_task_id: string
  edge_type: 'dependency' | 'related'
  waived?: boolean
  waiver_reason?: string | null
  disabled?: boolean
  disabled_reason?: string | null
  deleted_at?: string | null
  version: number
}

export interface JournalEntry {
  id: string
  project_id: string
  task_id: string
  entry_type: 'progress' | 'decision' | 'blocker' | 'note' | 'completion'
  content: string
  occurred_at: string
  updated_at?: string
  deleted_at?: string | null
  origin_key?: string | null
  content_sha256?: string | null
  version: number
}

export interface Artifact {
  id: string
  project_id: string
  artifact_root_id?: string | null
  locator: string
  kind: 'local' | 'url'
  provider?: string | null
  label: string
  notes?: string | null
  available?: boolean | null
  mime_type?: string | null
  size_bytes?: number | null
  previewable?: boolean
  preview_reason?: string | null
  preview_mode?: 'text' | 'image' | 'pdf' | null
  deleted_at?: string | null
  updated_at?: string | null
  version: number
}

export interface TaskArtifact {
  id: string
  task_id: string
  artifact_id: string
  role: ArtifactRole
  notes?: string | null
}

export interface TaskLayout {
  id?: string
  task_id: string
  parent_id?: string | null
  x: number
  y: number
  version?: number
}

export interface GraphViewport {
  id: string
  parent_id?: string | null
  x: number
  y: number
  zoom: number
  version: number
}

export interface AuditEvent {
  id: string | number
  project_id: string
  request_id?: string | null
  actor_type: 'ui' | 'agent' | 'import' | 'system'
  actor_label?: string | null
  event_type: string
  entity_type?: string | null
  entity_id?: string | null
  summary: string
  created_at: string
  before?: unknown
  after?: unknown
  undoable?: boolean
  undo_reason?: string | null
  undo_code?: string | null
  undo_request_head?: boolean
  undo_operation_count?: number
}

export interface EvidenceRef {
  kind?: unknown
  locator?: unknown
  description?: unknown
  summary?: unknown
  path?: unknown
  source_path?: unknown
  anchor?: unknown
  opaque_key?: unknown
  id?: unknown
  monitor_reference_id?: unknown
  fingerprint?: unknown
  content_hash?: unknown
  source_root_id?: unknown
  [key: string]: unknown
}

export type EvidenceItem = EvidenceRef | string

export interface ProposalOperation {
  id: string
  type: string
  data: Record<string, unknown>
  entity_id?: string | null
  expected_version?: number | null
  rationale?: string | null
  confidence?: number | null
  evidence?: EvidenceItem[]
  source_references?: EvidenceRef[]
  prerequisite_operation_ids?: string[]
  atomic_group_id?: string | null
  disposition?: 'pending' | 'selected' | 'applied' | 'rejected' | 'conflict'
  before?: Record<string, unknown> | null
  after?: Record<string, unknown> | null
  basis?: 'source_evidence' | 'user_instruction' | 'inference' | null
  risk?: 'normal' | 'high' | string | null
  default_selected?: boolean
}

export interface Proposal {
  id: string
  project_id: string
  summary: string
  rationale?: string | null
  status: 'draft' | 'applied' | 'rejected' | 'conflict' | 'superseded' | 'no_changes'
  base_semantic_revision: number
  operations: ProposalOperation[]
  operation_count?: number
  detail_loaded?: boolean
  detail_url?: string
  created_at: string
  actor_label?: string | null
  supersedes_proposal_id?: string | null
  superseded_by_proposal_id?: string | null
  proposal_contract_version?: '1' | '2' | string
  workflow_request_id?: string | null
  intent_id?: string | null
  workflow_mode?: GuidedWorkflowMode | 'legacy_custom' | string
  scope_type?: AgentScopeType | null
  scope_id?: string | null
  result_kind?: 'changes' | 'no_changes'
  no_change_reason?: 'up_to_date' | 'insufficient_evidence' | 'ambiguous_sources' | null
  scan_summary?: string | Record<string, unknown> | null
  top_level_evidence?: EvidenceItem[]
  evidence?: EvidenceItem[]
  source_references?: EvidenceRef[]
  fingerprint_version?: number
  regenerates_proposal_id?: string | null
  risk_counts?: Record<string, number>
  basis_counts?: Record<string, number>
  evidence_count?: number
  source_reference_count?: number
}

export interface ProposalPage {
  proposals: Proposal[]
  next_cursor?: string | null
  total?: number
  draft_count?: number
  closed_count?: number
  has_more?: boolean
  status_counts?: Record<string, number>
  result_kind_counts?: Record<string, number>
  workflow_mode_counts?: Record<string, number>
}

export interface AgentArtifactLocator {
  kind: 'local' | 'url'
  locator: string
  artifact_root_id?: string | null
  label?: string
  provider?: string
}

export interface AgentPromptRequest {
  mode: GuidedWorkflowMode
  scope_type: AgentScopeType
  scope_id?: string | null
  instructions?: string
  force_fresh?: boolean
  allow_completion?: boolean
  artifact_locators?: AgentArtifactLocator[]
  regenerate_proposal_id?: string | null
}

export interface SkillStatus {
  status: 'current' | 'missing' | 'modified' | 'outdated' | 'blocked' | string
  normalized_status?: 'current' | 'missing' | 'modified' | 'outdated' | 'blocked' | string
  optional?: boolean
  label?: string
  installed_version?: string | null
  bundled_version?: string | null
  command?: string | null
  setup_command?: string | null
  detail?: string | null
  blocking_reason?: string | null
  installed?: boolean
  modified?: boolean
  update_available?: boolean
  path?: string
  destination?: string | null
}

export interface AutomationState {
  active_intent_count?: number
  unexpired_unconsumed_intent_count?: number
  open_draft_count?: number
}

export interface AgentPromptWarning {
  code: string
  message: string
  proposal_ids?: string[]
}

export interface AgentPrompt {
  intent_id: string
  project_id?: string
  proposal_request_id?: string
  prompt_version?: string
  issued_semantic_revision?: number
  planning_profile_version?: number
  expires_at: string
  workflow_mode: GuidedWorkflowMode
  scope_type: AgentScopeType
  scope_id?: string | null
  allow_completion?: boolean
  instructions?: string
  artifact_locators?: AgentArtifactLocator[]
  regenerates_proposal_id?: string | null
  consumed_proposal_id?: string | null
  prompt: string
  context_command?: string
  disclosure?: string
  warnings?: AgentPromptWarning[]
  skill_status?: SkillStatus
}

export interface ProjectSnapshot {
  project: Project
  scan_policy: ScanPolicy
  artifact_roots: ArtifactRoot[]
  pipelines: Pipeline[]
  tasks: Task[]
  edges: TaskEdge[]
  journals: JournalEntry[]
  artifacts: Artifact[]
  task_artifacts: TaskArtifact[]
  layouts: TaskLayout[]
  viewports?: GraphViewport[]
  progress: ProjectProgress
  planning_profile?: PlanningProfile
  automation_state?: AutomationState
}

export interface MutationOperation {
  id: string
  type: string
  data: Record<string, unknown>
  entity_id?: string
  expected_version?: number
}

export interface MutationResult {
  request_id: string
  project_id: string
  semantic_revision: number
  layout_revision: number
  results: Array<Record<string, unknown>>
}

export interface OutboxEvent {
  id: number
  project_id: string
  event_type: string
  payload?: unknown
  created_at: string
}

export interface OutboxReplay {
  events: OutboxEvent[]
  stream_id: string
  latest_id: number
  reset_required: boolean
  reset_reason: string | null
}

export interface SearchResult {
  entity_type: 'task' | 'journal' | 'artifact'
  entity_id: string
  title: string
  snippet: string
  rank: number
  task_id?: string
  artifact_type?: 'local' | 'url'
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  count: number
  total: number
  offset: number
  limit: number
  truncated: boolean
}

/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import type { ProjectSnapshot } from '../types'
import { OverviewView } from './OverviewView'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const snapshot: ProjectSnapshot = {
  project: { id: projectId, name: 'Outcome study', root_path: '/tmp/outcomes', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0 },
  scan_policy: { preferred_sources: [], include_globs: [], exclude_globs: [], max_text_file_size: 2_097_152, allow_git_metadata: false, git_history_limit: 0, sensitive_patterns: [], allow_outside_sources: false, follow_symlinks: false },
  artifact_roots: [],
  pipelines: [{ id: pipelineId, project_id: projectId, title: 'Experiments', flow_mode: 'freeform', position: 0, archived: false, version: 1 }],
  tasks: [
    { id: '33333333-3333-4333-8333-333333333331', project_id: projectId, pipeline_id: pipelineId, title: 'Positive result', kind: 'task', status: 'done', outcome: 'successful', priority: 'required', labels: [], position: 0, child_flow_mode: 'freeform', readiness: 'ready', unsatisfied_predecessor_ids: [], version: 1 },
    { id: '33333333-3333-4333-8333-333333333332', project_id: projectId, pipeline_id: pipelineId, title: 'Negative result', kind: 'task', status: 'done', outcome: 'negative', priority: 'required', labels: [], position: 1, child_flow_mode: 'freeform', readiness: 'ready', unsatisfied_predecessor_ids: [], version: 1 },
  ],
  edges: [], journals: [], artifacts: [], task_artifacts: [], layouts: [],
  progress: { leaf_total: 2, leaf_done: 2, ready: 0, waiting: 0, blocked: 0, review: 0, by_status: { done: 2 }, by_outcome: { successful: 1, negative: 1 } },
}

function renderView(client: QueryClient) {
  return render(<MemoryRouter><QueryClientProvider client={client}><OverviewView snapshot={snapshot} copyPrompt={() => undefined} /></QueryClientProvider></MemoryRouter>)
}

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('OverviewView progress', () => {
  it('renders separate workflow and research-outcome breakdowns', async () => {
    vi.spyOn(api, 'getHistory').mockResolvedValue([])
    renderView(new QueryClient({ defaultOptions: { queries: { retry: false } } }))
    const panel = screen.getByRole('heading', { name: 'Workflow status and research outcomes' }).closest('section')!
    expect(within(panel).getByText('Done').parentElement?.parentElement?.textContent).toContain('2')
    expect(within(panel).getByText('Successful').parentElement?.textContent).toContain('1')
    expect(within(panel).getByText('Negative').parentElement?.textContent).toContain('1')
  })

  it('shows a retryable error instead of claiming history is empty', async () => {
    vi.spyOn(api, 'getHistory').mockRejectedValue(new Error('History unavailable'))
    renderView(new QueryClient({ defaultOptions: { queries: { retry: false } } }))
    expect(await screen.findByText('History unavailable')).not.toBeNull()
    expect(screen.queryByText('No recorded activity yet.')).toBeNull()
    expect(screen.getByRole('button', { name: 'Try again' })).not.toBeNull()
  })
})

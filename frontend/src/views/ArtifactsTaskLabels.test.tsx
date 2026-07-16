/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { ProjectSnapshot, Task } from '../types'
import { ArtifactsView } from './ArtifactsView'

const projectId = '11111111-1111-4111-8111-111111111111'
const pipelineId = '22222222-2222-4222-8222-222222222222'
const task = (id: string, title: string, parentId: string | null, userKey?: string): Task => ({
  id,
  project_id: projectId,
  pipeline_id: pipelineId,
  parent_id: parentId,
  title,
  user_key: userKey,
  kind: 'task',
  status: 'planned',
  priority: 'required',
  labels: [],
  position: 0,
  child_flow_mode: 'freeform',
  readiness: 'ready',
  unsatisfied_predecessor_ids: [],
  version: 1,
})
const root = task('33333333-3333-4333-8333-333333333333', 'Prepare data', null, 'DATA')
const child = task('44444444-4444-4444-8444-444444444444', 'Validate manifest', root.id, 'QC')
const snapshot: ProjectSnapshot = {
  project: { id: projectId, name: 'Artifact label test', root_path: '/tmp/artifact-label', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0 },
  scan_policy: { preferred_sources: [], include_globs: [], exclude_globs: [], max_text_file_size: 2_097_152, allow_git_metadata: false, git_history_limit: 0, sensitive_patterns: [], allow_outside_sources: false, follow_symlinks: false },
  artifact_roots: [],
  pipelines: [{ id: pipelineId, project_id: projectId, title: 'Evaluation', flow_mode: 'freeform', position: 0, archived: false, version: 1 }],
  tasks: [root, child], edges: [], journals: [], artifacts: [], task_artifacts: [], layouts: [],
  progress: { leaf_total: 1, leaf_done: 0, ready: 1, waiting: 0, blocked: 0, review: 0 },
}

afterEach(cleanup)

describe('artifact task association labels', () => {
  it('shows the pipeline and full ancestor path in the task picker', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={client}><ArtifactsView snapshot={snapshot} /></QueryClientProvider></MemoryRouter>)

    fireEvent.click(screen.getByRole('button', { name: 'Link first artifact' }))
    const picker = screen.getByLabelText('Add task association') as HTMLSelectElement
    expect([...picker.options].map((option) => option.text)).toContain('Evaluation › DATA · Prepare data › QC · Validate manifest')
  })
})

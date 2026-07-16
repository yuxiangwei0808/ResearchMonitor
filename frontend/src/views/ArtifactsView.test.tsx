/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { ProjectSnapshot } from '../types'
import { ArtifactsView } from './ArtifactsView'

const projectId = '11111111-1111-4111-8111-111111111111'
const snapshot: ProjectSnapshot = {
  project: { id: projectId, name: 'Scale study', root_path: '/tmp/scale', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0 },
  scan_policy: { preferred_sources: [], include_globs: [], exclude_globs: [], max_text_file_size: 2_097_152, allow_git_metadata: false, git_history_limit: 0, sensitive_patterns: [], allow_outside_sources: false, follow_symlinks: false },
  artifact_roots: [], pipelines: [], tasks: [], edges: [], journals: [], task_artifacts: [], layouts: [],
  artifacts: Array.from({ length: 205 }, (_, index) => ({ id: `artifact-${String(index).padStart(4, '0')}`, project_id: projectId, locator: `https://example.test/${index}`, kind: 'url' as const, label: `Artifact ${String(index).padStart(3, '0')}`, version: 1 })),
  progress: { leaf_total: 0, leaf_done: 0, ready: 0, waiting: 0, blocked: 0, review: 0 },
}

afterEach(cleanup)

describe('ArtifactsView scale bounds', () => {
  it('renders at most 100 artifact rows and exposes accessible paging controls', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={client}><ArtifactsView snapshot={snapshot} /></QueryClientProvider></MemoryRouter>)

    expect(screen.getAllByRole('row')).toHaveLength(101)
    expect(screen.getByRole('navigation', { name: 'Artifact pages' }).textContent).toContain('Page 1 of 3')
    expect(screen.queryByText('Artifact 100')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Artifact 100')).not.toBeNull()
    expect(screen.queryByText('Artifact 000')).toBeNull()
    expect(screen.getAllByRole('row')).toHaveLength(101)
  })

  it('sorts the full filtered collection before selecting a page', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<MemoryRouter><QueryClientProvider client={client}><ArtifactsView snapshot={snapshot} /></QueryClientProvider></MemoryRouter>)

    const artifactSort = screen.getByRole('button', { name: /^Artifact/ })
    fireEvent.click(artifactSort)
    fireEvent.click(artifactSort)

    expect(screen.getByText('Artifact 204')).not.toBeNull()
    expect(screen.queryByText('Artifact 104')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('Artifact 104')).not.toBeNull()
    expect(screen.queryByText('Artifact 204')).toBeNull()
  })
})

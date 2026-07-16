/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import type { ProjectSnapshot } from '../types'
import { SettingsView } from './SettingsView'

const snapshot: ProjectSnapshot = {
  project: { id: '11111111-1111-4111-8111-111111111111', name: 'Original project', root_path: '/tmp/project', description: 'Original description', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0 },
  scan_policy: { preferred_sources: [], include_globs: ['**/*.md'], exclude_globs: [], max_text_file_size: 2_097_152, allow_git_metadata: false, git_history_limit: 20, sensitive_patterns: [], allow_outside_sources: false, follow_symlinks: false, version: 1 },
  artifact_roots: [], pipelines: [], tasks: [], edges: [], journals: [], artifacts: [], task_artifacts: [], layouts: [],
  progress: { leaf_total: 0, leaf_done: 0, ready: 0, waiting: 0, blocked: 0, review: 0 },
}

function view(value: ProjectSnapshot, client: QueryClient) {
  return (
    <MemoryRouter>
      <QueryClientProvider client={client}><SettingsView snapshot={value} /></QueryClientProvider>
    </MemoryRouter>
  )
}

afterEach(cleanup)

describe('SettingsView draft preservation', () => {
  it('preserves dirty project and scan-policy fields when a newer snapshot arrives', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const rendered = render(view(snapshot, client))
    const name = screen.getByLabelText('Project name') as HTMLInputElement
    const include = screen.getByLabelText('Include globs') as HTMLTextAreaElement
    fireEvent.change(name, { target: { value: 'My unsaved project name' } })
    fireEvent.change(include, { target: { value: '**/*.md\nsrc/**/*.py' } })

    const refreshed: ProjectSnapshot = {
      ...snapshot,
      project: { ...snapshot.project, description: 'Changed elsewhere', semantic_revision: 2 },
      scan_policy: { ...snapshot.scan_policy, exclude_globs: ['data/**'], version: 2 },
    }
    rendered.rerender(view(refreshed, client))

    expect(name.value).toBe('My unsaved project name')
    expect(include.value).toContain('src/**/*.py')
    expect(await screen.findByText(/Project details changed in another UI or CLI action/)).not.toBeNull()
    expect(await screen.findByText(/scan policy changed in another UI or CLI action/)).not.toBeNull()
  })
})

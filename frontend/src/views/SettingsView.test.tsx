/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProjectSnapshot } from '../types'
import { SettingsView } from './SettingsView'
import { api } from '../lib/api'

const snapshot: ProjectSnapshot = {
  project: { id: '11111111-1111-4111-8111-111111111111', name: 'Original project', root_path: '/tmp/project', description: 'Original description', color: '#5c6e48', archived: false, semantic_revision: 1, layout_revision: 0, version: 7 },
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

afterEach(() => { cleanup(); vi.restoreAllMocks() })

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

  it('preserves planning-profile edits and hierarchical protection selections across a conflicting refresh', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const pipelineId = '22222222-2222-4222-8222-222222222222'
    const taskId = '33333333-3333-4333-8333-333333333333'
    const configured: ProjectSnapshot = {
      ...snapshot,
      pipelines: [{ id: pipelineId, project_id: snapshot.project.id, title: 'Experiments', flow_mode: 'freeform', position: 0, archived: false, version: 1 }],
      tasks: [{ id: taskId, project_id: snapshot.project.id, pipeline_id: pipelineId, parent_id: null, user_key: 'EXP-1', kind: 'task', title: 'Run baseline', status: 'planned', priority: 'required', labels: [], position: 0, child_flow_mode: 'freeform', readiness: 'ready', unsatisfied_predecessor_ids: [], version: 1 }],
      planning_profile: {
        task_granularity: 'balanced', max_nesting_depth: 3, planning_horizon: 'current_milestone', inference_policy: 'cautious_gaps', max_new_tasks_per_proposal: 30,
        preferred_pipeline_names: [], terminology_notes: '', additional_instructions: '', protected_pipeline_ids: [], protected_task_ids: [], version: 1,
      },
    }
    const rendered = render(view(configured, client))
    const granularity = screen.getByLabelText('Task granularity') as HTMLSelectElement
    const protectedTask = screen.getByRole('checkbox', { name: /EXP-1 · Run baseline/i }) as HTMLInputElement
    fireEvent.change(granularity, { target: { value: 'detailed' } })
    fireEvent.click(protectedTask)

    const refreshed: ProjectSnapshot = {
      ...configured,
      project: { ...configured.project, semantic_revision: 2 },
      planning_profile: { ...configured.planning_profile!, inference_policy: 'sources_only', version: 2 },
    }
    rendered.rerender(view(refreshed, client))

    expect(granularity.value).toBe('detailed')
    expect(protectedTask.checked).toBe(true)
    expect(await screen.findByText(/planning profile changed elsewhere while you were editing/i)).not.toBeNull()
  })

  it('keeps Save disabled for pristine and reverted normalized values while preserving blank draft lines', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(view(snapshot, client))
    const detailsSave = screen.getByRole('button', { name: 'Save details' }) as HTMLButtonElement
    const policySave = screen.getByRole('button', { name: 'Save scan policy' }) as HTMLButtonElement
    const profileSave = screen.getByRole('button', { name: 'Save planning profile' }) as HTMLButtonElement
    expect(detailsSave.disabled).toBe(true)
    expect(policySave.disabled).toBe(true)
    expect(profileSave.disabled).toBe(true)

    const name = screen.getByLabelText('Project name') as HTMLInputElement
    fireEvent.change(name, { target: { value: 'Changed project' } })
    expect(detailsSave.disabled).toBe(false)
    fireEvent.change(name, { target: { value: '  Original project  ' } })
    expect(detailsSave.disabled).toBe(true)

    const preferredNames = screen.getByLabelText(/Preferred pipeline names/i) as HTMLTextAreaElement
    fireEvent.change(preferredNames, { target: { value: 'Analysis\n\nExperiments' } })
    expect(preferredNames.value).toBe('Analysis\n\nExperiments')
    const include = screen.getByLabelText('Include globs') as HTMLTextAreaElement
    fireEvent.change(include, { target: { value: '**/*.md\n\n**/*.md' } })
    expect(include.value).toBe('**/*.md\n\n**/*.md')
    expect(policySave.disabled).toBe(true)
    fireEvent.change(include, { target: { value: '**/*.md\n\nsrc/**/*.py' } })
    expect(include.value).toBe('**/*.md\n\nsrc/**/*.py')
  })

  it('deduplicates normalized scan-policy lines only when saving', async () => {
    const mutate = vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: 'policy-request', project_id: snapshot.project.id, semantic_revision: 2, layout_revision: 0, results: [],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(view(snapshot, client))
    const include = screen.getByLabelText('Include globs') as HTMLTextAreaElement
    const sensitive = screen.getByLabelText('Sensitive path patterns') as HTMLTextAreaElement
    fireEvent.change(include, { target: { value: '**/*.md\n\n**/*.md\nsrc/**/*.py' } })
    fireEvent.change(sensitive, { target: { value: '.ENV*\n.env*\ncredentials/**' } })
    expect(include.value).toContain('\n\n')
    expect(sensitive.value).toBe('.ENV*\n.env*\ncredentials/**')
    fireEvent.click(screen.getByRole('button', { name: 'Save scan policy' }))
    await waitFor(() => expect(mutate).toHaveBeenCalled())
    const data = mutate.mock.calls[0][2][0].data
    expect(data.include_globs).toEqual(['**/*.md', 'src/**/*.py'])
    expect(data.sensitive_patterns).toEqual(['.ENV*', 'credentials/**'])
  })

  it('does not silently truncate preferred pipeline names before server validation', async () => {
    const mutate = vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: 'profile-request', project_id: snapshot.project.id, semantic_revision: 2, layout_revision: 0, results: [],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(view(snapshot, client))
    const names = Array.from({ length: 21 }, (_, index) => `Pipeline ${index + 1}`)
    fireEvent.change(screen.getByLabelText(/Preferred pipeline names/i), {
      target: { value: names.join('\n') },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save planning profile' }))
    await waitFor(() => expect(mutate).toHaveBeenCalled())
    expect(mutate.mock.calls[0][2][0].data.preferred_pipeline_names).toEqual(names)
  })

  it('uses the project entity version and reports the exact automation state that a profile save would stale', async () => {
    const mutate = vi.spyOn(api, 'mutate').mockResolvedValue({
      request_id: 'request', project_id: snapshot.project.id, semantic_revision: 2, layout_revision: 0, results: [],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(view({
      ...snapshot,
      automation_state: { unexpired_unconsumed_intent_count: 2, open_draft_count: 1 },
    }, client))

    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Versioned project' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save details' }))
    await waitFor(() => expect(mutate).toHaveBeenCalled())
    expect(mutate.mock.calls[0][2][0].expected_version).toBe(7)

    fireEvent.change(screen.getByLabelText('Task granularity'), { target: { value: 'detailed' } })
    expect(screen.getByText('Saving will make 2 active guided requests and 1 open proposal draft stale.')).not.toBeNull()
  })
})

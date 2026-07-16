import { lazy, Suspense, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, useNavigate, useParams } from 'react-router-dom'
import { AlertTriangle, Check, ChevronDown, Clipboard, ExternalLink, FolderOpen, MoreHorizontal } from 'lucide-react'
import { api } from '../lib/api'
import { shortPath } from '../lib/format'
import { Badge, Button, ErrorState, Spinner, ViewErrorBoundary } from '../components/ui'

const OverviewView = lazy(async () => ({ default: (await import('./OverviewView')).OverviewView }))
const OutlineView = lazy(async () => ({ default: (await import('./OutlineView')).OutlineView }))
const GraphView = lazy(async () => ({ default: (await import('./GraphView')).GraphView }))
const ArtifactsView = lazy(async () => ({ default: (await import('./ArtifactsView')).ArtifactsView }))
const ActivityView = lazy(async () => ({ default: (await import('./ActivityView')).ActivityView }))
const ProposalsView = lazy(async () => ({ default: (await import('./ProposalsView')).ProposalsView }))
const SettingsView = lazy(async () => ({ default: (await import('./SettingsView')).SettingsView }))

const views = [
  ['overview', 'Overview'],
  ['outline', 'Outline'],
  ['graph', 'Graph'],
  ['artifacts', 'Artifacts'],
  ['activity', 'Activity'],
  ['proposals', 'Proposals'],
  ['settings', 'Settings'],
] as const

export const snapshotSections: Record<(typeof views)[number][0], string[]> = {
  overview: ['progress', 'pipelines', 'tasks'],
  outline: ['pipelines', 'tasks', 'journals', 'artifact_roots', 'artifacts', 'task_artifacts'],
  graph: ['pipelines', 'tasks', 'edges', 'layouts', 'viewports'],
  artifacts: ['artifact_roots', 'pipelines', 'tasks', 'artifacts', 'task_artifacts'],
  activity: ['project'],
  proposals: ['pipelines', 'tasks', 'edges', 'journals', 'artifacts', 'task_artifacts'],
  settings: ['scan_policy', 'artifact_roots'],
}

export function ProjectWorkspace() {
  const { projectId, view } = useParams()
  const navigate = useNavigate()
  const [copied, setCopied] = useState(false)
  const requestedView = views.some(([slug]) => slug === view) ? view as keyof typeof snapshotSections : 'overview'
  const sections = snapshotSections[requestedView]
  const snapshotQuery = useQuery({
    queryKey: ['snapshot', projectId, requestedView],
    queryFn: () => api.getSnapshot(projectId!, sections),
    enabled: Boolean(projectId),
  })
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: () => api.listProjects(true) })
  if (!view) return <Navigate to={`/projects/${projectId}/overview`} replace />
  if (snapshotQuery.isLoading) return <div className="content-loading"><Spinner label="Loading project monitor…" /></div>
  if (snapshotQuery.error || !snapshotQuery.data) return <div className="page"><ErrorState error={snapshotQuery.error ?? new Error('Project was not found.')} retry={() => snapshotQuery.refetch()} /></div>
  const snapshot = snapshotQuery.data
  const project = snapshot.project
  const copyPrompt = async () => {
    const text = `Use $research-monitor for project ${project.id} at ${project.root_path}. Inspect this enrolled project read-only and propose updates to its pipelines, tasks, progress, and artifact links. Submit a reviewable proposal; do not apply it.`
    await navigator.clipboard.writeText(text)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1800)
  }
  const activeProjects = (projectsQuery.data ?? []).filter((item) => !item.archived && !item.trashed)
  return (
    <div className="workspace-page">
      <div className="project-tab-strip" aria-label="Open projects">
        <NavLink to="/" className="tab-home">Portfolio</NavLink>
        {activeProjects.slice(0, 7).map((item) => (
          <NavLink key={item.id} to={`/projects/${item.id}/overview`} className={`project-top-tab ${item.id === project.id ? 'active' : ''}`}>
            <span className="project-dot" style={{ background: item.color }} />{item.name}
          </NavLink>
        ))}
        {activeProjects.length > 7 && <details className="project-tab-overflow">
          <summary className="project-top-tab overflow-tab"><MoreHorizontal size={16} />{activeProjects.length - 7} more</summary>
          <div className="project-tab-menu" role="menu" aria-label="More enrolled projects">
            {activeProjects.slice(7).map((item) => (
              <NavLink key={item.id} to={`/projects/${item.id}/overview`} role="menuitem">
                <span className="project-dot" style={{ background: item.color }} />
                <span><strong>{item.name}</strong><small>{shortPath(item.root_path, 40)}</small></span>
              </NavLink>
            ))}
          </div>
        </details>}
      </div>
      <header className="project-header">
        <div className="project-heading-copy">
          <div className="project-title-line"><span className="project-heading-mark" style={{ background: project.color }} /> <h1>{project.name}</h1>{project.unavailable && <Badge tone="red"><AlertTriangle size={13} />Folder unavailable</Badge>}</div>
          <p title={project.root_path}><FolderOpen size={14} />{shortPath(project.root_path, 78)}</p>
        </div>
        <div className="project-heading-actions">
          <Button variant="secondary" onClick={copyPrompt}>{copied ? <Check size={16} /> : <Clipboard size={16} />}{copied ? 'Copied' : 'Copy Codex prompt'}</Button>
          <Button variant="ghost" size="icon" aria-label="More project actions" onClick={() => navigate(`/projects/${project.id}/settings`)}><ChevronDown size={18} /></Button>
        </div>
      </header>
      <nav className="view-tabs" aria-label="Project views">
        {views.map(([slug, label]) => <NavLink key={slug} to={`/projects/${project.id}/${slug}`} className={view === slug ? 'active' : ''}>{label}{slug === 'proposals' && <ProposalCount projectId={project.id} />}</NavLink>)}
      </nav>
      <div className="project-content">
        <ViewErrorBoundary resetKey={`${project.id}:${view}`}>
        <Suspense fallback={<div className="content-loading"><Spinner label="Opening project view…" /></div>}>
        {view === 'overview' && <OverviewView snapshot={snapshot} copyPrompt={copyPrompt} />}
        {view === 'outline' && <OutlineView snapshot={snapshot} />}
        {view === 'graph' && <GraphView snapshot={snapshot} />}
        {view === 'artifacts' && <ArtifactsView snapshot={snapshot} />}
        {view === 'activity' && <ActivityView snapshot={snapshot} />}
        {view === 'proposals' && <ProposalsView snapshot={snapshot} />}
        {view === 'settings' && <SettingsView snapshot={snapshot} />}
        {!views.some(([slug]) => slug === view) && <Navigate to={`/projects/${project.id}/overview`} replace />}
        </Suspense>
        </ViewErrorBoundary>
      </div>
    </div>
  )
}

function ProposalCount({ projectId }: { projectId: string }) {
  const query = useQuery({ queryKey: ['proposals', projectId], queryFn: () => api.getProposals(projectId) })
  const count = query.data?.filter((proposal) => proposal.status === 'draft').length ?? 0
  return count ? <span className="tab-count">{count}</span> : null
}

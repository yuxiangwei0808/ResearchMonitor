import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Ban, Bot, Check, CheckCircle2, Clipboard, Clock3, FolderPlus, Layers3, Sparkles } from 'lucide-react'
import { Link, useSearchParams } from 'react-router-dom'
import type { Project } from '../types'
import { Badge, Button, EmptyState, ProgressBar } from '../components/ui'
import { humanize, relativeDate, shortPath } from '../lib/format'
import { api } from '../lib/api'

export function PortfolioView({ projects, onAdd }: { projects: Project[]; onAdd: () => void }) {
  const [params] = useSearchParams()
  const showArchived = params.get('show') === 'archived'
  const showTrash = params.get('show') === 'trash'
  const visible = projects.filter((project) => showTrash ? project.trashed : showArchived ? project.archived && !project.trashed : !project.archived && !project.trashed)
  const heading = showTrash ? 'Recoverable trash' : showArchived ? 'Archived projects' : 'Portfolio'
  const done = visible.reduce((sum, project) => sum + (project.progress?.leaf_done ?? 0), 0)
  const total = visible.reduce((sum, project) => sum + (project.progress?.leaf_total ?? 0), 0)
  const blocked = visible.reduce((sum, project) => sum + (project.progress?.blocked ?? 0), 0)
  const ready = visible.reduce((sum, project) => sum + (project.progress?.ready ?? 0), 0)

  return (
    <div className="page portfolio-page">
      <header className="page-heading portfolio-heading">
        <div><p className="eyebrow">Research workspace</p><h1>{heading}</h1><p>{showTrash ? 'Restore a monitor here, or purge it later from the CLI after a verified backup.' : 'Plan the work, preserve the evidence, and always know the next meaningful step.'}</p></div>
        <Button onClick={onAdd}><FolderPlus size={17} />Add project</Button>
      </header>

      {!showArchived && !showTrash && <OptionalAutomationCard />}

      {!visible.length ? (
        <EmptyState
          icon={<Layers3 size={28} />}
          title={showTrash ? 'Trash is empty' : showArchived ? 'No archived projects' : 'Start with one research folder'}
          description={showTrash ? 'Removed monitors remain recoverable here until explicitly purged.' : showArchived ? 'Projects you archive will remain safely available here.' : 'Choose only the folder you want to monitor. The app will never modify its contents.'}
          action={!showArchived && !showTrash && <Button onClick={onAdd}><FolderPlus size={17} />Add your first project</Button>}
        />
      ) : (
        <>
          <section className="metric-grid portfolio-metrics" aria-label="Portfolio summary">
            <article className="metric-card"><span className="metric-icon sage"><Layers3 size={18} /></span><div><span>Active projects</span><strong>{visible.length}</strong></div></article>
            <article className="metric-card"><span className="metric-icon green"><CheckCircle2 size={18} /></span><div><span>Completed tasks</span><strong>{done}<small> / {total}</small></strong></div></article>
            <article className="metric-card"><span className="metric-icon blue"><Sparkles size={18} /></span><div><span>Ready next</span><strong>{ready}</strong></div></article>
            <article className="metric-card"><span className="metric-icon red"><Ban size={18} /></span><div><span>Blocked</span><strong>{blocked}</strong></div></article>
          </section>

          <div className="section-heading"><div><h2>Your projects</h2><p>Only folders you explicitly enrolled appear here.</p></div></div>
          <section className="project-card-grid">
            {visible.map((project) => {
              const progress = project.progress ?? { leaf_done: 0, leaf_total: 0, blocked: 0, ready: 0, waiting: 0, review: 0 }
              const percent = progress.leaf_total ? Math.round((progress.leaf_done / progress.leaf_total) * 100) : 0
              return (
                <Link to={`/projects/${project.id}/${showTrash ? 'settings' : 'overview'}`} className="project-card" key={project.id}>
                  <div className="project-card-accent" style={{ background: project.color }} />
                  <header><span className="project-avatar" style={{ background: `${project.color}20`, color: project.color }}>{project.name.slice(0, 2).toUpperCase()}</span><span className="project-card-arrow"><ArrowRight size={18} /></span></header>
                  <div className="project-card-title"><h3>{project.name}</h3>{project.unavailable && <Badge tone="red">Folder unavailable</Badge>}</div>
                  <p className="project-root">{shortPath(project.root_path)}</p>
                  <p className="project-description">{project.research_goal || project.description || 'No research goal added yet.'}</p>
                  <ProgressBar value={progress.leaf_done} max={progress.leaf_total} label={`${percent}% · ${progress.leaf_done} of ${progress.leaf_total} leaf tasks`} />
                  <footer>
                    <span className={progress.blocked ? 'danger-text' : ''}>{progress.blocked} blocked</span>
                    <span>{progress.ready} ready</span>
                    <span className="activity-time"><Clock3 size={13} />{relativeDate(project.last_manual_update || project.updated_at)}</span>
                  </footer>
                </Link>
              )
            })}
          </section>
        </>
      )}
    </div>
  )
}

export function OptionalAutomationCard() {
  const [copyStatus, setCopyStatus] = useState('')
  const skill = useQuery({ queryKey: ['skill-status'], queryFn: api.getSkillStatus, retry: false })
  const status = skill.data?.status ?? (skill.error ? 'blocked' : 'missing')
  const setupCommand = skill.data?.setup_command ?? skill.data?.command ?? (
    status === 'current'
      ? 'research-monitor skill status'
      : status === 'blocked'
        ? 'CODEX_HOME=/safe/codex-home research-monitor skill install'
        : status === 'missing'
          ? 'research-monitor skill install'
          : 'research-monitor skill update'
  )
  const statusLabel = status === 'current' ? 'Current' : status === 'missing' ? 'Missing' : status === 'modified' ? 'Modified' : status === 'outdated' ? 'Outdated' : 'Blocked'
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(setupCommand)
      setCopyStatus('Setup command copied.')
    } catch {
      setCopyStatus(`Copy was denied. Run: ${setupCommand}`)
    }
  }
  return <section className="optional-automation-card" aria-labelledby="optional-automation-heading">
    <span className="optional-automation-icon"><Bot size={20} /></span>
    <div className="optional-automation-copy">
      <div className="proposal-title-line"><h2 id="optional-automation-heading">Optional Codex automation</h2><Badge tone={status === 'current' ? 'green' : status === 'blocked' ? 'red' : 'amber'}>{skill.isPending ? 'Checking…' : statusLabel}</Badge></div>
      <p>Research Monitor works fully without this companion skill. Install or update it only if you want Codex to prepare review-only proposals.</p>
      {skill.data?.blocking_reason && <small>{skill.data.blocking_reason}</small>}
      {skill.error && <small>Skill status could not be checked. Manual planning, graphs, journals, artifacts, and recovery remain available.</small>}
      {skill.data?.destination && <small>Optional destination: <code>{skill.data.destination}</code></small>}
    </div>
    <div className="optional-automation-action">
      <code>{setupCommand}</code>
      {status !== 'current' && <small>Stop Research Monitor before running this command, then restart it.</small>}
      <Button type="button" size="sm" variant="secondary" onClick={copy}>{copyStatus.startsWith('Setup command copied') ? <Check size={14} /> : <Clipboard size={14} />}Copy command</Button>
      <span aria-live="polite">{copyStatus}</span>
    </div>
  </section>
}

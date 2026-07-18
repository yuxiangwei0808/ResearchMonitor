import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Ban, CheckCircle2, CircleDot, Clock3, GitBranch, ListChecks, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { ProjectSnapshot } from '../types'
import { TASK_OUTCOMES, TASK_STATUSES } from '../types'
import { api } from '../lib/api'
import { formatCalendarDate, humanize, relativeDate, statusTone } from '../lib/format'
import { Badge, Button, EmptyState, ErrorState, ProgressBar } from '../components/ui'

export function OverviewView({ snapshot, copyPrompt }: { snapshot: ProjectSnapshot; copyPrompt: () => void }) {
  const { project, progress, pipelines, tasks } = snapshot
  const history = useQuery({ queryKey: ['history', project.id], queryFn: () => api.getHistory(project.id) })
  const activePipelineIds = new Set(pipelines.filter((pipeline) => !pipeline.deleted_at && !pipeline.archived).map((pipeline) => pipeline.id))
  const activeTasks = tasks.filter((task) => !task.deleted_at && task.status !== 'dropped' && activePipelineIds.has(task.pipeline_id))
  const focusTasks = activeTasks.filter((task) => task.status !== 'done' && (task.readiness === 'ready' || task.status === 'in_progress'))
  const percentage = progress.leaf_total ? Math.round((progress.leaf_done / progress.leaf_total) * 100) : 0
  const statusBreakdown = TASK_STATUSES.map((status) => ({ status, count: progress.by_status?.[status] ?? activeTasks.filter((task) => task.status === status).length }))
  const outcomeBreakdown = TASK_OUTCOMES.map((outcome) => ({ outcome, count: progress.by_outcome?.[outcome] ?? activeTasks.filter((task) => task.outcome === outcome).length }))
  const quickViews = [
    { name: 'Ready', value: progress.ready, icon: Sparkles, tone: 'blue', filter: 'ready' },
    { name: 'In review', value: progress.review, icon: CircleDot, tone: 'amber', filter: 'review' },
    { name: 'Blocked', value: progress.blocked, icon: Ban, tone: 'red', filter: 'blocked' },
    { name: 'Completed', value: progress.leaf_done, icon: CheckCircle2, tone: 'green', filter: 'done' },
  ]
  if (!pipelines.length && !tasks.length) {
    return (
      <div className="view-page">
        <section className="goal-card"><p className="eyebrow">Research goal</p><h2>{project.research_goal || 'Define what success means for this project'}</h2><p>{project.success_criteria || 'Add a research goal and success criteria in Settings, then create the first pipeline or ask Codex to draft a proposal.'}</p></section>
        <EmptyState icon={<GitBranch size={30} />} title="Your project monitor is ready" description="Create the structure yourself or ask Codex to inspect the enrolled folder and submit a draft for review." action={<div className="button-row"><Link to={`/projects/${project.id}/outline?action=new-pipeline`} className="button button-primary button-md"><GitBranch size={17} />Create pipeline</Link><Link to={`/projects/${project.id}/outline?action=new-task`} className="button button-secondary button-md"><ListChecks size={17} />Create task</Link><Button variant="secondary" onClick={copyPrompt}><Sparkles size={17} />Ask Codex to initialize</Button></div>} />
      </div>
    )
  }
  return (
    <div className="view-page overview-grid">
      <section className="goal-card span-full">
        <div><p className="eyebrow">Research goal</p><h2>{project.research_goal || project.description || 'No research goal recorded yet'}</h2>{project.success_criteria && <p><strong>Success:</strong> {project.success_criteria}</p>}</div>
        <div className="completion-orbit" style={{ '--progress': `${percentage * 3.6}deg` } as React.CSSProperties}><span><strong>{percentage}%</strong><small>complete</small></span></div>
      </section>
      <section className="metric-grid quick-metrics span-full">
        {quickViews.map(({ name, value, icon: Icon, tone, filter }) => <Link key={name} to={`/projects/${project.id}/outline?view=${filter}`} className="metric-card"><span className={`metric-icon ${tone}`}><Icon size={18} /></span><div><span>{name}</span><strong>{value}</strong></div><ArrowRight size={16} /></Link>)}
      </section>
      <section className="panel progress-breakdown-panel span-full" aria-labelledby="progress-breakdown-heading">
        <header className="panel-header"><div><p className="eyebrow">Recorded progress</p><h2 id="progress-breakdown-heading">Workflow status and research outcomes</h2></div></header>
        <div className="breakdown-columns">
          <div><h3>Workflow status</h3><dl>{statusBreakdown.map(({ status, count }) => <div key={status}><dt><Badge tone={statusTone[status]}>{humanize(status)}</Badge></dt><dd>{count}</dd></div>)}</dl></div>
          <div><h3>Research outcome</h3><dl>{outcomeBreakdown.map(({ outcome, count }) => <div key={outcome}><dt>{humanize(outcome)}</dt><dd>{count}</dd></div>)}</dl></div>
        </div>
      </section>
      <section className="panel pipelines-panel">
        <header className="panel-header"><div><p className="eyebrow">Structure</p><h2>Pipelines</h2></div><Link to={`/projects/${project.id}/outline`} className="text-link">Open outline <ArrowRight size={14} /></Link></header>
        <div className="pipeline-summary-list">
          {pipelines.filter((pipeline) => !pipeline.deleted_at && !pipeline.archived).map((pipeline) => {
            const pipelineTasks = activeTasks.filter((task) => task.pipeline_id === pipeline.id)
            const leaves = pipelineTasks.filter((task) => !pipelineTasks.some((child) => child.parent_id === task.id))
            const done = leaves.filter((task) => task.status === 'done').length
            return <Link key={pipeline.id} to={`/projects/${project.id}/outline#${pipeline.id}`} className="pipeline-summary"><div className="pipeline-summary-top"><span><GitBranch size={15} />{pipeline.title}</span><Badge tone={pipeline.flow_mode === 'sequential' ? 'blue' : 'neutral'}>{humanize(pipeline.flow_mode)}</Badge></div><ProgressBar value={done} max={leaves.length} label={`${done} / ${leaves.length} leaf tasks`} /></Link>
          })}
        </div>
      </section>
      <section className="panel activity-panel">
        <header className="panel-header"><div><p className="eyebrow">Recorded history</p><h2>Recent activity</h2></div><Link to={`/projects/${project.id}/activity`} className="text-link">View all <ArrowRight size={14} /></Link></header>
        <div className="activity-list compact">
          {history.error ? <ErrorState error={history.error} retry={() => history.refetch()} /> : <>{(history.data ?? []).slice(0, 6).map((event) => <article key={event.id} className="activity-item"><span className="activity-dot" /><div><p>{event.summary}</p><small>{event.actor_label || humanize(event.actor_type)} · {relativeDate(event.created_at)}</small></div></article>)}
          {!history.isLoading && !history.data?.length && <div className="mini-empty"><Clock3 size={20} /><p>No recorded activity yet.</p></div>}</>}
        </div>
      </section>
      <section className="panel task-focus-panel span-full">
        <header className="panel-header"><div><p className="eyebrow">Current focus</p><h2>Ready and active tasks</h2></div></header>
        <div className="focus-task-grid">
          {focusTasks.slice(0, 8).map((task) => <Link key={task.id} to={`/projects/${project.id}/outline?task=${task.id}`} className="focus-task"><div><Badge tone={statusTone[task.status]}>{humanize(task.status)}</Badge>{task.user_key && <span className="task-key">{task.user_key}</span>}</div><strong>{task.title}</strong><small>{task.target_date ? `Target ${formatCalendarDate(task.target_date)}` : humanize(task.priority)}</small></Link>)}
          {!focusTasks.length && <div className="mini-empty"><CheckCircle2 size={20} /><p>No ready or active tasks right now.</p></div>}
        </div>
      </section>
    </div>
  )
}

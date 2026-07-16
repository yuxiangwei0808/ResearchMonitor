import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { useForm } from 'react-hook-form'
import { Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { z } from 'zod'
import { Archive, Beaker, ChevronRight, FolderPlus, LayoutDashboard, Plus, Search, Settings2, Trash2 } from 'lucide-react'
import { api } from './lib/api'
import { useOutboxReplay } from './lib/hooks'
import { shortPath } from './lib/format'
import type { Project } from './types'
import { Button, Dialog, ErrorState, Field, Spinner } from './components/ui'
import { PortfolioView } from './views/PortfolioView'
import { ProjectWorkspace } from './views/ProjectWorkspace'

const projectSchema = z.object({
  name: z.string().trim().min(1, 'Enter a project name.'),
  root_path: z.string().trim().min(1, 'Enter the project folder.').refine((path) => path.startsWith('/'), 'Use an absolute Linux path.'),
  description: z.string(),
  research_goal: z.string(),
  color: z.string().regex(/^#[0-9a-fA-F]{6}$/, 'Choose a valid project color.'),
})

type ProjectDraft = z.infer<typeof projectSchema>
const projectDefaults: ProjectDraft = { name: '', root_path: '', description: '', research_goal: '', color: '#5c6e48' }

function AddProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate()
  const client = useQueryClient()
  const { register, handleSubmit, reset, watch, formState: { errors } } = useForm<ProjectDraft>({
    resolver: zodResolver(projectSchema),
    defaultValues: projectDefaults,
  })
  const mutation = useMutation({
    mutationFn: (form: ProjectDraft) => api.createProject(form),
    onSuccess: async (project) => {
      await client.invalidateQueries({ queryKey: ['projects'] })
      onClose()
      reset(projectDefaults)
      navigate(`/projects/${project.id}/overview`)
    },
  })
  const close = () => {
    mutation.reset()
    reset(projectDefaults)
    onClose()
  }
  return (
    <Dialog open={open} onClose={close} title="Add a research project" description="Only this folder will be enrolled. Research Monitor never changes its files.">
      <form onSubmit={handleSubmit((form) => mutation.mutate(form))} className="form-stack" noValidate>
        <Field label="Project name"><input {...register('name')} aria-invalid={Boolean(errors.name)} placeholder="Brain representation study" />{errors.name && <small className="field-error">{errors.name.message}</small>}</Field>
        <Field label="Project folder" hint="Use an absolute folder path under an allowed workspace root."><input {...register('root_path')} aria-invalid={Boolean(errors.root_path)} placeholder="/home/me/research/my-project" spellCheck={false} />{errors.root_path && <small className="field-error">{errors.root_path.message}</small>}</Field>
        <Field label="Research goal"><textarea {...register('research_goal')} rows={3} placeholder="What does this project aim to establish?" /></Field>
        <Field label="Description"><textarea {...register('description')} rows={2} placeholder="Optional context" /></Field>
        <Field label="Project color"><div className="color-input"><input type="color" {...register('color')} /><span>{watch('color')}</span></div>{errors.color && <small className="field-error">{errors.color.message}</small>}</Field>
        {mutation.error && <div className="inline-error">{mutation.error.message}</div>}
        <div className="dialog-actions"><Button type="button" variant="ghost" onClick={close}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? 'Adding…' : 'Add project'}</Button></div>
      </form>
    </Dialog>
  )
}

function Shell({ projects, onAdd }: { projects: Project[]; onAdd: () => void }) {
  const location = useLocation()
  const [searchOpen, setSearchOpen] = useState(false)
  const active = projects.filter((project) => !project.archived && !project.trashed)
  const archived = projects.filter((project) => project.archived && !project.trashed)
  const trashed = projects.filter((project) => project.trashed)
  return (
    <div className="app-shell min-h-screen">
      <aside className="sidebar">
        <NavLink to="/" className="brand" aria-label="Research Monitor home">
          <span className="brand-mark"><Beaker size={20} /></span>
          <span><strong>Research</strong><small>Monitor</small></span>
        </NavLink>
        <nav className="sidebar-nav">
          <NavLink to="/" end className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><LayoutDashboard size={17} />Portfolio</NavLink>
          <button className="nav-item" onClick={() => setSearchOpen(!searchOpen)}><Search size={17} />Find a project</button>
        </nav>
        {searchOpen && <ProjectSearch projects={active} onDone={() => setSearchOpen(false)} />}
        <div className="sidebar-section-heading"><span>Projects</span><Button size="icon" variant="ghost" onClick={onAdd} aria-label="Add project"><Plus size={16} /></Button></div>
        <nav className="project-list" aria-label="Projects">
          {active.map((project) => (
            <NavLink key={project.id} to={`/projects/${project.id}/overview`} className={({ isActive }) => `project-link ${isActive || location.pathname.includes(project.id) ? 'active' : ''}`}>
              <span className="project-dot" style={{ background: project.color }} />
              <span className="project-link-copy"><strong>{project.name}</strong><small>{shortPath(project.root_path, 28)}</small></span>
              {project.unavailable && <span className="warning-dot" title="Folder unavailable" />}
            </NavLink>
          ))}
          {!active.length && <p className="sidebar-empty">No projects enrolled</p>}
        </nav>
        {archived.length > 0 && <NavLink className="archive-link" to="/?show=archived"><Archive size={15} />{archived.length} archived</NavLink>}
        {trashed.length > 0 && <NavLink className="archive-link" to="/?show=trash"><Trash2 size={15} />{trashed.length} in trash</NavLink>}
        <div className="sidebar-footer"><Settings2 size={14} /><span>Local only · v0.1</span></div>
      </aside>
      <main className="main-canvas"><Outlet /></main>
    </div>
  )
}

function ProjectSearch({ projects, onDone }: { projects: Project[]; onDone: () => void }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const results = projects.filter((project) => `${project.name} ${project.root_path}`.toLowerCase().includes(query.toLowerCase()))
  return (
    <div className="sidebar-search">
      <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search projects…" />
      {query && <div className="sidebar-search-results">{results.map((project) => <button key={project.id} onClick={() => { navigate(`/projects/${project.id}/overview`); onDone() }}><span className="project-dot" style={{ background: project.color }} />{project.name}<ChevronRight size={14} /></button>)}</div>}
    </div>
  )
}

export default function App() {
  useOutboxReplay()
  const [addOpen, setAddOpen] = useState(false)
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: () => api.listProjects(true, true) })
  if (projectsQuery.isLoading) return <div className="center-screen"><Spinner label="Opening your research workspace…" /></div>
  if (projectsQuery.error) return <div className="center-screen"><ErrorState error={projectsQuery.error} retry={() => projectsQuery.refetch()} /></div>
  const projects = projectsQuery.data ?? []
  return (
    <>
      <Routes>
        <Route element={<Shell projects={projects} onAdd={() => setAddOpen(true)} />}>
          <Route index element={<PortfolioView projects={projects} onAdd={() => setAddOpen(true)} />} />
          <Route path="projects/:projectId/:view?" element={<ProjectWorkspace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <AddProjectDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </>
  )
}

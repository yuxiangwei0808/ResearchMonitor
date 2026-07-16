import type { Pipeline, Task } from '../types'

function taskName(task: Task) {
  return task.user_key ? `${task.user_key} · ${task.title}` : task.title
}

/**
 * Builds stable, human-readable labels for task pickers without changing task identity.
 * The full pipeline and ancestor path keeps duplicate keys and titles distinguishable.
 */
export function createTaskLabeler(pipelines: Pipeline[], tasks: Task[]) {
  const pipelineById = new Map(pipelines.map((pipeline) => [pipeline.id, pipeline]))
  const taskById = new Map(tasks.map((task) => [task.id, task]))
  const cache = new Map<string, string>()

  return (task?: Task): string => {
    if (!task) return 'Unavailable task'
    const cached = cache.get(task.id)
    if (cached) return cached

    const ancestors: Task[] = []
    const visited = new Set([task.id])
    let parentId = task.parent_id
    while (parentId && !visited.has(parentId)) {
      visited.add(parentId)
      const parent = taskById.get(parentId)
      if (!parent) break
      ancestors.unshift(parent)
      parentId = parent.parent_id
    }

    const pipeline = pipelineById.get(task.pipeline_id)
    const label = [pipeline?.title ?? 'Unavailable pipeline', ...ancestors.map(taskName), taskName(task)].join(' › ')
    cache.set(task.id, label)
    return label
  }
}

export function taskPickerSearchText(task: Task, labelTask: (task?: Task) => string) {
  return `${labelTask(task)} ${task.labels.join(' ')}`.toLowerCase()
}

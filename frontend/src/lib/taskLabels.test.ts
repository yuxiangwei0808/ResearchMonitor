import { describe, expect, it } from 'vitest'
import type { Pipeline, Task } from '../types'
import { createTaskLabeler, taskPickerSearchText } from './taskLabels'

const pipeline = { id: 'pipeline', title: 'Evaluation', position: 0 } as Pipeline
const task = (id: string, title: string, parentId: string | null, userKey?: string): Task => ({
  id,
  project_id: 'project',
  pipeline_id: pipeline.id,
  parent_id: parentId,
  user_key: userKey,
  title,
  kind: 'task',
  status: 'planned',
  priority: 'required',
  labels: id === 'leaf' ? ['benchmark'] : [],
  position: 0,
  child_flow_mode: 'freeform',
  readiness: 'ready',
  unsatisfied_predecessor_ids: [],
  version: 1,
})

describe('task picker labels', () => {
  it('includes the pipeline, complete ancestor path, key, and title', () => {
    const root = task('root', 'Prepare data', null, 'DATA')
    const child = task('child', 'Validate inputs', root.id)
    const leaf = task('leaf', 'Check manifest', child.id, 'QC-1')
    const labelTask = createTaskLabeler([pipeline], [root, child, leaf])

    expect(labelTask(leaf)).toBe('Evaluation › DATA · Prepare data › Validate inputs › QC-1 · Check manifest')
    expect(taskPickerSearchText(leaf, labelTask)).toContain('evaluation › data · prepare data')
    expect(taskPickerSearchText(leaf, labelTask)).toContain('benchmark')
  })

  it('terminates safely for malformed cyclic or missing ancestry', () => {
    const left = task('left', 'Left', 'right')
    const right = task('right', 'Right', 'left')
    const orphan = task('orphan', 'Orphan', 'missing')
    const labelTask = createTaskLabeler([], [left, right, orphan])

    expect(labelTask(left)).toBe('Unavailable pipeline › Right › Left')
    expect(labelTask(orphan)).toBe('Unavailable pipeline › Orphan')
    expect(labelTask()).toBe('Unavailable task')
  })
})

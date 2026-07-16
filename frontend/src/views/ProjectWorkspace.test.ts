import { describe, expect, it } from 'vitest'
import { snapshotSections } from './ProjectWorkspace'

describe('ProjectWorkspace snapshot sections', () => {
  it('loads pipelines for contextual task labels in the Artifacts view', () => {
    expect(snapshotSections.artifacts).toContain('pipelines')
  })
})

import { describe, expect, it } from 'vitest'
import { bytes, humanize, shortPath } from './format'
import { operation } from './api'

describe('display formatting', () => {
  it('humanizes API enum values', () => {
    expect(humanize('in_progress')).toBe('In Progress')
    expect(humanize('external_run')).toBe('External Run')
  })

  it('keeps short paths intact and abbreviates deep paths', () => {
    expect(shortPath('/home/research')).toBe('/home/research')
    expect(shortPath('/home/user/projects/brain/results/metrics.json', 30)).toBe('…/brain/results/metrics.json')
  })

  it('formats artifact sizes', () => {
    expect(bytes(900)).toBe('900 B')
    expect(bytes(1536)).toBe('1.5 KB')
  })
})

describe('mutation operation construction', () => {
  it('keeps entity concurrency information separate from operation data', () => {
    const result = operation('task.update', { title: 'Updated' }, { id: 'd0cb8423-4539-4ab9-b1e5-45c46666fd02', version: 4 })
    expect(result.type).toBe('task.update')
    expect(result.entity_id).toBe('d0cb8423-4539-4ab9-b1e5-45c46666fd02')
    expect(result.expected_version).toBe(4)
    expect(result.data).toEqual({ title: 'Updated' })
    expect(result.id).toMatch(/^[0-9a-f-]{36}$/)
  })
})

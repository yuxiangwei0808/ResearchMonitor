/** @vitest-environment jsdom */

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SafeMarkdown } from './SafeMarkdown'

afterEach(cleanup)

describe('SafeMarkdown', () => {
  it('renders useful Markdown without creating executable project HTML', () => {
    const rendered = render(<SafeMarkdown value={'## Result notes\n\n**Done** with `metric=0.91`.\n\n<script>alert(1)</script>'} />)
    expect(screen.getByRole('heading', { name: 'Result notes' })).toBeTruthy()
    expect(screen.getByText('Done')).toBeTruthy()
    expect(screen.getByText('metric=0.91')).toBeTruthy()
    expect(rendered.container.querySelector('script')).toBeNull()
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy()
  })

  it('links only HTTP(S) destinations and marks them as isolated external links', () => {
    render(<SafeMarkdown value={'[safe](https://example.org/result) and [unsafe](javascript:evil)'} />)
    const safe = screen.getByRole('link', { name: 'safe' })
    expect(safe.getAttribute('href')).toBe('https://example.org/result')
    expect(safe.getAttribute('target')).toBe('_blank')
    expect(safe.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.queryByRole('link', { name: 'unsafe' })).toBeNull()
    expect(screen.getByText('unsafe (javascript:evil)')).toBeTruthy()
  })
})

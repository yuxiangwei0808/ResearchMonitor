/** @vitest-environment jsdom */

import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SystemBanners } from './App'

afterEach(cleanup)

describe('SystemBanners', () => {
  it('waits for two transport failures, then clears the disconnected banner after success', () => {
    render(<SystemBanners />)
    act(() => window.dispatchEvent(new CustomEvent('research-monitor:transport-failure')))
    expect(screen.queryByText('Research Monitor is disconnected')).toBeNull()
    act(() => window.dispatchEvent(new CustomEvent('research-monitor:transport-failure')))
    expect(screen.getByText('Research Monitor is disconnected')).not.toBeNull()
    expect(screen.getByRole('status').textContent).toContain('Research Monitor is disconnected')
    expect(screen.queryByRole('alert')).toBeNull()
    act(() => window.dispatchEvent(new CustomEvent('research-monitor:request-success')))
    expect(screen.queryByText('Research Monitor is disconnected')).toBeNull()
  })

  it('shows authentication failures immediately and does not hide them after an unrelated success', () => {
    render(<SystemBanners />)
    act(() => window.dispatchEvent(new CustomEvent('research-monitor:authentication-required')))
    expect(screen.getByRole('alert').textContent).toContain('Re-authenticate this browser')
    act(() => window.dispatchEvent(new CustomEvent('research-monitor:request-success')))
    expect(screen.getByRole('alert').textContent).toContain('Re-authenticate this browser')

  })
  it('announces proposal activity and exposes keyboard-reachable review and dismissal controls', () => {
    render(<SystemBanners />)
    act(() => window.dispatchEvent(new CustomEvent('research-monitor:proposal-update', {
      detail: {
        id: 42,
        project_id: '11111111-1111-4111-8111-111111111111',
        event_type: 'proposal.created',
      },
    })))
    const announcement = screen.getByRole('status')
    expect(announcement.getAttribute('aria-live')).toBe('polite')
    expect(announcement.textContent).toContain('Proposal activity recorded')
    expect(screen.getByRole('link', { name: 'Review' }).getAttribute('href')).toBe('/projects/11111111-1111-4111-8111-111111111111/proposals')
    const dismiss = screen.getByRole('button', { name: 'Dismiss proposal notification' })
    dismiss.focus()
    expect(document.activeElement).toBe(dismiss)
    dismiss.click()
  })
})

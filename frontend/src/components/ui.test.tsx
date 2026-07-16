/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Dialog, Notice, ProgressBar, Spinner, ViewErrorBoundary } from './ui'

afterEach(cleanup)

describe('Dialog', () => {
  it('exposes an accessible modal, focuses inside, and closes with Escape', async () => {
    const close = vi.fn()
    render(<Dialog open onClose={close} title="Edit task" description="Change task fields"><button>Save</button></Dialog>)

    const dialog = screen.getByRole('dialog', { name: 'Edit task', description: 'Change task fields' })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('provides a keyboard-reachable close action', async () => {
    const close = vi.fn()
    render(<Dialog open onClose={close} title="Keyboard dialog"><button>Save</button></Dialog>)
    const closeButton = screen.getByRole('button', { name: 'Close dialog' })
    expect(closeButton.tabIndex).toBeGreaterThanOrEqual(0)
    closeButton.focus()
    expect(document.activeElement).toBe(closeButton)
    fireEvent.click(closeButton)
    expect(close).toHaveBeenCalledTimes(1)
  })
})

describe('shared accessibility primitives', () => {
  it('announces progress, loading, and errors with native semantics', () => {
    render(<><ProgressBar value={3} max={5} label="3 of 5 tasks" /><Spinner label="Refreshing project" /><Notice tone="danger">Conflict detected</Notice></>)
    const progress = screen.getByRole('progressbar', { name: '3 of 5 tasks' })
    expect(progress.getAttribute('aria-valuenow')).toBe('3')
    expect(progress.getAttribute('aria-valuemax')).toBe('5')
    expect(screen.getByRole('status', { name: '' }).textContent).toContain('Refreshing project')
    expect(screen.getByRole('alert').textContent).toContain('Conflict detected')
  })

  it('contains rendering failures to the current view', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const Broken = () => { throw new Error('Broken view') }
    render(<ViewErrorBoundary resetKey="project:overview"><Broken /></ViewErrorBoundary>)
    expect(screen.getByRole('alert').textContent).toContain('Broken view')
    consoleError.mockRestore()
  })
})

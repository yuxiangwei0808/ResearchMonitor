/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '../lib/api'
import { PortfolioView } from './PortfolioView'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function renderPortfolio() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<MemoryRouter><QueryClientProvider client={client}><PortfolioView projects={[]} onAdd={() => undefined} /></QueryClientProvider></MemoryRouter>)
}

describe('Portfolio optional Codex automation', () => {
  it.each([
    ['current', 'Current'],
    ['missing', 'Missing'],
    ['modified', 'Modified'],
    ['outdated', 'Outdated'],
    ['blocked', 'Blocked'],
  ] as const)('shows %s as a passive optional integration state', async (status, label) => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({
      status,
      optional: true,
      ...(status === 'blocked' ? {} : { setup_command: status === 'missing' ? 'research-monitor skill install' : 'research-monitor skill update' }),
      ...(status === 'blocked' ? { blocking_reason: 'Choose a safe CODEX_HOME.' } : {}),
    })
    renderPortfolio()

    expect(await screen.findByRole('heading', { name: 'Optional Codex automation' })).not.toBeNull()
    expect(await screen.findByText(label, { exact: true })).not.toBeNull()
    expect(screen.getByText(/works fully without this companion skill/i)).not.toBeNull()
    expect(screen.queryByRole('button', { name: /install/i })).toBeNull()
    if (status !== 'current') expect(screen.getByText('Stop Research Monitor before running this command, then restart it.')).not.toBeNull()
    if (status === 'blocked') expect(screen.getByText('CODEX_HOME=/safe/codex-home research-monitor skill install')).not.toBeNull()
  })

  it('copies only the explicit CLI setup command', async () => {
    vi.spyOn(api, 'getSkillStatus').mockResolvedValue({ status: 'missing', optional: true, setup_command: 'research-monitor skill install' })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    renderPortfolio()
    fireEvent.click(await screen.findByRole('button', { name: 'Copy command' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('research-monitor skill install'))
    expect(screen.getByText('Setup command copied.')).not.toBeNull()
  })
})

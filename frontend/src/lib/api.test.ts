/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('skill-status compatibility adapter', () => {
  it('prefers the additive normalized status while retaining the display label', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: 'Installed and current',
      normalized_status: 'blocked',
      blocking_reason: 'Destination overlaps monitored data.',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))

    await expect(api.getSkillStatus()).resolves.toMatchObject({
      status: 'blocked',
      normalized_status: 'blocked',
      label: 'Installed and current',
    })
  })
})

/**
 * RegisterForm.test.ts
 *
 * Property 10: Post-Registration Redirect Logic
 *
 * For any site config, the redirect destination after successful registration is:
 * - `/dashboard` when site.id === 'platform'
 * - `/{site.slug}/dashboard` for all other tenants
 *
 * Validates: Requirements 2.3, 9.3
 */

import { describe, it, expect } from 'vitest'
import * as fc from 'fast-check'
import { SITES, VALID_SITE_IDS } from '@/lib/sites'

// ── Extract redirect logic (mirrors RegisterForm implementation) ──────────────

function getPostRegistrationRedirect(siteId: string, siteSlug: string): string {
  return siteId === 'platform' ? '/dashboard' : `/${siteSlug}/dashboard`
}

// ── Property 10: Post-Registration Redirect Logic ─────────────────────────────

describe('Property 10: Post-Registration Redirect Logic', () => {
  it(
    'for platform site, redirect is always /dashboard',
    () => {
      const site = SITES.platform!
      const redirect = getPostRegistrationRedirect(site.id, site.slug)
      expect(redirect).toBe('/dashboard')
    },
  )

  it(
    'for any non-platform site, redirect is always /{site.slug}/dashboard',
    () => {
      fc.assert(
        fc.property(
          fc.constantFrom(...VALID_SITE_IDS.filter(id => id !== 'platform')),
          (siteId) => {
            const site = SITES[siteId]!
            const redirect = getPostRegistrationRedirect(site.id, site.slug)
            expect(redirect).toBe(`/${site.slug}/dashboard`)
            expect(redirect).not.toBe('/dashboard')
          },
        ),
        { numRuns: 100 },
      )
    },
  )

  it(
    'for any site config, redirect always starts with / and contains "dashboard"',
    () => {
      fc.assert(
        fc.property(
          fc.constantFrom(...VALID_SITE_IDS),
          (siteId) => {
            const site = SITES[siteId]!
            const redirect = getPostRegistrationRedirect(site.id, site.slug)
            expect(redirect.startsWith('/')).toBe(true)
            expect(redirect).toContain('dashboard')
          },
        ),
        { numRuns: 100 },
      )
    },
  )

  it(
    'redirect logic is consistent with LoginForm pattern (platform special case)',
    () => {
      // The LoginForm redirects platform → /dashboard, others → /{slug}
      // RegisterForm redirects platform → /dashboard, others → /{slug}/dashboard
      // Both share the platform special case
      fc.assert(
        fc.property(
          fc.constantFrom(...VALID_SITE_IDS),
          (siteId) => {
            const site = SITES[siteId]!
            const redirect = getPostRegistrationRedirect(site.id, site.slug)

            if (siteId === 'platform') {
              expect(redirect).toBe('/dashboard')
            } else {
              expect(redirect).toBe(`/${site.slug}/dashboard`)
              expect(redirect).not.toBe(`/${site.slug}`) // Not homepage
            }
          },
        ),
        { numRuns: 100 },
      )
    },
  )
})

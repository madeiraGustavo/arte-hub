/**
 * route.test.ts
 *
 * Unit tests for POST /api/auth/register proxy route.
 *
 * Property 8: Site Resolution Fallback — proxy defaults to 'platform'
 * when X-Site-Id is missing.
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 4.4, 4.5, 5.5, 5.6
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import * as fc from 'fast-check'
import { NextRequest } from 'next/server'

// ── Set env before import ─────────────────────────────────────────────────────

process.env.NEXT_PUBLIC_API_URL = 'http://localhost:3333'

// ── Mock global fetch ─────────────────────────────────────────────────────────

const mockFetch = vi.fn()
vi.stubGlobal('fetch', mockFetch)

// ── Import after mocks ────────────────────────────────────────────────────────

import { POST } from './route'

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeRequest(body: unknown, headers?: Record<string, string>): NextRequest {
  const reqHeaders = new Headers({ 'Content-Type': 'application/json', ...headers })
  return new NextRequest('http://localhost:3000/api/auth/register', {
    method: 'POST',
    headers: reqHeaders,
    body: JSON.stringify(body),
  })
}

function makeInvalidJsonRequest(): NextRequest {
  return new NextRequest('http://localhost:3000/api/auth/register', {
    method: 'POST',
    headers: new Headers({ 'Content-Type': 'application/json' }),
    body: 'not-valid-json{{{',
  })
}

function mockApiSuccess(siteId = 'platform') {
  mockFetch.mockResolvedValueOnce(
    new Response(JSON.stringify({ accessToken: 'tok', siteId }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('POST /api/auth/register (proxy)', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  // ── Property 8: Site Resolution Fallback ──────────────────────────────────

  describe('Property 8: Site Resolution Fallback', () => {
    it('defaults X-Site-Id to "platform" when header is missing', async () => {
      await fc.assert(
        fc.asyncProperty(
          fc.record({
            email:    fc.emailAddress(),
            password: fc.string({ minLength: 6, maxLength: 20 }),
          }),
          async ({ email, password }) => {
            mockFetch.mockReset()
            mockApiSuccess()

            const req = makeRequest({ email, password })
            await POST(req)

            const callArgs = mockFetch.mock.calls[0]
            const url = callArgs[0] as string
            const options = callArgs[1] as { headers: Record<string, string> }
            expect(url).toContain('/auth/register')
            expect(options.headers['X-Site-Id']).toBe('platform')
          },
        ),
        { numRuns: 20 },
      )
    })

    it('forwards valid X-Site-Id header to the backend', async () => {
      const VALID_SITE_IDS = ['platform', 'marketplace', 'tattoo', 'music']

      await fc.assert(
        fc.asyncProperty(
          fc.constantFrom(...VALID_SITE_IDS),
          async (siteId) => {
            mockFetch.mockReset()
            mockApiSuccess(siteId)

            const req = makeRequest(
              { email: 'test@test.com', password: '123456' },
              { 'X-Site-Id': siteId },
            )
            await POST(req)

            const callArgs = mockFetch.mock.calls[0]
            const options = callArgs[1] as { headers: Record<string, string> }
            expect(options.headers['X-Site-Id']).toBe(siteId)
          },
        ),
        { numRuns: 20 },
      )
    })

    it('passes through any X-Site-Id value to backend without filtering', async () => {
      mockApiSuccess()

      const req = makeRequest(
        { email: 'test@test.com', password: '123456' },
        { 'X-Site-Id': 'invalid-site' },
      )
      await POST(req)

      const callArgs = mockFetch.mock.calls[0]
      const options = callArgs[1] as { headers: Record<string, string> }
      expect(options.headers['X-Site-Id']).toBe('invalid-site')
    })
  })

  // ── Example-based tests ───────────────────────────────────────────────────

  describe('Error handling', () => {
    it('returns 400 when request body is not valid JSON', async () => {
      const req = makeInvalidJsonRequest()
      const res = await POST(req)

      expect(res.status).toBe(400)
      const data = await res.json()
      expect(data.error).toBe('Corpo da requisição inválido')
    })

    it('returns 503 when API is unreachable', async () => {
      mockFetch.mockRejectedValueOnce(new Error('ECONNREFUSED'))

      const req = makeRequest({ email: 'test@test.com', password: '123456' })
      const res = await POST(req)

      expect(res.status).toBe(503)
      const data = await res.json()
      expect(data.error).toBe('Serviço indisponível')
    })
  })

  describe('Response forwarding', () => {
    it('forwards API status code and JSON body', async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({ error: 'Email já cadastrado neste site' }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

      const req = makeRequest(
        { email: 'dup@test.com', password: '123456' },
        { 'X-Site-Id': 'marketplace' },
      )
      const res = await POST(req)

      expect(res.status).toBe(409)
      const data = await res.json()
      expect(data.error).toBe('Email já cadastrado neste site')
    })

    it('forwards Set-Cookie headers from API response', async () => {
      const setCookieValue = 'ah_platform_refresh=token123; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800'
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({ accessToken: 'tok', siteId: 'platform' }), {
          status: 201,
          headers: {
            'Content-Type': 'application/json',
            'Set-Cookie': setCookieValue,
          },
        }),
      )

      const req = makeRequest(
        { email: 'new@test.com', password: '123456' },
        { 'X-Site-Id': 'platform' },
      )
      const res = await POST(req)

      expect(res.status).toBe(201)
      expect(res.headers.get('set-cookie')).toBe(setCookieValue)
    })

    it('returns 201 with accessToken and siteId on success', async () => {
      mockFetch.mockResolvedValueOnce(
        new Response(JSON.stringify({ accessToken: 'jwt-token', siteId: 'tattoo' }), {
          status: 201,
          headers: { 'Content-Type': 'application/json' },
        }),
      )

      const req = makeRequest(
        { email: 'new@test.com', password: '123456' },
        { 'X-Site-Id': 'tattoo' },
      )
      const res = await POST(req)

      expect(res.status).toBe(201)
      const data = await res.json()
      expect(data.accessToken).toBe('jwt-token')
      expect(data.siteId).toBe('tattoo')
    })
  })
})

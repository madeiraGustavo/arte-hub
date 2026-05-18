/**
 * middleware.ts
 *
 * Multi-tenant auth middleware.
 *
 * Fluxo: resolveSite() → checkAuth() → route
 *
 * Defesa em profundidade — verifica presença do cookie de refresh (HttpOnly)
 * como proxy de autenticação. O cookie é gerenciado pela API Fastify e só existe
 * quando o usuário tem uma sessão ativa.
 *
 * Cookies são isolados por tenant:
 * - ah_platform_refresh
 * - ah_marketplace_refresh
 * - ah_tattoo_refresh
 * - ah_music_refresh
 *
 * O cookie legado 'refreshToken' é aceito como fallback para backward compat.
 */

import { NextRequest, NextResponse } from 'next/server'
import { SITES, VALID_SITE_IDS } from '@/lib/sites'

const PROTECTED_PATHS = ['/dashboard']

const PROTECTED_API_PATHS = [
  '/api/dashboard',
  '/api/upload',
]

/**
 * Resolve qual tenant a request pertence baseado no pathname.
 * Retorna o siteId ou 'platform' como fallback.
 */
function resolveSiteIdFromPath(pathname: string): string {
  const segments = pathname.split('/').filter(Boolean)
  const first = segments[0] ?? ''

  if (VALID_SITE_IDS.includes(first)) {
    return first
  }

  // Rotas sem prefixo de site → platform
  return 'platform'
}

/**
 * Verifica se o usuário tem cookie de refresh válido para o tenant.
 * Aceita cookie por tenant OU cookie legado como fallback.
 */
function hasValidRefreshCookie(req: NextRequest, siteId: string): boolean {
  const site = SITES[siteId]
  if (!site) return false

  // Cookie por tenant (novo)
  if (req.cookies.has(site.cookieName)) return true

  // Cookie legado (backward compat)
  if (req.cookies.has('refreshToken')) return true

  return false
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl

  // ── Redirect /login → /platform/login (backward compat) ────────────────
  if (pathname === '/login') {
    return NextResponse.redirect(new URL('/platform/login', req.url))
  }

  // ── Redirect /register → /platform/register (backward compat) ──────────
  if (pathname === '/register') {
    return NextResponse.redirect(new URL('/platform/register', req.url))
  }

  // ── Resolve tenant ─────────────────────────────────────────────────────
  const siteId = resolveSiteIdFromPath(pathname)

  // ── Protected pages ────────────────────────────────────────────────────
  const isProtected = PROTECTED_PATHS.some(p => pathname.startsWith(p))

  if (isProtected && !hasValidRefreshCookie(req, siteId)) {
    const loginPath = `/${siteId}/login`
    const loginUrl = new URL(loginPath, req.url)
    loginUrl.searchParams.set('redirect', pathname)
    return NextResponse.redirect(loginUrl)
  }

  // ── Protected API routes ───────────────────────────────────────────────
  const isProtectedApi = PROTECTED_API_PATHS.some(p => pathname.startsWith(p))

  if (isProtectedApi && !hasValidRefreshCookie(req, siteId)) {
    return NextResponse.json({ error: 'Não autorizado' }, { status: 401 })
  }

  return NextResponse.next()
}

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|data/).*)',
  ],
}

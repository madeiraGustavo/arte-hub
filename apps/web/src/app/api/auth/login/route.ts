/**
 * POST /api/auth/login
 * Proxy para POST ${API_URL}/auth/login na API Fastify
 *
 * Multi-tenant: repassa header X-Site-Id para que o backend resolva o tenant.
 * O header vem do frontend (LoginForm) — o backend valida contra config estática.
 */

import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export async function POST(req: NextRequest): Promise<NextResponse> {
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Corpo da requisição inválido' }, { status: 400 })
  }

  // Repassa X-Site-Id do frontend para o backend
  const siteId = req.headers.get('x-site-id') ?? 'platform'

  let apiRes: Response
  try {
    apiRes = await fetch(`${API_URL}/auth/login`, {
      method:  'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Site-Id':    siteId,
      },
      body: JSON.stringify(body),
    })
  } catch {
    return NextResponse.json({ error: 'Serviço indisponível' }, { status: 503 })
  }

  const data = await apiRes.json() as Record<string, unknown>
  const res  = NextResponse.json(data, { status: apiRes.status })

  // Repassa todos os cookies Set-Cookie da API para o browser
  // getSetCookie() retorna array com cada cookie individual
  const cookies = apiRes.headers.getSetCookie?.() ?? []
  if (cookies.length > 0) {
    for (const cookie of cookies) {
      res.headers.append('set-cookie', cookie)
    }
  } else {
    // Fallback: get() retorna cookies concatenados com ", "
    const setCookie = apiRes.headers.get('set-cookie')
    if (setCookie) {
      res.headers.set('set-cookie', setCookie)
    }
  }

  return res
}

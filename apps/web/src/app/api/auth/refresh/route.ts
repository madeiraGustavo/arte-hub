/**
 * POST /api/auth/refresh
 * Proxy para POST ${API_URL}/auth/refresh na API Fastify
 *
 * Repassa o cookie HttpOnly de refresh para o backend.
 * O backend valida o refresh token, revoga o antigo, e retorna novo par de tokens.
 */

import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export async function POST(req: NextRequest): Promise<NextResponse> {
  const siteId = req.headers.get('x-site-id') ?? 'platform'
  const cookieHeader = req.headers.get('cookie') ?? ''

  let apiRes: Response
  try {
    apiRes = await fetch(`${API_URL}/auth/refresh`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Site-Id': siteId,
        'Cookie': cookieHeader,
      },
      body: '{}',
    })
  } catch {
    return NextResponse.json({ error: 'Serviço indisponível' }, { status: 503 })
  }

  const data = await apiRes.json() as Record<string, unknown>
  const res = NextResponse.json(data, { status: apiRes.status })

  // Forward Set-Cookie headers (new refresh token cookie)
  const cookies = apiRes.headers.getSetCookie?.() ?? []
  if (cookies.length > 0) {
    for (const cookie of cookies) {
      res.headers.append('set-cookie', cookie)
    }
  } else {
    const setCookie = apiRes.headers.get('set-cookie')
    if (setCookie) {
      res.headers.set('set-cookie', setCookie)
    }
  }

  return res
}

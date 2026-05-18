/**
 * POST /api/auth/logout
 * Proxy para POST ${API_URL}/auth/logout na API Fastify
 *
 * Multi-tenant: repassa header X-Site-Id e cookies para que o backend
 * revogue os refresh tokens do tenant correto.
 */

import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export async function POST(req: NextRequest): Promise<NextResponse> {
  const siteId = req.headers.get('x-site-id') ?? 'platform'
  const authHeader = req.headers.get('authorization') ?? ''
  const cookieHeader = req.headers.get('cookie') ?? ''

  let apiRes: Response
  try {
    apiRes = await fetch(`${API_URL}/auth/logout`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Site-Id': siteId,
        'Authorization': authHeader,
        'Cookie': cookieHeader,
      },
      body: '{}',
    })
  } catch {
    return NextResponse.json({ error: 'Serviço indisponível' }, { status: 503 })
  }

  const res = new NextResponse(null, { status: apiRes.status })

  // Forward Set-Cookie headers (cookie clearance from API)
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

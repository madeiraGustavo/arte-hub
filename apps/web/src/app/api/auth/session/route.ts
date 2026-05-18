/**
 * GET /api/auth/session
 * Proxy para GET ${API_URL}/auth/session na API Fastify
 *
 * Repassa o accessToken via Authorization header.
 * Necessário porque o client.ts armazena o token em memória
 * e o cookie HttpOnly não é enviado cross-domain.
 */

import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export async function GET(req: NextRequest): Promise<NextResponse> {
  const authHeader = req.headers.get('authorization') ?? ''
  const siteId = req.headers.get('x-site-id') ?? 'platform'

  let apiRes: Response
  try {
    apiRes = await fetch(`${API_URL}/auth/session`, {
      method: 'GET',
      headers: {
        'Authorization': authHeader,
        'X-Site-Id': siteId,
      },
    })
  } catch {
    return NextResponse.json({ error: 'Serviço indisponível' }, { status: 503 })
  }

  const data = await apiRes.json() as Record<string, unknown>
  return NextResponse.json(data, { status: apiRes.status })
}

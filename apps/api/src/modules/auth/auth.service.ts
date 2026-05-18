/**
 * auth.service.ts
 *
 * Lógica de negócio de autenticação.
 * Não conhece FastifyRequest/FastifyReply — recebe e retorna DTOs tipados.
 * artist_id é extraído do banco via repository — nunca do token.
 */

import jwt from 'jsonwebtoken'
import crypto from 'crypto'
import { env } from '../../env.js'
import { verifyPassword, hashPassword } from '../../lib/password.js'
import {
  findUserByEmail,
  findUserByEmailAndSite,
  findUserById,
  createUser,
  createRefreshToken,
  findRefreshToken,
  revokeRefreshToken,
  revokeAllUserTokens,
  findArtistById,
  type UserWithAuth,
} from './auth.repository.js'

// ── Tipos ─────────────────────────────────────────────────────────────────────

export interface TokenPair {
  accessToken:  string
  refreshToken: string
}

export interface SessionData {
  authenticated: true
  user: {
    id:     string
    email:  string
    role:   string
    siteId: string
  }
  artist: {
    id:   string
    slug: string
    name: string
  } | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function signAccessToken(user: UserWithAuth): string {
  // Payload inclui sub, role e siteId para conveniência de leitura.
  // Nota: o hook authenticate SEMPRE busca role e siteId do banco —
  // os valores do token são informativos, não autoritativos.
  return jwt.sign(
    { sub: user.id, role: user.role, siteId: user.siteId },
    env.JWT_SECRET,
    { expiresIn: '15m' },
  )
}

function signRefreshToken(userId: string): string {
  return jwt.sign(
    { sub: userId },
    env.JWT_REFRESH_SECRET,
    { expiresIn: '7d' },
  )
}

function hashToken(token: string): string {
  return crypto.createHash('sha256').update(token).digest('hex')
}

function refreshExpiresAt(): Date {
  const d = new Date()
  d.setDate(d.getDate() + 7)
  return d
}

// ── Service methods ───────────────────────────────────────────────────────────

/**
 * Autentica usuário por siteId + email + password.
 * Se siteId não for fornecido, usa 'platform' como fallback (backward compat).
 */
export async function login(email: string, password: string, siteId: string = 'platform'): Promise<TokenPair> {
  const user = await findUserByEmailAndSite(email, siteId)

  if (!user || !user.password) {
    throw new Error('Credenciais inválidas')
  }

  const valid = await verifyPassword(password, user.password)
  if (!valid) {
    throw new Error('Credenciais inválidas')
  }

  const accessToken  = signAccessToken(user)
  const refreshToken = signRefreshToken(user.id)

  await createRefreshToken(user.id, hashToken(refreshToken), refreshExpiresAt())

  return { accessToken, refreshToken }
}

/**
 * Registra novo usuário no site especificado.
 * Retorna par de tokens (login automático após registro).
 */
export async function register(
  email:    string,
  password: string,
  siteId:   string,
  name?:    string,
): Promise<TokenPair> {
  // Verifica se já existe no site
  const existing = await findUserByEmailAndSite(email, siteId)
  if (existing) {
    throw new Error('Email já cadastrado neste site')
  }

  const passwordHash = await hashPassword(password)
  const user = await createUser(siteId, email, passwordHash, 'client', name)

  const accessToken  = signAccessToken(user)
  const refreshToken = signRefreshToken(user.id)

  await createRefreshToken(user.id, hashToken(refreshToken), refreshExpiresAt())

  return { accessToken, refreshToken }
}

export async function refresh(tokenFromCookieOrBody: string): Promise<TokenPair> {
  // 1. Verifica assinatura com JWT_REFRESH_SECRET
  let payload: { sub: string }
  try {
    payload = jwt.verify(tokenFromCookieOrBody, env.JWT_REFRESH_SECRET) as { sub: string }
  } catch {
    throw new Error('Refresh token inválido')
  }

  // 2. Busca hash no banco
  const tokenHash = hashToken(tokenFromCookieOrBody)
  const stored    = await findRefreshToken(tokenHash)

  if (!stored) {
    throw new Error('Refresh token inválido ou expirado')
  }

  // 3. Revoga token atual (rotação)
  await revokeRefreshToken(stored.id)

  // 4. Busca dados atualizados do usuário
  const user = await findUserById(payload.sub)
  if (!user) {
    throw new Error('Usuário não encontrado')
  }

  // 5. Emite novo par de tokens
  const accessToken     = signAccessToken(user)
  const newRefreshToken = signRefreshToken(user.id)

  await createRefreshToken(user.id, hashToken(newRefreshToken), refreshExpiresAt())

  return { accessToken, refreshToken: newRefreshToken }
}

export async function logout(userId: string): Promise<void> {
  await revokeAllUserTokens(userId)
}

export async function getSession(userId: string): Promise<SessionData> {
  const user = await findUserById(userId)
  if (!user) throw new Error('Usuário não encontrado')

  const artist = user.artistId
    ? await findArtistById(user.artistId)
    : null

  return {
    authenticated: true,
    user: {
      id:     user.id,
      email:  user.email,
      role:   user.role,
      siteId: user.siteId,
    },
    artist,
  }
}

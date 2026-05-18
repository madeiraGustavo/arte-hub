'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { SITES } from '@/lib/sites'
import { setAccessToken } from '@/lib/api/client'
import { bootstrapSession, type SessionData } from '@/lib/auth/session'

const site = SITES.tattoo!

export default function TattooMinhaContaPage() {
  const router = useRouter()
  const [session, setSession] = useState<SessionData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    bootstrapSession(site.id)
      .then(result => {
        if (result.status === 'authenticated') {
          setSession(result.data)
        } else {
          setSession(null)
        }
      })
      .catch(() => setSession(null))
      .finally(() => setLoading(false))
  }, [])

  function handleLogout() {
    fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-Site-Id': site.id },
    }).then(() => {
      setAccessToken(null)
      router.push(`/${site.slug}/login`)
    })
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-900">
        <p className="text-white/60">Carregando...</p>
      </div>
    )
  }

  return (
    <div className="min-h-screen px-4 py-12 bg-gray-900">
      <div className="max-w-lg mx-auto">
        <h1 className="text-2xl font-bold mb-2" style={{ color: site.theme.secondaryColor }}>
          {site.displayName}
        </h1>
        <h2 className="text-xl text-white mb-6">Minha Conta</h2>

        {session ? (
          <div className="space-y-4">
            <div className="bg-white/5 border border-white/10 rounded-lg p-4">
              <p className="text-white/60 text-sm mb-1">Logado como</p>
              <p className="text-white font-medium">{session.user.email}</p>
            </div>

            <div className="bg-white/5 border border-white/10 rounded-lg p-4">
              <h3 className="text-white font-medium mb-2">Dados da conta</h3>
              <p className="text-white/60 text-sm">Gerencie suas informações pessoais.</p>
            </div>

            <div className="bg-white/5 border border-white/10 rounded-lg p-4">
              <h3 className="text-white font-medium mb-2">Pedidos / Agendamentos</h3>
              <p className="text-white/60 text-sm">Acompanhe seus pedidos e agendamentos.</p>
            </div>

            <button
              onClick={handleLogout}
              className="w-full py-3 rounded-lg border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-colors"
            >
              Sair
            </button>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-white/60 mb-4">Sessão expirada. Faça login novamente.</p>
            <a href={`/${site.slug}/login`} className="text-sm underline" style={{ color: site.theme.secondaryColor }}>
              Ir para login
            </a>
          </div>
        )}
      </div>
    </div>
  )
}

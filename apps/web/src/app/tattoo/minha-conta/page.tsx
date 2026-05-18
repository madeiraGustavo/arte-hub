'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { SITES } from '@/lib/sites'
import { getAccessToken } from '@/lib/api/client'

const site = SITES.tattoo!

interface Session {
  authenticated: boolean
  user: { id: string; email: string; role: string }
}

export default function TattooMinhaContaPage() {
  const router = useRouter()
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = getAccessToken()
    fetch('/api/auth/session', {
      headers: token ? { 'Authorization': `Bearer ${token}`, 'X-Site-Id': site.id } : { 'X-Site-Id': site.id },
    })
      .then(r => r.ok ? r.json() : null)
      .then(data => setSession(data as Session | null))
      .catch(() => setSession(null))
      .finally(() => setLoading(false))
  }, [])

  function handleLogout() {
    fetch('/api/auth/logout', { method: 'POST', headers: { 'X-Site-Id': site.id } })
      .then(() => router.push(`/${site.slug}/login`))
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

        {session?.authenticated ? (
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
          <p className="text-white/60">Sessão expirada. Faça login novamente.</p>
        )}
      </div>
    </div>
  )
}

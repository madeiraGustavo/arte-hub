'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import type { SiteConfig } from '@/lib/sites'
import { setAccessToken } from '@/lib/api/client'

interface RegisterFormProps {
  site: SiteConfig
}

export function RegisterForm({ site }: RegisterFormProps) {
  const router = useRouter()
  const [email,    setEmail]    = useState('')
  const [password, setPassword] = useState('')
  const [name,     setName]     = useState('')
  const [error,    setError]    = useState<string | null>(null)
  const [loading,  setLoading]  = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)

    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Site-Id': site.id,
      },
      body: JSON.stringify({ email, password, ...(name.trim() && { name: name.trim() }) }),
    })

    if (res.status === 201) {
      const data = await res.json() as { accessToken?: string }
      if (data.accessToken) {
        setAccessToken(data.accessToken)
      }
      // Todos os tenants vão para área do cliente
      const redirectTo = `/${site.slug}/minha-conta`
      router.push(redirectTo)
      router.refresh()
    } else if (res.status === 409) {
      setError('Email já cadastrado neste site')
    } else if (res.status === 422) {
      const data = await res.json() as { details?: { fieldErrors?: Record<string, string[]> } }
      const fieldErrors = data.details?.fieldErrors
      if (fieldErrors) {
        const messages = Object.values(fieldErrors).flat().join('. ')
        setError(messages || 'Dados inválidos')
      } else {
        setError('Dados inválidos')
      }
    } else {
      const data = await res.json().catch(() => null) as { error?: string } | null
      setError(data?.error ?? 'Erro ao criar conta. Tente novamente.')
    }

    setLoading(false)
  }

  const buttonStyle = site.theme.gradientMain
    ? { background: site.theme.gradientMain }
    : { backgroundColor: site.theme.primaryColor }

  return (
    <form onSubmit={handleSubmit}
      className="bg-bg-card border border-[rgba(255,255,255,0.07)] rounded-lg p-8 flex flex-col gap-5">

      <div className="flex flex-col gap-2">
        <label htmlFor="name" className="text-sm font-medium text-text-secondary">Nome (opcional)</label>
        <input
          id="name" type="text" autoComplete="name"
          value={name} onChange={e => setName(e.target.value)}
          placeholder="Seu nome"
          maxLength={100}
          className={`bg-bg-elevated border border-[rgba(255,255,255,0.07)] rounded-md px-4 py-3
            text-text-primary text-sm outline-none placeholder:text-text-muted
            focus:border-[${site.theme.primaryColor}] transition-all`}
        />
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor="email" className="text-sm font-medium text-text-secondary">Email</label>
        <input
          id="email" type="email" required autoComplete="email"
          value={email} onChange={e => setEmail(e.target.value)}
          placeholder="seu@email.com"
          className={`bg-bg-elevated border border-[rgba(255,255,255,0.07)] rounded-md px-4 py-3
            text-text-primary text-sm outline-none placeholder:text-text-muted
            focus:border-[${site.theme.primaryColor}] transition-all`}
        />
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor="password" className="text-sm font-medium text-text-secondary">Senha</label>
        <input
          id="password" type="password" required autoComplete="new-password"
          value={password} onChange={e => setPassword(e.target.value)}
          placeholder="••••••••"
          minLength={6}
          className={`bg-bg-elevated border border-[rgba(255,255,255,0.07)] rounded-md px-4 py-3
            text-text-primary text-sm outline-none placeholder:text-text-muted
            focus:border-[${site.theme.primaryColor}] transition-all`}
        />
      </div>

      {error && (
        <div className="text-sm px-4 py-3 rounded-md border
          bg-[rgba(255,60,60,0.1)] border-[rgba(255,60,60,0.3)] text-[#ff3c3c]">
          {error}
        </div>
      )}

      <button type="submit" disabled={loading}
        style={buttonStyle}
        className="w-full py-3.5 rounded-[32px] text-white font-semibold text-sm
          shadow-[0_4px_20px_rgba(0,0,0,0.3)] hover:translate-y-[-2px] transition-all
          disabled:opacity-60 disabled:cursor-not-allowed">
        {loading ? 'Criando conta...' : 'Criar conta'}
      </button>

      <div className="text-center">
        <a
          href={`/${site.slug}/login`}
          className="text-xs text-text-muted hover:text-text-secondary transition-colors"
        >
          Já tem uma conta? Entrar
        </a>
      </div>
    </form>
  )
}

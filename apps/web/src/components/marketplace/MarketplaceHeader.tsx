'use client'

import { useEffect, useState, useCallback } from 'react'
import { setAccessToken } from '@/lib/api/client'
import { useCartStore } from '@/stores/cartStore'
import { bootstrapSession, type SessionData } from '@/lib/auth/session'

// ── Types ─────────────────────────────────────────────────────────────────────

type AuthState = 'loading' | 'authenticated' | 'unauthenticated'

// ── Helpers ───────────────────────────────────────────────────────────────────

function truncateDisplayName(value: string, maxLen = 20): string {
  return value.length > maxLen ? `${value.slice(0, maxLen)}…` : value
}

function resolveDisplayName(session: SessionData): string {
  if (session.artist?.name) return truncateDisplayName(session.artist.name)
  if (session.user.email) return truncateDisplayName(session.user.email)
  return 'Minha Conta'
}

// ── Component ─────────────────────────────────────────────────────────────────

export function MarketplaceHeader() {
  const [authState, setAuthState] = useState<AuthState>('loading')
  const [session, setSession] = useState<SessionData | null>(null)
  const [isScrolled, setIsScrolled] = useState(false)
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false)
  const [isLoggingOut, setIsLoggingOut] = useState(false)

  const items = useCartStore((state) => state.items)
  const cartItemCount = items.reduce((sum, item) => sum + item.quantity, 0)

  // Derived state
  const isLoggedIn = authState === 'authenticated'
  const isArtist = session?.user.role === 'artist' || session?.user.role === 'admin' || session?.user.role === 'editor'
  const displayName = session ? resolveDisplayName(session) : ''

  // ── Session bootstrap on mount ──────────────────────────────────────────
  useEffect(() => {
    bootstrapSession('marketplace')
      .then(result => {
        if (result.status === 'authenticated') {
          setSession(result.data)
          setAuthState('authenticated')
        } else {
          setAuthState('unauthenticated')
        }
      })
      .catch(() => setAuthState('unauthenticated'))
  }, [])

  // ── Logout handler ──────────────────────────────────────────────────────
  async function handleLogout() {
    setIsLoggingOut(true)
    closeMobileMenu()
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        headers: { 'X-Site-Id': 'marketplace' },
      })
    } catch {
      // Best-effort — clear local state regardless
    }
    setAccessToken(null)
    setSession(null)
    setAuthState('unauthenticated')
    setIsLoggingOut(false)
    window.location.href = '/marketplace/login'
  }

  useEffect(() => {
    function handleScroll() {
      setIsScrolled(window.scrollY > 50)
    }

    window.addEventListener('scroll', handleScroll, { passive: true })
    handleScroll()

    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  const closeMobileMenu = useCallback(() => {
    setIsMobileMenuOpen(false)
  }, [])

  const toggleMobileMenu = useCallback(() => {
    setIsMobileMenuOpen((prev) => !prev)
  }, [])

  return (
    <header
      className={`sticky top-0 z-50 transition-all duration-300 ${
        isScrolled
          ? 'backdrop-blur-md bg-white/90 shadow-sm'
          : 'bg-white'
      }`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-20 flex items-center justify-between">
        {/* Logo */}
        <a
          href="/marketplace"
          className="flex items-center gap-2"
        >
          <div className="w-8 h-8 flex items-center justify-center rounded-sm" style={{ backgroundColor: 'var(--mp-accent)' }}>
            <svg viewBox="0 0 24 24" fill="none" className="w-5 h-5" aria-hidden="true">
              <path d="M4 12 Q12 6 20 12 L20 14 Q12 8 4 14 Z" fill="var(--mp-text-on-accent)" />
              <line x1="6" y1="14" x2="6" y2="20" stroke="var(--mp-text-on-accent)" strokeWidth="1.5" />
              <line x1="18" y1="14" x2="18" y2="20" stroke="var(--mp-text-on-accent)" strokeWidth="1.5" />
            </svg>
          </div>
          <span
            className="text-lg font-bold uppercase tracking-tight"
            style={{ fontFamily: 'var(--mp-font-heading)', color: 'var(--mp-text-default)' }}
          >
            Toldos Colibri
          </span>
        </a>

        {/* Desktop Navigation */}
        <nav className="hidden md:flex items-center gap-8" aria-label="Navegação principal">
          <a
            href="/marketplace"
            className="text-sm font-medium transition-colors duration-200 hover:opacity-80"
            style={{ color: 'var(--mp-text-secondary)' }}
          >
            Catálogo
          </a>
          <a
            href="/marketplace#categorias"
            className="text-sm font-medium transition-colors duration-200 hover:opacity-80"
            style={{ color: 'var(--mp-text-secondary)' }}
          >
            Categorias
          </a>
          <a
            href="/marketplace#orcamento"
            className="text-sm font-medium transition-colors duration-200 hover:opacity-80"
            style={{ color: 'var(--mp-text-secondary)' }}
          >
            Orçamento
          </a>
          {isArtist && (
            <a
              href="/dashboard/marketplace"
              className="text-sm font-medium transition-colors duration-200 hover:opacity-80"
              style={{ color: 'var(--mp-text-secondary)' }}
            >
              Dashboard
            </a>
          )}
        </nav>

        {/* Desktop Actions */}
        <div className="hidden md:flex items-center gap-4">
          {/* Cart Icon */}
          <a
            href="/marketplace/cart"
            className="relative flex items-center justify-center w-11 h-11 rounded-full transition-colors duration-200 hover:bg-gray-100"
            aria-label={`Carrinho de compras, ${cartItemCount} ${cartItemCount === 1 ? 'item' : 'itens'}`}
          >
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ color: 'var(--mp-text-default)' }}
            >
              <path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z" />
              <line x1="3" y1="6" x2="21" y2="6" />
              <path d="M16 10a4 4 0 01-8 0" />
            </svg>
            {cartItemCount > 0 && (
              <span
                className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[20px] h-5 px-1 text-xs font-bold rounded-full"
                style={{
                  backgroundColor: 'var(--mp-accent)',
                  color: 'var(--mp-text-on-accent)',
                }}
              >
                {cartItemCount > 99 ? '99+' : cartItemCount}
              </span>
            )}
          </a>

          {/* Login / Account / Logout */}
          {authState === 'loading' ? (
            <div className="w-24 h-10" /> /* placeholder while loading */
          ) : !isLoggedIn ? (
            <a
              href="/marketplace/login"
              className="mp-btn-primary text-sm px-5 py-2"
              style={{ minHeight: '44px', display: 'inline-flex', alignItems: 'center' }}
            >
              Entrar
            </a>
          ) : (
            <div className="flex items-center gap-3">
              <a
                href="/marketplace/minha-conta"
                className="text-sm font-medium transition-colors duration-200 hover:opacity-80"
                style={{ color: 'var(--mp-text-default)' }}
              >
                Olá, {displayName}
              </a>
              <button
                type="button"
                disabled={isLoggingOut}
                onClick={handleLogout}
                className="text-sm font-medium transition-colors duration-200 hover:opacity-80 disabled:opacity-50"
                style={{ color: 'var(--mp-text-secondary)' }}
              >
                Sair
              </button>
            </div>
          )}
        </div>

        {/* Mobile Actions */}
        <div className="flex md:hidden items-center gap-2">
          {/* Cart Icon (Mobile) */}
          <a
            href="/marketplace/cart"
            className="relative flex items-center justify-center w-11 h-11 rounded-full transition-colors duration-200 hover:bg-gray-100"
            aria-label={`Carrinho de compras, ${cartItemCount} ${cartItemCount === 1 ? 'item' : 'itens'}`}
          >
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ color: 'var(--mp-text-default)' }}
            >
              <path d="M6 2L3 6v14a2 2 0 002 2h14a2 2 0 002-2V6l-3-4z" />
              <line x1="3" y1="6" x2="21" y2="6" />
              <path d="M16 10a4 4 0 01-8 0" />
            </svg>
            {cartItemCount > 0 && (
              <span
                className="absolute -top-0.5 -right-0.5 flex items-center justify-center min-w-[20px] h-5 px-1 text-xs font-bold rounded-full"
                style={{
                  backgroundColor: 'var(--mp-accent)',
                  color: 'var(--mp-text-on-accent)',
                }}
              >
                {cartItemCount > 99 ? '99+' : cartItemCount}
              </span>
            )}
          </a>

          {/* Hamburger Button */}
          <button
            type="button"
            onClick={toggleMobileMenu}
            className="flex items-center justify-center w-11 h-11 rounded-full transition-colors duration-200 hover:bg-gray-100"
            aria-label={isMobileMenuOpen ? 'Fechar menu' : 'Abrir menu'}
            aria-expanded={isMobileMenuOpen}
          >
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ color: 'var(--mp-text-default)' }}
            >
              {isMobileMenuOpen ? (
                <>
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </>
              ) : (
                <>
                  <line x1="3" y1="6" x2="21" y2="6" />
                  <line x1="3" y1="12" x2="21" y2="12" />
                  <line x1="3" y1="18" x2="21" y2="18" />
                </>
              )}
            </svg>
          </button>
        </div>
      </div>

      {/* Mobile Slide-in Panel */}
      {/* Overlay */}
      <div
        className={`fixed inset-0 z-40 bg-black/40 transition-opacity duration-300 md:hidden ${
          isMobileMenuOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={closeMobileMenu}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        className={`fixed top-0 right-0 z-50 h-full w-72 bg-white shadow-xl transition-transform duration-300 ease-in-out md:hidden ${
          isMobileMenuOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        role="dialog"
        aria-modal="true"
        aria-label="Menu de navegação"
      >
        {/* Panel Header */}
        <div className="flex items-center justify-between h-20 px-4 border-b" style={{ borderColor: 'var(--mp-border-default)' }}>
          <span
            className="text-lg font-bold"
            style={{ fontFamily: 'var(--mp-font-heading)', color: 'var(--mp-text-default)' }}
          >
            Menu
          </span>
          <button
            type="button"
            onClick={closeMobileMenu}
            className="flex items-center justify-center w-11 h-11 rounded-full transition-colors duration-200 hover:bg-gray-100"
            aria-label="Fechar menu"
          >
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ color: 'var(--mp-text-default)' }}
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Panel Navigation */}
        <nav className="flex flex-col p-4 gap-1" aria-label="Menu mobile">
          <a
            href="/marketplace"
            className="flex items-center h-11 px-3 rounded-lg text-sm font-medium transition-colors duration-200 hover:bg-gray-100"
            style={{ color: 'var(--mp-text-default)' }}
            onClick={closeMobileMenu}
          >
            Catálogo
          </a>
          <a
            href="/marketplace#categorias"
            className="flex items-center h-11 px-3 rounded-lg text-sm font-medium transition-colors duration-200 hover:bg-gray-100"
            style={{ color: 'var(--mp-text-default)' }}
            onClick={closeMobileMenu}
          >
            Categorias
          </a>
          <a
            href="/marketplace#orcamento"
            className="flex items-center h-11 px-3 rounded-lg text-sm font-medium transition-colors duration-200 hover:bg-gray-100"
            style={{ color: 'var(--mp-text-default)' }}
            onClick={closeMobileMenu}
          >
            Orçamento
          </a>
          {isArtist && (
            <a
              href="/dashboard/marketplace"
              className="flex items-center h-11 px-3 rounded-lg text-sm font-medium transition-colors duration-200 hover:bg-gray-100"
              style={{ color: 'var(--mp-text-default)' }}
              onClick={closeMobileMenu}
            >
              Dashboard
            </a>
          )}
        </nav>

        {/* Panel Footer — Login/Account/Logout */}
        <div className="absolute bottom-0 left-0 right-0 p-4 border-t" style={{ borderColor: 'var(--mp-border-default)' }}>
          {authState === 'loading' ? (
            <div className="h-11" />
          ) : !isLoggedIn ? (
            <a
              href="/marketplace/login"
              className="mp-btn-primary w-full text-sm text-center"
              onClick={closeMobileMenu}
            >
              Entrar
            </a>
          ) : (
            <div className="flex flex-col gap-2">
              <a
                href="/marketplace/minha-conta"
                className="mp-btn-secondary w-full text-sm text-center"
                onClick={closeMobileMenu}
              >
                Olá, {displayName}
              </a>
              <button
                type="button"
                disabled={isLoggingOut}
                onClick={handleLogout}
                className="w-full h-11 text-sm font-medium rounded-lg border transition-colors duration-200 hover:bg-red-50 disabled:opacity-50"
                style={{ borderColor: 'var(--mp-border-default)', color: '#dc2626' }}
              >
                Sair
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}

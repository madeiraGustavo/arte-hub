# Implementation Plan: Tenant Authenticated Header

## Overview

Update the MarketplaceHeader to consume session state from the Fastify API instead of decoding JWTs locally. The header will display a personalized greeting for authenticated users (supporting all roles: client, artist, editor, admin) with proper display name resolution, account navigation, and tenant-isolated logout. Backend changes are minimal: add `name` to the artist select in `findArtistById`.

## Tasks

- [ ] 1. Backend: Add name field to artist session data
  - [ ] 1.1 Update `findArtistById` in auth.repository.ts to include `name` in the select clause
    - Change `select: { id: true, slug: true }` to `select: { id: true, slug: true, name: true }`
    - Update the return type from `{ id: string; slug: string }` to `{ id: string; slug: string; name: string }`
    - _Requirements: 2.1_

  - [ ] 1.2 Update `SessionData` interface in auth.service.ts to reflect the new artist shape
    - Change `artist: { id: string; slug: string } | null` to `artist: { id: string; slug: string; name: string } | null`
    - _Requirements: 2.1_

- [ ] 2. Frontend: Update MarketplaceHeader to use session API
  - [ ] 2.1 Add session fetching logic replacing JWT decode in MarketplaceHeader.tsx
    - Remove the `atob()` JWT decode block from the `useEffect`
    - Add `AuthState` type (`'loading' | 'authenticated' | 'unauthenticated'`) and `SessionData` interface
    - On mount: check `getAccessToken()`, if present fetch `GET /api/auth/session` with `Authorization: Bearer {token}` and `X-Site-Id: {siteId}` headers
    - Resolve current site using `resolveSiteFromPath(window.location.pathname)` from `@/lib/sites`
    - On 200 with `authenticated: true`: transition to authenticated state
    - On non-200 or no token: transition to unauthenticated state
    - While loading: render header structure without auth UI (no "Entrar" or greeting)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 9.1, 9.2_

  - [ ] 2.2 Implement display name resolution and greeting in MarketplaceHeader.tsx
    - Add `truncateDisplayName(value: string, maxLen = 20): string` helper function
    - Implement display name priority: `user.name` (if available) → `artist.name` (if user has artist profile) → `user.email` → `"Minha Conta"`
    - Render greeting text "Olá, {displayName}" in both desktop and mobile areas
    - Truncate to 20 characters with ellipsis when exceeded
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ] 2.3 Implement authenticated actions (account link + logout) in MarketplaceHeader.tsx
    - Add "Minha Conta" link pointing to `/{siteSlug}/minha-conta` in both desktop and mobile
    - Add "Sair" button that sends `POST /api/auth/logout` with `X-Site-Id` header
    - On logout (success or error): call `setAccessToken(null)`, transition to unauthenticated, redirect to `/{siteSlug}/login`
    - Add `isLoggingOut` state to disable "Sair" button during request
    - Close mobile menu after logout action
    - Preserve "Entrar" link for unauthenticated state pointing to `/{siteSlug}/login`
    - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 1.1, 1.2, 1.3, 1.4_

  - [ ] 2.4 Update Dashboard link visibility based on session role
    - Derive `isArtist` from `user.role === 'artist' || user.role === 'admin'` using session response data
    - Show Dashboard link only when `isArtist` is true
    - _Requirements: 6.3, 8.1, 8.2_

  - [ ]* 2.5 Write property tests for display name resolution (Property 1)
    - **Property 1: Display name resolution**
    - Test that for any valid session response, the resolved display name follows priority: `user.name` → `artist.name` → `user.email` → `"Minha Conta"`
    - Use fast-check to generate arbitrary session shapes
    - **Validates: Requirements 2.1, 2.2, 2.3**

  - [ ]* 2.6 Write property test for display name truncation (Property 2)
    - **Property 2: Display name truncation**
    - Test that output equals input when length ≤ 20, or first 20 chars + "…" when length > 20
    - Use fast-check with arbitrary strings
    - **Validates: Requirements 2.5**

  - [ ]* 2.7 Write property test for tenant URL construction (Property 3)
    - **Property 3: Tenant URL construction**
    - Test that for any valid SiteConfig, login URL equals `/${site.slug}/login` and account URL equals `/${site.slug}/minha-conta`
    - **Validates: Requirements 1.2, 3.1, 4.4**

- [ ] 3. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Frontend: Ensure tenant isolation and responsive behavior
  - [ ] 4.1 Verify X-Site-Id header inclusion and tenant resolution in MarketplaceHeader.tsx
    - Ensure all fetch calls (session check and logout) include `X-Site-Id` header with the resolved site id
    - Ensure site resolution uses `resolveSiteFromPath()` from `@/lib/sites`
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [ ] 4.2 Ensure responsive behavior for authenticated state in both desktop and mobile
    - Verify greeting, account link, and "Sair" button render in Desktop_Nav (≥768px)
    - Verify greeting, account link, and "Sair" button render in Mobile_Menu (<768px)
    - Ensure mobile menu closes after "Sair" action
    - Maintain 44x44px minimum touch targets for interactive elements
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ]* 4.3 Write property test for X-Site-Id header inclusion (Property 4)
    - **Property 4: X-Site-Id header inclusion**
    - Test that for any API request initiated by the header, the `X-Site-Id` header value equals the resolved site id
    - **Validates: Requirements 4.2, 5.2, 7.2**

  - [ ]* 4.4 Write property test for logout clears token (Property 5)
    - **Property 5: Logout clears token regardless of response**
    - Test that for any logout response (success or error), `setAccessToken(null)` is called and state transitions to unauthenticated
    - **Validates: Requirements 4.3, 4.5**

  - [ ]* 4.5 Write property test for session response determines auth state (Property 6)
    - **Property 6: Session response determines auth state**
    - Test that HTTP 200 + `authenticated: true` → authenticated state; non-200 or `authenticated: false` → unauthenticated state
    - **Validates: Requirements 5.3, 5.4**

  - [ ]* 4.6 Write property test for navigation invariants (Property 7)
    - **Property 7: Navigation invariants across auth states**
    - Test that nav links (Catálogo, Categorias, Orçamento) and cart icon always render regardless of auth state
    - Test that unauthenticated state never renders greeting, account link, or logout action
    - **Validates: Requirements 6.1, 6.2, 1.4**

  - [ ]* 4.7 Write property test for Dashboard link conditional on role (Property 8)
    - **Property 8: Dashboard link conditional on role**
    - Test that Dashboard link is present iff `user.role` is `'artist'` or `'admin'`
    - **Validates: Requirements 6.3**

  - [ ]* 4.8 Write property test for tenant resolution from URL path (Property 9)
    - **Property 9: Tenant resolution from URL path**
    - Test that valid site slugs resolve to correct SiteConfig and unknown slugs resolve to platform
    - **Validates: Requirements 7.1**

- [ ] 5. Final checkpoint - Ensure all tests pass and typecheck
  - Ensure all tests pass, ask the user if questions arise.
  - Run `tsc --noEmit` to verify no type errors were introduced.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- The design uses TypeScript — all implementation uses TypeScript
- The logout proxy (`/api/auth/logout/route.ts`) already exists and does not need to be created
- The session proxy (`/api/auth/session/route.ts`) already exists and does not need to be created
- Display name priority per user's critical adjustment: `user.name` → `artist.name` (only when user has artist profile) → `user.email` → `"Minha Conta"`
- The `User` model may not have a `name` field — if the session endpoint doesn't return `user.name`, the fallback chain handles it gracefully
- No new dependencies are introduced
- Existing header layout (logo, nav links, cart, responsive behavior) is preserved

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1"] },
    { "id": 3, "tasks": ["2.2", "2.4"] },
    { "id": 4, "tasks": ["2.3", "2.5", "2.6", "2.7"] },
    { "id": 5, "tasks": ["4.1", "4.2"] },
    { "id": 6, "tasks": ["4.3", "4.4", "4.5", "4.6", "4.7", "4.8"] }
  ]
}
```

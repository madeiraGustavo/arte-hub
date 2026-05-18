# Implementation Plan: Tenant Authenticated Header

## Overview

Update the MarketplaceHeader to consume session state from the Fastify API instead of decoding JWTs locally. The header will display a personalized greeting for authenticated users (supporting all roles: client, artist, editor, admin) with proper display name resolution, account navigation, and tenant-isolated logout. Backend changes are minimal: add `name` to the artist select in `findArtistById`.

## Tasks

- [x] 1. Backend: Add name field to artist session data
  - [x] 1.1 Update `findArtistById` in auth.repository.ts to include `name` in the select clause
    - Change `select: { id: true, slug: true }` to `select: { id: true, slug: true, name: true }`
    - Update the return type from `{ id: string; slug: string }` to `{ id: string; slug: string; name: string }`
    - _Requirements: 2.1_

  - [x] 1.2 Update `SessionData` interface in auth.service.ts to reflect the new artist shape
    - Change `artist: { id: string; slug: string } | null` to `artist: { id: string; slug: string; name: string } | null`
    - _Requirements: 2.1_

- [x] 2. Frontend: Update MarketplaceHeader to use session API
  - [x] 2.1 Add session fetching logic replacing JWT decode in MarketplaceHeader.tsx
    - Remove the `atob()` JWT decode block from the `useEffect`
    - Add `AuthState` type (`'loading' | 'authenticated' | 'unauthenticated'`) and `SessionData` interface
    - On mount: check `getAccessToken()`, if present fetch `GET /api/auth/session` with `Authorization: Bearer {token}` and `X-Site-Id: marketplace` headers
    - On 200 with `authenticated: true`: transition to authenticated state
    - On non-200 or no token: transition to unauthenticated state
    - While loading: render header structure without auth UI (no "Entrar" or greeting)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 9.1, 9.2_

  - [x] 2.2 Implement display name resolution and greeting in MarketplaceHeader.tsx
    - Add `truncateDisplayName(value: string, maxLen = 20): string` helper function
    - Implement display name priority: `artist.name` (if user has artist profile) → `user.email` → `"Minha Conta"`
    - Render greeting text "Olá, {displayName}" in both desktop and mobile areas
    - Truncate to 20 characters with ellipsis when exceeded
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [x] 2.3 Implement authenticated actions (account link + logout) in MarketplaceHeader.tsx
    - Add "Olá, {displayName}" link pointing to `/marketplace/minha-conta` in both desktop and mobile
    - Add "Sair" button that sends `POST /api/auth/logout` with `X-Site-Id: marketplace` header
    - On logout (success or error): call `setAccessToken(null)`, transition to unauthenticated, redirect to `/marketplace/login`
    - Add `isLoggingOut` state to disable "Sair" button during request
    - Close mobile menu after logout action
    - Preserve "Entrar" link for unauthenticated state pointing to `/marketplace/login`
    - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 1.1, 1.2, 1.3, 1.4_

  - [x] 2.4 Update Dashboard link visibility based on session role
    - Derive `isArtist` from `user.role === 'artist' || user.role === 'admin' || user.role === 'editor'`
    - Show Dashboard link only when `isArtist` is true
    - _Requirements: 6.3, 8.1, 8.2_

  - [ ]* 2.5 Write property tests for display name resolution (Property 1)
    - **Property 1: Display name resolution**
    - Test that for any valid session response, the resolved display name follows priority: `artist.name` → `user.email` → `"Minha Conta"`
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

- [x] 3. Checkpoint - Ensure all tests pass
  - API typecheck: ✅ zero errors
  - Web typecheck: ✅ zero errors
  - Auth tests (33): ✅ all pass

- [x] 4. Frontend: Ensure tenant isolation and responsive behavior
  - [x] 4.1 Verify X-Site-Id header inclusion and tenant resolution in MarketplaceHeader.tsx
    - All fetch calls include `X-Site-Id: marketplace` header
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [x] 4.2 Ensure responsive behavior for authenticated state in both desktop and mobile
    - Greeting, account link, and "Sair" button render in Desktop_Nav (≥768px)
    - Greeting, account link, and "Sair" button render in Mobile_Menu (<768px)
    - Mobile menu closes after "Sair" action
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [ ]* 4.3 Write property test for X-Site-Id header inclusion (Property 4)
    - **Property 4: X-Site-Id header inclusion**
    - **Validates: Requirements 4.2, 5.2, 7.2**

  - [ ]* 4.4 Write property test for logout clears token (Property 5)
    - **Property 5: Logout clears token regardless of response**
    - **Validates: Requirements 4.3, 4.5**

  - [ ]* 4.5 Write property test for session response determines auth state (Property 6)
    - **Property 6: Session response determines auth state**
    - **Validates: Requirements 5.3, 5.4**

  - [ ]* 4.6 Write property test for navigation invariants (Property 7)
    - **Property 7: Navigation invariants across auth states**
    - **Validates: Requirements 6.1, 6.2, 1.4**

  - [ ]* 4.7 Write property test for Dashboard link conditional on role (Property 8)
    - **Property 8: Dashboard link conditional on role**
    - **Validates: Requirements 6.3**

  - [ ]* 4.8 Write property test for tenant resolution from URL path (Property 9)
    - **Property 9: Tenant resolution from URL path**
    - **Validates: Requirements 7.1**

- [x] 5. Final checkpoint - Ensure all tests pass and typecheck
  - API typecheck: ✅ zero errors
  - Web typecheck: ✅ zero errors
  - Auth tests (33): ✅ all pass
  - Committed and pushed to main: ✅

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Display name priority (adjusted per user): `artist.name` → `user.email` → `"Minha Conta"`
- Dashboard link visible for: artist, editor, admin (not only artist + admin)
- User model does NOT have a `name` field — greeting uses artist.name or email
- Logout proxy created at `/api/auth/logout/route.ts`
- No new dependencies introduced
- Existing header layout (logo, nav links, cart, responsive behavior) preserved

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

# Implementation Plan: Multi-Tenant Public Registration

## Overview

Frontend-only implementation exposing the existing Fastify `POST /auth/register` endpoint to users. Creates a proxy route, a shared RegisterForm component, per-tenant registration pages, and a middleware redirect. No backend changes required.

## Tasks

- [x] 1. Create the registration proxy route
  - [x] 1.1 Create `apps/web/src/app/api/auth/register/route.ts`
    - Mirror the existing `/api/auth/login/route.ts` pattern exactly
    - Parse request body (return 400 if invalid JSON)
    - Extract `X-Site-Id` header (default to `'platform'`)
    - Forward to `${API_URL}/auth/register` with `Content-Type` and `X-Site-Id` headers
    - Forward `Set-Cookie` headers from API response to browser
    - Return API status code and JSON body
    - Return 503 if API is unreachable
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

  - [x]* 1.2 Write unit tests for the registration proxy route [PBT]
    - **Property 8: Site Resolution Fallback** — verify proxy defaults to `'platform'` when `X-Site-Id` is missing or invalid
    - **Validates: Requirements 3.1, 4.4, 4.5, 5.5, 5.6**
    - Also test: 400 on invalid JSON, 503 on unreachable API, Set-Cookie forwarding

- [x] 2. Create the RegisterForm component
  - [x] 2.1 Create `apps/web/src/components/auth/RegisterForm.tsx`
    - Client component (`'use client'`)
    - Accept `site: SiteConfig` prop (same interface as LoginForm)
    - Collect fields: email (required), password (required, min 6), name (optional)
    - Submit to `/api/auth/register` with `X-Site-Id: site.id` header
    - On 201: redirect to `/dashboard` (platform) or `/${site.slug}/dashboard` (other tenants)
    - On 409: display "Email já cadastrado neste site"
    - On 422: display field-level errors from response
    - On other errors: display "Erro ao criar conta. Tente novamente."
    - Disable submit button during request (loading state)
    - Include link to `/{site.slug}/login` below the form
    - Match LoginForm visual pattern: same structure, spacing, input styling, error display, themed button
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 9.3_

  - [x]* 2.2 Write property test for post-registration redirect logic [PBT]
    - **Property 10: Post-Registration Redirect Logic**
    - For any site config, redirect is `/dashboard` when site.id === 'platform', otherwise `/{site.slug}/dashboard`
    - **Validates: Requirements 2.3, 9.3**

- [x] 3. Create per-tenant registration pages
  - [x] 3.1 Create `apps/web/src/app/platform/register/page.tsx`
    - Server component importing `SITES.platform`, `AuthLayout`, and `RegisterForm`
    - Set metadata: `title: 'Criar conta — Arte Hub'`
    - Render `<AuthLayout site={site}><RegisterForm site={site} /></AuthLayout>`
    - Follow exact pattern of `apps/web/src/app/platform/login/page.tsx`
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 3.2 Create `apps/web/src/app/marketplace/register/page.tsx`
    - Same pattern as 3.1 but with `SITES.marketplace`
    - Set metadata: `title: 'Criar conta — Toldos Colibri'`
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 3.3 Create `apps/web/src/app/tattoo/register/page.tsx`
    - Same pattern as 3.1 but with `SITES.tattoo`
    - Set metadata: `title: 'Criar conta — Studio Tattoo'`
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 3.4 Create `apps/web/src/app/music/register/page.tsx`
    - Same pattern as 3.1 but with `SITES.music`
    - Set metadata: `title: 'Criar conta — Arte Hub Music'`
    - _Requirements: 1.1, 1.2, 1.4_

- [x] 4. Add middleware redirect for `/register`
  - [x] 4.1 Modify `apps/web/src/middleware.ts`
    - Add `if (pathname === '/register')` redirect to `/platform/register`
    - Place immediately after the existing `/login` → `/platform/login` redirect (line 73)
    - Single line addition matching the existing pattern exactly
    - Do NOT create a separate `/register/page.tsx` — middleware handles it
    - _Requirements: 1.5_

- [x] 5. Checkpoint — Verify registration flow
  - All TypeScript checks pass (tsc --noEmit exits 0)
  - All diagnostics clean across all new files

- [x] 6. Update API documentation
  - [x] 6.1 Update `apps/api/README.md`
    - Document the public registration flow: POST `/auth/register` endpoint
    - Document Zod validation rules (email format, password min 6, optional name 1-100)
    - Document email normalization (lowercase + trim)
    - Document tenant resolution via `X-Site-Id` header
    - Document tenant-specific cookie names (ah_platform_refresh, ah_marketplace_refresh, ah_tattoo_refresh, ah_music_refresh) with attributes (HttpOnly, Secure, SameSite=Strict, Path=/)
    - Document composite key strategy (siteId + email)
    - Document the proxy pattern: Next.js `/api/auth/register` → Fastify `/auth/register`
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

- [x] 7. Final checkpoint — Ensure all tests pass
  - TypeScript compilation: ✅ zero errors
  - All diagnostics: ✅ clean

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Backend is complete — NO modifications to `apps/api/src/` files
- Redirect after registration: platform → `/dashboard`, other tenants → `/{site.slug}/dashboard`
- Route naming uses `/register` (English) matching existing `/login` convention
- Password minimum: 6 characters (consistent with LoginSchema and RegisterSchema)
- Proxy is pass-through only — no business logic in Next.js
- Property tests tagged [PBT] validate universal correctness properties from the design document
- Existing backend property tests (auth.isolation.property.test.ts, auth.service.property.test.ts) already cover Properties 1–9; frontend tasks cover Property 10

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "3.1", "3.2", "3.3", "3.4", "4.1"] },
    { "id": 1, "tasks": ["1.2", "2.1"] },
    { "id": 2, "tasks": ["2.2", "6.1"] }
  ]
}
```

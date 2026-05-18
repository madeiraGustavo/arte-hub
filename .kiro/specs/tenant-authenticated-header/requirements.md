# Requirements Document

## Introduction

Tenant-aware authenticated header for Arte Hub multi-tenant sites. Updates the header/nav component of each tenant site to reflect the authenticated state of the user. When logged in, the header displays a personalized greeting and account/logout actions instead of the generic "Entrar" button — similar to the pattern used by Mercado Livre and Amazon. The feature respects multi-tenant isolation: authentication state on one tenant does not affect another tenant's header. All session state is consumed from the existing Fastify API; no business logic resides in the frontend.

## Glossary

- **Header_Component**: The tenant-specific React header component (e.g., MarketplaceHeader) that renders the top navigation bar
- **Session_Endpoint**: The Fastify GET `/auth/session` endpoint that returns the authenticated user's session data (id, email, role, siteId, artist info)
- **Session_Proxy**: The Next.js API route at `/api/auth/session` that proxies requests to the Fastify Session_Endpoint
- **Logout_Proxy**: The Next.js API route at `/api/auth/logout` that proxies logout requests to the Fastify API and forwards Set-Cookie headers for cookie clearance
- **Access_Token**: The in-memory JWT (15-minute expiry) stored by the client.ts module and used for Authorization headers
- **Refresh_Cookie**: The HttpOnly cookie named `ah_{siteId}_refresh` used for session persistence per tenant
- **Tenant**: One of the configured sites (platform, marketplace, tattoo, music) identified by siteId
- **Site_Config**: The static configuration object defining a tenant's id, slug, displayName, theme, authEnabled, and cookieName
- **Authenticated_State**: The UI state where the header displays a greeting, account link, and logout action because a valid session exists
- **Unauthenticated_State**: The UI state where the header displays the "Entrar" button linking to the tenant login page
- **Mobile_Menu**: The slide-in navigation panel displayed on viewports below the `md` breakpoint (768px)
- **Desktop_Nav**: The horizontal navigation bar displayed on viewports at or above the `md` breakpoint

## Requirements

### Requirement 1: Unauthenticated Header State

**User Story:** As an unauthenticated visitor on a tenant site, I want to see a clear "Entrar" button in the header, so that I know how to log in.

#### Acceptance Criteria

1. WHILE the user has no valid Access_Token in memory, THE Header_Component SHALL display a button or link with the text "Entrar"
2. WHEN the user clicks the "Entrar" element, THE Header_Component SHALL navigate to `/{siteSlug}/login` where siteSlug is the current Tenant slug
3. THE Header_Component SHALL display the "Entrar" element in both the Desktop_Nav actions area and the Mobile_Menu footer area
4. THE Header_Component SHALL NOT display any greeting text, account link, or logout action while in Unauthenticated_State

### Requirement 2: Authenticated Header Greeting

**User Story:** As an authenticated user, I want to see a personalized greeting in the header, so that I have visual confirmation that I am logged in.

#### Acceptance Criteria

1. WHEN the Session_Endpoint returns a valid session with a user name, THE Header_Component SHALL display the greeting text "Olá, {name}" where {name} is the user's name from the session response
2. WHEN the Session_Endpoint returns a valid session without a user name but with an email, THE Header_Component SHALL display the greeting text "Olá, {email}" where {email} is the user's email from the session response
3. IF the Session_Endpoint returns a valid session but both name and email are unavailable, THEN THE Header_Component SHALL display the fallback text "Minha Conta"
4. THE Header_Component SHALL display the greeting text in both the Desktop_Nav actions area and the Mobile_Menu panel
5. THE Header_Component SHALL truncate the displayed name or email to a maximum of 20 characters followed by an ellipsis when the value exceeds 20 characters, to prevent layout overflow

### Requirement 3: Authenticated Account Navigation

**User Story:** As an authenticated user, I want to access my account page from the header, so that I can manage my profile without searching for the link.

#### Acceptance Criteria

1. WHILE the user is in Authenticated_State, THE Header_Component SHALL display a clickable link to `/{siteSlug}/minha-conta` where siteSlug is the current Tenant slug
2. THE Header_Component SHALL display the account link in both the Desktop_Nav actions area and the Mobile_Menu panel
3. WHEN the user clicks the account link in the Mobile_Menu, THE Header_Component SHALL close the Mobile_Menu after navigation

### Requirement 4: Authenticated Logout Action

**User Story:** As an authenticated user, I want to log out from the header, so that I can end my session without navigating to a separate page.

#### Acceptance Criteria

1. WHILE the user is in Authenticated_State, THE Header_Component SHALL display a "Sair" action element (button or link)
2. WHEN the user activates the "Sair" action, THE Header_Component SHALL send a POST request to the Logout_Proxy with the header `X-Site-Id` set to the current Tenant id
3. WHEN the Logout_Proxy returns a successful response (HTTP 204), THE Header_Component SHALL clear the Access_Token from memory by calling setAccessToken(null) and transition the header to Unauthenticated_State
4. WHEN the Logout_Proxy returns a successful response, THE Header_Component SHALL redirect the user to `/{siteSlug}/login`
5. IF the Logout_Proxy returns an error response, THEN THE Header_Component SHALL still clear the local Access_Token and transition to Unauthenticated_State to avoid a stale UI
6. THE Header_Component SHALL display the "Sair" action in both the Desktop_Nav actions area and the Mobile_Menu panel
7. THE Header_Component SHALL disable the "Sair" action element while the logout request is in progress to prevent duplicate submissions

### Requirement 5: Session State Resolution

**User Story:** As the frontend application, I want to resolve the user's authentication state on component mount, so that the header renders the correct state without a full page reload.

#### Acceptance Criteria

1. WHEN the Header_Component mounts, THE Header_Component SHALL check for the presence of an Access_Token via the getAccessToken() function from client.ts
2. WHEN an Access_Token is present, THE Header_Component SHALL send a GET request to the Session_Proxy with the `Authorization: Bearer {token}` header and the `X-Site-Id` header set to the current Tenant id
3. WHEN the Session_Proxy returns HTTP 200 with `authenticated: true`, THE Header_Component SHALL transition to Authenticated_State using the user data from the response
4. WHEN the Session_Proxy returns a non-200 response or `authenticated: false`, THE Header_Component SHALL remain in Unauthenticated_State
5. IF no Access_Token is present on mount, THEN THE Header_Component SHALL remain in Unauthenticated_State without making a network request to the Session_Proxy
6. WHILE the session check is in progress, THE Header_Component SHALL render the header structure (logo, navigation links, cart) without displaying either the "Entrar" button or the authenticated greeting, to avoid a flash of incorrect state

### Requirement 6: Preserve Existing Header Elements

**User Story:** As a user, I want the header to retain all existing navigation elements regardless of authentication state, so that I can always access the catalog, categories, budget, and cart.

#### Acceptance Criteria

1. THE Header_Component SHALL display the navigation links (Catálogo, Categorias, Orçamento) in both Authenticated_State and Unauthenticated_State without modification
2. THE Header_Component SHALL display the cart icon with the current item count in both Authenticated_State and Unauthenticated_State without modification
3. THE Header_Component SHALL display the Dashboard link for users with artist or admin role in Authenticated_State, consistent with the existing behavior
4. THE Header_Component SHALL preserve the existing logo, scroll behavior (sticky header with backdrop blur), and responsive layout breakpoints

### Requirement 7: Multi-Tenant Isolation

**User Story:** As a platform operator, I want authentication state in the header to be fully isolated per tenant, so that logging in on one site does not affect the header of another site.

#### Acceptance Criteria

1. THE Header_Component SHALL resolve the current Tenant from the page URL path using the resolveSiteFromPath function or equivalent site resolution logic
2. THE Header_Component SHALL include the resolved Tenant id in the `X-Site-Id` header for all requests to the Session_Proxy and Logout_Proxy
3. WHEN a user is authenticated on Tenant A and visits Tenant B, THE Header_Component on Tenant B SHALL display Unauthenticated_State because the Access_Token is scoped to a single in-memory session and the Refresh_Cookie is tenant-specific
4. WHEN the user logs out on Tenant A, THE Logout_Proxy SHALL clear only the Refresh_Cookie for Tenant A (cookie named `ah_{tenantA_siteId}_refresh`) without affecting cookies for other tenants
5. THE Header_Component SHALL NOT read or depend on cookies from other tenants when determining authentication state

### Requirement 8: Responsive Behavior

**User Story:** As a user on a mobile device, I want the authenticated header state to work correctly in the mobile menu, so that I have the same account access on all screen sizes.

#### Acceptance Criteria

1. WHILE the viewport is below the `md` breakpoint (768px), THE Header_Component SHALL display the authenticated greeting, account link, and logout action inside the Mobile_Menu slide-in panel
2. WHILE the viewport is at or above the `md` breakpoint, THE Header_Component SHALL display the authenticated greeting, account link, and logout action in the Desktop_Nav horizontal bar
3. THE Mobile_Menu SHALL close after the user activates the "Sair" action
4. THE Header_Component SHALL maintain minimum touch target sizes of 44x44 pixels for all interactive elements (account link, logout button) in both Desktop_Nav and Mobile_Menu, consistent with existing button sizing

### Requirement 9: No Frontend Business Logic

**User Story:** As a developer, I want the frontend header to only consume session state from the API, so that the Fastify-first architecture is preserved.

#### Acceptance Criteria

1. THE Header_Component SHALL NOT perform token validation, JWT decoding for authorization decisions, or role-based access control logic beyond reading the role field from the Session_Endpoint response
2. THE Header_Component SHALL determine authentication state exclusively from the Session_Endpoint response or the presence of an Access_Token returned by getAccessToken()
3. THE Header_Component SHALL delegate all logout logic (token revocation, cookie clearance) to the Logout_Proxy and Fastify API
4. THE Header_Component SHALL NOT directly read or parse cookies; cookie management is handled by the API layer via HttpOnly cookies and the credentials: 'include' fetch option


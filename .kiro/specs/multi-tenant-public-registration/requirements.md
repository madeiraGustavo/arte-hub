# Requirements Document

## Introduction

Public multi-tenant registration system for Arte Hub. Allows new users to self-register on any tenant site (platform, marketplace, tattoo, music) through a public registration page. The system ensures complete tenant isolation — users are scoped to their site, cookies are per-tenant, and no privileged roles can be assigned via public registration. The backend (Fastify API) owns all authentication logic; the Next.js frontend acts as a thin proxy and UI layer.

## Glossary

- **Registration_API**: The Fastify POST `/auth/register` endpoint that creates a new user in the database
- **Registration_Page**: The Next.js page at `/{siteSlug}/register` that renders the registration form
- **Registration_Proxy**: The Next.js API route at `/api/auth/register` that proxies requests to the Fastify API
- **Registration_Form**: The client-side React component that collects email, password, and optional name
- **Tenant**: One of the configured sites (platform, marketplace, tattoo, music) identified by siteId
- **Site_Config**: The static configuration object defining a tenant's id, slug, display name, theme, and cookie name
- **Composite_Key**: The unique database constraint on (siteId, email) that allows the same email across different tenants
- **Refresh_Cookie**: The HttpOnly cookie named `ah_{siteId}_refresh` used for session persistence per tenant
- **Zod_Schema**: A Zod validation object that defines the shape and constraints of request input
- **Password_Hash**: The bcrypt hash (12 salt rounds) stored in the database instead of the plaintext password

## Requirements

### Requirement 1: Public Registration Page per Tenant

**User Story:** As a new visitor on any Arte Hub tenant site, I want to access a registration page branded for that specific site, so that I can create an account without confusion about which platform I am joining.

#### Acceptance Criteria

1. WHEN a visitor navigates to `/{siteSlug}/register`, THE Registration_Page SHALL render a registration form styled with the corresponding Tenant theme (primaryColor, gradientMain, backgroundColor) by applying those values to the page's CSS custom properties or inline styles
2. THE Registration_Page SHALL display the Tenant displayName as the visible heading text (e.g., within an `<h1>` element) and include it in the HTML document title
3. THE Registration_Page SHALL include a visible hyperlink to the corresponding login page at `/{siteSlug}/login` with link text indicating it leads to login
4. THE Registration_Page SHALL exist for each Tenant: platform, marketplace, tattoo, and music
5. WHEN a visitor navigates to `/register`, THE Registration_Page SHALL perform an HTTP redirect to `/platform/register`
6. IF a visitor navigates to `/{siteSlug}/register` where siteSlug does not match any configured Tenant (platform, marketplace, tattoo, music), THEN THE Registration_Page SHALL return an HTTP 404 response

### Requirement 2: Registration Form Component

**User Story:** As a new visitor, I want to fill in my email, password, and optionally my name, so that I can create an account on the site.

#### Acceptance Criteria

1. THE Registration_Form SHALL collect email (required, valid email format), password (required, minimum 6 characters), and name (optional, 1-100 characters) fields with matching client-side constraints
2. THE Registration_Form SHALL submit the form data to `/api/auth/register` with the header `X-Site-Id` set to the current Tenant id
3. WHEN the Registration_API returns HTTP 201, THE Registration_Form SHALL redirect the user to `/dashboard` if the Tenant id is `platform`, or to `/{siteSlug}/dashboard` for all other tenants
4. WHEN the Registration_API returns HTTP 409, THE Registration_Form SHALL display the error message "Email já cadastrado neste site"
5. WHEN the Registration_API returns HTTP 422, THE Registration_Form SHALL display each field-level validation error returned in the response body adjacent to the corresponding form field
6. IF the Registration_API returns an HTTP status other than 201, 409, or 422, THEN THE Registration_Form SHALL display a generic error message indicating the operation failed
7. THE Registration_Form SHALL disable the submit button while a request is in progress to prevent duplicate submissions
8. THE Registration_Form SHALL use the same component structure, spacing, input styling, error display pattern, and themed button styling as the existing LoginForm component

### Requirement 3: Registration Proxy Route

**User Story:** As the frontend application, I want to proxy registration requests to the Fastify API, so that the backend URL is not exposed to the browser and cookies are correctly forwarded.

#### Acceptance Criteria

1. WHEN the Registration_Proxy receives a POST request, THE Registration_Proxy SHALL forward the request body, a `Content-Type: application/json` header, and the `X-Site-Id` header to the Fastify Registration_API at `${API_URL}/auth/register`; IF the incoming request does not include an `X-Site-Id` header, THEN THE Registration_Proxy SHALL default the value to `platform`
2. WHEN the Fastify Registration_API responds, THE Registration_Proxy SHALL return the API's HTTP status code and JSON response body to the browser, and forward any `Set-Cookie` headers present in the API response
3. IF the Fastify Registration_API is unreachable or the upstream request fails, THEN THE Registration_Proxy SHALL return HTTP 503 with an error message indicating the service is unavailable
4. IF the request body is not valid JSON, THEN THE Registration_Proxy SHALL return HTTP 400 with an error message indicating the request body is invalid

### Requirement 4: Fastify Registration Endpoint Validation

**User Story:** As the system, I want to validate all registration input with Zod before processing, so that malformed or malicious data is rejected early.

#### Acceptance Criteria

1. THE Registration_API SHALL validate the request body using a Zod_Schema that requires email (valid email format, maximum 254 characters) and password (minimum 6 characters, maximum 128 characters), and accepts an optional name (1-100 characters)
2. WHEN validation fails, THE Registration_API SHALL return HTTP 422 with a response body containing the Zod flattened error details (fieldErrors and formErrors)
3. WHEN validation succeeds, THE Registration_API SHALL normalize the email to lowercase and trim leading and trailing whitespace before any further processing
4. THE Registration_API SHALL resolve the Tenant from the `X-Site-Id` request header using the resolveSiteFromRequest function
5. IF the `X-Site-Id` header is missing or contains a value not present in Site_Config, THEN THE Registration_API SHALL fall back to the `platform` Tenant

### Requirement 5: Tenant Isolation on Registration

**User Story:** As a platform operator, I want registration to be fully isolated per tenant, so that users from one site cannot interfere with another site's user base.

#### Acceptance Criteria

1. THE Registration_API SHALL create the user record with the siteId resolved from the `X-Site-Id` request header via the resolveSiteFromRequest function
2. WHEN a user registers with an email that already exists in the same Tenant, THE Registration_API SHALL return HTTP 409 with error "Email já cadastrado neste site"
3. WHEN a user registers with an email that already exists in a different Tenant, THE Registration_API SHALL create the user successfully (Composite_Key allows same email across tenants)
4. WHEN registration succeeds, THE Registration_API SHALL set the Refresh_Cookie using the Tenant-specific cookie name defined in the Site_Config cookieName field (e.g., `ah_marketplace_refresh` for marketplace tenant)
5. IF the resolved siteId does not exist in the static Site_Config, THEN THE Registration_API SHALL fall back to the `platform` tenant configuration
6. IF the `X-Site-Id` header is absent from the registration request, THEN THE Registration_API SHALL fall back to the `platform` tenant configuration

### Requirement 6: Password Security

**User Story:** As a platform operator, I want passwords to be securely hashed before storage, so that user credentials are protected even if the database is compromised.

#### Acceptance Criteria

1. THE Registration_API SHALL hash the password using bcrypt with 12 salt rounds before storing it in the database
2. THE Registration_API SHALL store only the Password_Hash in the user record and SHALL NOT include the plaintext password in API responses, logs, or error messages
3. THE Registration_API SHALL enforce a maximum password length of 72 bytes to prevent silent truncation by bcrypt
4. IF the bcrypt hashing operation fails, THEN THE Registration_API SHALL return HTTP 500 with an error message indicating a server error without revealing internal details
5. FOR ALL passwords that pass the Zod_Schema validation (minimum 6 characters, maximum 72 bytes), hashing then verifying with the original plaintext SHALL return true (round-trip property)

### Requirement 7: Role Protection on Public Registration

**User Story:** As a platform operator, I want public registration to only create client-role users, so that no one can self-assign privileged roles (admin, artist, editor).

#### Acceptance Criteria

1. THE Registration_API SHALL assign the role `client` to all users created via public registration, regardless of any role-related fields present in the request body
2. WHEN the request body contains a `role` field or any field not defined in the Registration Zod_Schema (email, password, name), THE Registration_API SHALL silently discard those fields and proceed with registration without returning an error
3. THE Registration_API SHALL hardcode the role value passed to the user-creation layer as `client`, never reading a role value from the request body
4. IF a user record created via public registration is inspected in the database, THEN the role column SHALL equal `client` and SHALL NOT equal `admin`, `artist`, or `editor`

### Requirement 8: Cookie Isolation per Tenant

**User Story:** As a platform operator, I want authentication cookies to be isolated per tenant, so that a session on one site does not grant access to another site.

#### Acceptance Criteria

1. WHEN registration succeeds, THE Registration_API SHALL set a cookie named according to the Tenant's cookieName field (e.g., `ah_marketplace_refresh` for marketplace) containing the refresh token value
2. THE Registration_API SHALL set the tenant cookie with attributes: HttpOnly, SameSite=Strict, Path=/, and maxAge of 7 days (604800 seconds)
3. IF the application is running in production, THEN THE Registration_API SHALL set the Secure attribute on the tenant cookie
4. THE Registration_API SHALL also set the legacy `refreshToken` cookie with the same value and same attributes as the tenant cookie for backward compatibility
5. WHEN a user authenticated on Tenant A makes a request to Tenant B, THE system SHALL return HTTP 403 with an error indicating site mismatch, without granting access to Tenant B resources
6. WHEN the authenticate hook resolves the user from the access token, THE system SHALL compare the user's stored siteId against the request's resolved siteId and reject the request if they do not match

### Requirement 9: Post-Registration Auto-Login

**User Story:** As a new user, I want to be automatically logged in after registration, so that I do not have to enter my credentials again immediately.

#### Acceptance Criteria

1. WHEN registration succeeds, THE Registration_API SHALL return an accessToken (JWT, 15-minute expiry) and set the Refresh_Cookie (7-day expiry), producing a token pair identical in structure and validity to the login endpoint
2. WHEN registration succeeds, THE Registration_API SHALL return HTTP 201 with the accessToken and siteId in the response body
3. WHEN the Registration_Form receives an HTTP 201 response with a valid accessToken, THE Registration_Form SHALL store the session and redirect the user to the post-registration destination (dashboard for platform, site home for other tenants) without requiring a separate login step
4. IF token generation fails after the user record has been created, THEN THE Registration_API SHALL return HTTP 500 with an error message indicating an internal failure, and the user SHALL be able to log in separately with the registered credentials

### Requirement 10: Login Multi-Tenant Consistency

**User Story:** As an existing user, I want the login flow to remain consistent with the new registration flow, so that both authentication paths use the same multi-tenant strategy.

#### Acceptance Criteria

1. THE Login endpoint SHALL resolve the Tenant from the `X-Site-Id` header via resolveSiteFromRequest, falling back to the platform Tenant when the header is absent or does not match a configured site
2. THE Login endpoint SHALL use the Composite_Key (siteId + email) to find the user
3. WHEN login succeeds, THE Login endpoint SHALL set the Tenant-specific Refresh_Cookie and the legacy `refreshToken` cookie, and return HTTP 200 with the accessToken and siteId in the response body
4. WHEN login is attempted with credentials that do not match any user in the resolved Tenant, THE Login endpoint SHALL return HTTP 401 with an error message indicating invalid credentials
5. WHEN the request body fails validation (missing or malformed email, or password shorter than 6 characters), THE Login endpoint SHALL return HTTP 422 with the Zod flattened error details

### Requirement 11: Documentation Update

**User Story:** As a developer, I want the project documentation to reflect the current multi-tenant registration system, so that new contributors can understand the authentication architecture.

#### Acceptance Criteria

1. THE documentation SHALL describe the public registration flow covering: the POST `/auth/register` endpoint, the Zod_Schema validation rules (email format, password minimum 6 characters, optional name 1-100 characters), email normalization to lowercase, and Tenant resolution via the `X-Site-Id` header
2. THE documentation SHALL list all Tenant-specific cookie names (ah_platform_refresh, ah_marketplace_refresh, ah_tattoo_refresh, ah_music_refresh) along with their cookie attributes (HttpOnly, Secure, SameSite=Strict, Path=/) and their role in session persistence
3. THE documentation SHALL document the Composite_Key strategy (siteId + email) for user uniqueness, explaining that the same email may exist across different tenants but is unique within a single tenant
4. THE documentation SHALL describe the proxy pattern for authentication routes, covering: the Next.js API route at `/api/auth/register` forwarding requests to the Fastify Registration_API, the `X-Site-Id` header propagation, and the `Set-Cookie` header forwarding from the Fastify response to the browser
5. THE documentation SHALL be located in the API project README (`apps/api/README.md`) or a dedicated architecture document linked from it

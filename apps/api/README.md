# @hub-art/api

API Fastify oficial do projeto Hub Art — responsável por toda a lógica de negócio, autenticação e acesso ao banco de dados.

## Responsabilidades

- Autenticação JWT própria (access token 15 min + refresh token 7 dias via cookie HttpOnly)
- Acesso ao banco de dados via Prisma + PostgreSQL (hospedado no Supabase)
- Validação de entrada com Zod
- Regras de negócio (ownership, RBAC, status de projetos)
- Uploads para Supabase Storage

> `apps/web` não acessa o Prisma diretamente — toda lógica de dados passa pela API Fastify.

## Scripts

| Script | Descrição |
|--------|-----------|
| `pnpm dev` | Inicia o servidor em modo desenvolvimento com hot-reload |
| `pnpm build` | Compila TypeScript para `dist/` |
| `pnpm start` | Inicia o servidor compilado |
| `pnpm typecheck` | Verifica tipos sem emitir arquivos |
| `pnpm test` | Executa todos os testes com Vitest |
| `pnpm prisma:migrate` | Aplica migrations pendentes |
| `pnpm prisma:generate` | Regenera o Prisma Client |

## Variáveis de ambiente

Copie `apps/api/.env.example` para `apps/api/.env` e preencha:

| Variável | Descrição |
|----------|-----------|
| `DATABASE_URL` | URL de conexão PostgreSQL (Supabase) |
| `JWT_SECRET` | Segredo para assinar access tokens (mínimo 32 caracteres) |
| `JWT_REFRESH_SECRET` | Segredo para assinar refresh tokens (mínimo 32 caracteres) |
| `ALLOWED_ORIGINS` | Origens permitidas pelo CORS (ex: `http://localhost:3000`) |
| `STORAGE_BUCKET` | Nome do bucket no Supabase Storage |
| `SUPABASE_URL` | URL do projeto Supabase |
| `SUPABASE_SERVICE_ROLE_KEY` | Chave de serviço do Supabase (para uploads) |
| `PORT` | Porta do servidor (padrão: `3333`) |

## Migrations

As migrations SQL estão em `apps/api/migrations/` e são aplicadas via Prisma:

```bash
pnpm prisma:migrate
```

## Autenticação Multi-Tenant

### Estratégia

A identidade do usuário é definida pela chave composta `(siteId, email)` — o mesmo email pode existir em tenants diferentes sem conflito. O tenant é resolvido a partir do header `X-Site-Id` (definido pelo proxy server-side do Next.js, nunca pelo browser diretamente).

### Tenants configurados

| Site ID | Cookie Name | Display Name |
|---------|-------------|--------------|
| `platform` | `ah_platform_refresh` | Arte Hub |
| `marketplace` | `ah_marketplace_refresh` | Toldos Colibri |
| `tattoo` | `ah_tattoo_refresh` | Studio Tattoo |
| `music` | `ah_music_refresh` | Arte Hub Music |

### Cookies

Cada tenant possui um cookie de refresh isolado. Atributos:
- `HttpOnly` — inacessível via JavaScript
- `Secure` — apenas HTTPS em produção
- `SameSite=Strict` — proteção contra CSRF
- `Path=/`
- `Max-Age=604800` (7 dias)

O cookie legado `refreshToken` é mantido para backward compatibility.

### POST `/auth/register` — Cadastro público

Endpoint público para criação de conta. Não requer autenticação.

**Validação (Zod):**
- `email` — formato de email válido (obrigatório)
- `password` — mínimo 6 caracteres (obrigatório)
- `name` — 1 a 100 caracteres (opcional)

**Comportamento:**
1. Valida body com Zod (422 se inválido)
2. Resolve tenant via header `X-Site-Id` (fallback: `platform`)
3. Normaliza email (lowercase + trim)
4. Verifica duplicidade no tenant (409 se email já existe no mesmo site)
5. Hash da senha com bcrypt (12 salt rounds)
6. Cria usuário com role `client` (hardcoded — não aceita role do body)
7. Gera par de tokens (access 15min + refresh 7d)
8. Define cookies tenant-specific + legado
9. Retorna `201 { accessToken, siteId }`

**Respostas:**
- `201` — Conta criada com sucesso + auto-login
- `409` — Email já cadastrado neste site
- `422` — Dados inválidos (detalhes do Zod)
- `500` — Erro interno

### Proxy Pattern (Next.js → Fastify)

O frontend Next.js não acessa a API diretamente do browser. Rotas de autenticação passam por um proxy server-side:

```
Browser → POST /api/auth/register (Next.js route handler)
       → POST ${API_URL}/auth/register (Fastify)
       ← Set-Cookie headers repassados ao browser
```

O proxy:
- Repassa body e header `X-Site-Id`
- Repassa `Set-Cookie` da resposta da API
- Retorna status code e body da API
- Retorna 400 para JSON inválido, 503 se API indisponível

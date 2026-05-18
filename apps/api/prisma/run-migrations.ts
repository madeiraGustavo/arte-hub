import 'dotenv/config'
import { PrismaClient } from '@prisma/client'
import { readFileSync, readdirSync } from 'fs'
import { join } from 'path'

const prisma = new PrismaClient()

/**
 * Splits a SQL file into individual executable statements.
 *
 * Handles PostgreSQL-specific syntax:
 * - DO $$ ... $$ blocks (dollar-quoted strings)
 * - CREATE FUNCTION with $$ body
 * - Single-quoted strings with escaped semicolons
 * - Double-quoted identifiers
 * - SQL comments (-- and /* ... *\/)
 *
 * Does NOT naively split on ';'. Uses a state machine to track
 * whether we're inside a string, dollar-quote, or comment.
 */
function splitStatements(sql: string): string[] {
  const statements: string[] = []
  let current = ''
  let i = 0

  while (i < sql.length) {
    const ch = sql[i]

    // ── Line comment: -- until end of line ──────────────────────────────
    if (ch === '-' && sql[i + 1] === '-') {
      const end = sql.indexOf('\n', i)
      if (end === -1) {
        current += sql.slice(i)
        i = sql.length
      } else {
        current += sql.slice(i, end + 1)
        i = end + 1
      }
      continue
    }

    // ── Block comment: /* ... */ ─────────────────────────────────────────
    if (ch === '/' && sql[i + 1] === '*') {
      const end = sql.indexOf('*/', i + 2)
      if (end === -1) {
        current += sql.slice(i)
        i = sql.length
      } else {
        current += sql.slice(i, end + 2)
        i = end + 2
      }
      continue
    }

    // ── Dollar-quoted string: $tag$ ... $tag$ ───────────────────────────
    if (ch === '$') {
      // Find the end of the opening tag
      const tagEnd = sql.indexOf('$', i + 1)
      if (tagEnd !== -1) {
        const tag = sql.slice(i, tagEnd + 1) // e.g., "$$" or "$body$"
        const closeIdx = sql.indexOf(tag, tagEnd + 1)
        if (closeIdx !== -1) {
          current += sql.slice(i, closeIdx + tag.length)
          i = closeIdx + tag.length
          continue
        }
      }
      // Not a valid dollar-quote, treat as regular character
      current += ch
      i++
      continue
    }

    // ── Single-quoted string: '...' with '' escapes ─────────────────────
    if (ch === "'") {
      current += ch
      i++
      while (i < sql.length) {
        if (sql[i] === "'" && sql[i + 1] === "'") {
          current += "''"
          i += 2
        } else if (sql[i] === "'") {
          current += "'"
          i++
          break
        } else {
          current += sql[i]
          i++
        }
      }
      continue
    }

    // ── Double-quoted identifier: "..." ─────────────────────────────────
    if (ch === '"') {
      current += ch
      i++
      while (i < sql.length && sql[i] !== '"') {
        current += sql[i]
        i++
      }
      if (i < sql.length) {
        current += sql[i] // closing quote
        i++
      }
      continue
    }

    // ── Statement terminator: ; at top level ────────────────────────────
    if (ch === ';') {
      current += ';'
      const trimmed = current.trim()
      if (trimmed && trimmed !== ';') {
        statements.push(trimmed)
      }
      current = ''
      i++
      continue
    }

    // ── Regular character ───────────────────────────────────────────────
    current += ch
    i++
  }

  // Handle trailing statement without semicolon
  const trimmed = current.trim()
  if (trimmed && trimmed !== ';') {
    statements.push(trimmed)
  }

  return statements
}

async function main() {
  const migrationsDir = join(process.cwd(), 'migrations')
  const files = readdirSync(migrationsDir).filter(f => f.endsWith('.sql')).sort()

  console.log(`Found ${files.length} migration files`)

  for (const file of files) {
    console.log(`Running: ${file}...`)
    const sql = readFileSync(join(migrationsDir, file), 'utf-8')
    const statements = splitStatements(sql)

    let applied = 0
    let skipped = 0
    let errors = 0

    for (const stmt of statements) {
      try {
        await prisma.$executeRawUnsafe(stmt)
        applied++
      } catch (e: any) {
        const msg = e.message ?? ''
        const code = e.code ?? ''
        // Known idempotent errors: already exists, duplicate
        if (
          msg.includes('already exists') ||
          msg.includes('duplicate key') ||
          code === '42710' || // duplicate_object
          code === '42P07' || // duplicate_table
          code === '42701' || // duplicate_column (for ADD COLUMN IF NOT EXISTS fallback)
          code === '42P16'    // invalid_table_definition
        ) {
          skipped++
        } else {
          errors++
          console.error(`  ❌ Statement error: ${msg.slice(0, 200)}`)
        }
      }
    }

    if (errors === 0) {
      if (skipped > 0 && applied === 0) {
        console.log(`  ⚠️  Skipped (already applied)`)
      } else {
        console.log(`  ✅ Done (${applied} applied, ${skipped} skipped)`)
      }
    }
  }

  console.log('\n🎉 All migrations processed!')
}

main().catch(e => console.error(e)).finally(() => prisma.$disconnect())

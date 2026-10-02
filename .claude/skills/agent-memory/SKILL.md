---
name: agent-memory
description: Persist session work to a local memory database and inject relevant context at the start of new sessions using vector and keyword search. Use this skill to maintain continuity across Claude Code sessions — save progress, recall past decisions, and avoid re-discovering things you already know.
---

# Agent Memory — Persistent Cross-Session Memory

This skill maintains a local memory database that survives across Claude Code sessions. It compresses and stores work done during a session, then retrieves relevant context at the start of future sessions.

## Memory Architecture

Three tiers of storage:

| Tier | Purpose | When to Write |
|------|---------|---------------|
| **Daily logs** | Session progress, bug fixes, discoveries | Default — write here almost always |
| **Long-term memory** (MEMORY.md) | Durable facts: architecture decisions, critical commands, key patterns | Rarely — only when a fact belongs in every future session |
| **Scratchpad** | Cross-session TODOs, follow-ups | When a task must persist to the next session |

## Session Start: Inject Context

At the beginning of each session, recall relevant memory:

1. Read MEMORY.md for long-term facts
2. Search daily logs for recent work on current task
3. Check scratchpad for open TODOs

Announce retrieved context:
```
📋 Memory context loaded:
- Last session: Implemented auth middleware, blocked on JWT refresh logic
- Long-term: Uses Prisma ORM, PostgreSQL, port 3000 in dev
- TODO: Write tests for the refresh token endpoint
```

## Session End: Save Context

Before ending a session, write a summary to daily logs:

```markdown
## 2026-06-19

### Work Done
- Implemented JWT refresh token endpoint at `src/auth/refresh.ts`
- Fixed race condition in session store (#42)

### Discoveries
- The `AuthService` depends on `RedisClient` — must be initialized first
- Prisma migration must run before tests: `npx prisma migrate dev`

### Open Questions
- Should refresh tokens be single-use or reusable?

### Next Session TODO
- [ ] Write integration tests for refresh endpoint
- [ ] Review PR feedback on #41
```

## Memory Commands

Use these commands during a session:

- `!memory save <note>` — Save a quick note to daily log
- `!memory recall <query>` — Search memory by keyword or semantic meaning
- `!memory todo <task>` — Add to scratchpad for next session
- `!memory distil` — Condense daily logs into MEMORY.md summary

## Key Principles

- **Default to daily logs** for almost everything
- **Promote to long-term** only when a fact will be useful in every future session
- **Keep MEMORY.md under ~50 lines** — curated wiki, not a dump
- **Use hashtags** for better searchability: `#auth`, `#database`, `#bug`

## Storage Location

```
~/.claude/memory/
├── MEMORY.md          # Long-term facts
├── scratchpad.md      # Cross-session TODOs
└── daily/
    ├── 2026-06-19.md
    └── 2026-06-18.md
```

Source: https://github.com/jayzeng/agentmemory

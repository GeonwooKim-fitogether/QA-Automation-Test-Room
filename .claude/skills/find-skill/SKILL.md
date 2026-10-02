---
name: find-skill
description: Discover and recommend the best Claude Code skill for any task by searching a catalogue of 4800+ skills from official and community sources. Use this skill when the user isn't sure which skill to use, wants to find a skill for a specific purpose, or asks "is there a skill for X?"
---

# Find Skill — Discover the Right Skill for Any Task

Search a multi-source catalogue of Claude Code skills and recommend the best match for what the user is trying to accomplish.

## Trigger

Use this skill when:
- The user asks "is there a skill for X?"
- The user describes a task and isn't sure if a skill exists
- The user wants to browse available skills by category
- The user asks "what skills should I install?"

## Skill Sources (ranked by trust)

| Source | Trust Level | Coverage |
|--------|-------------|----------|
| `anthropics/skills` | Official | Document processing, design, dev tools |
| `anthropics/claude-code` (plugins) | Official | Frontend design, web artifacts, MCP builder |
| Community (starred repos) | Community-vetted | Broad coverage, varies in quality |
| SkillsMP marketplace | Marketplace | Large catalogue, requires manual review |

## Search Workflow

### Step 1: Understand the Need
Ask (or infer from context):
- What is the user trying to do?
- Which agent (Claude Code, Codex, Cursor)?
- Any specific requirements (no dependencies, official only, etc.)?

### Step 2: Search the Catalogue

Check these known skill collections:
- **Official**: `anthropics/skills`, `anthropics/claude-code` plugins
- **Community**: travisvn/awesome-claude-skills, ComposioHQ/awesome-claude-skills
- **Specialized**: remotion-dev/skills, expo/skills, trailofbits/skills

Match by:
1. Exact keyword match in skill name or description
2. Semantic match to the user's goal
3. Category match (video, security, frontend, memory, etc.)

### Step 3: Present Results

For ≤5 results, list them compactly:
```
1. **skill-name** (source/repo) — One-sentence description
   Install: git clone https://github.com/... ~/.claude/skills/skill-name
```

For 6+ results, use a table with columns: Name | Source | Description | Install Command

### Step 4: Recommend and Confirm

- Highlight the single best match with reasoning
- Warn about skills that require external dependencies or API keys
- Ask for confirmation before installing anything

### Step 5: Install (with permission)

```bash
# Standard installation
mkdir -p ~/.claude/skills/<skill-name>
git clone https://github.com/<owner>/<repo>.git ~/.claude/skills/<skill-name>

# Or copy single SKILL.md
curl -sL <raw-url>/SKILL.md -o ~/.claude/skills/<skill-name>/SKILL.md
```

Verify: check that `~/.claude/skills/<skill-name>/SKILL.md` exists.

## Special Commands

- `find-skill --top 20` — show top 20 most-starred skills
- `find-skill --category <cat>` — filter by category (video, security, frontend, memory, writing, etc.)
- `find-skill --official` — show only Anthropic official skills
- `find-skill --stats` — show source distribution in the catalogue

## Security Note

Skills can execute arbitrary code. Only install from trusted sources. When recommending community skills, note the repository's star count and last-updated date as quality signals.

Source: https://github.com/fockus/claude-skill-find-skill

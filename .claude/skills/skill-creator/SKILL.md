---
name: skill-creator
description: Build, test, and refine new Claude Code skills through an iterative workflow. Use this skill when the user wants to create a custom skill, improve an existing skill, or benchmark skill performance.
---

# Skill Creator — Build Custom Claude Skills

This skill guides the complete lifecycle of Claude Code skill development: from capturing intent through writing, testing, evaluating, and packaging.

## Modes

Choose a mode based on what the user needs:

- **Create** — Build a new skill from scratch
- **Improve** — Refine an existing skill based on feedback
- **Eval** — Run structured tests to measure skill quality
- **Benchmark** — Compare skill vs. baseline across multiple runs

---

## Mode: Create

### Step 1: Capture Intent

Ask clarifying questions:
- What should this skill do? When should it trigger?
- What are the expected inputs and outputs?
- What does "success" look like?
- Are outputs objectively verifiable (code, data) or subjective (writing style)?

### Step 2: Write SKILL.md

Create `.claude/skills/<skill-name>/SKILL.md` with this structure:

```markdown
---
name: <skill-name>
description: <One sentence. Be specific about when this triggers. Mention key contexts where it applies.>
---

# <Skill Name> — <Tagline>

<Purpose and overview>

## Trigger

Use this skill when: <specific conditions>

## Workflow

<Step-by-step instructions>

## Key Principles

<The "why" behind important instructions>
```

**Naming guidelines:**
- Description should be somewhat assertive to prevent undertriggering
- Keep SKILL.md under 500 lines
- Explain the *why* behind rules, not just the *what*
- Use imperative language ("Do X", "Write Y")

### Step 3: Test

Draft 2-3 realistic test prompts that real users would actually type. Run each prompt:
- Once WITH the skill active
- Once WITHOUT the skill (baseline)

### Step 4: Evaluate

Compare outputs:
- Does the skill produce meaningfully better results?
- Are there cases where the skill makes things worse?
- Is the description triggering at the right times?

### Step 5: Iterate

Based on evaluation:
- Remove instructions that don't improve output
- Add instructions for observed failure modes
- Generalize fixes — don't overfit to test cases

---

## Mode: Improve

1. Read the existing SKILL.md
2. Ask what's not working (under-triggering, wrong outputs, etc.)
3. Propose targeted edits with reasoning
4. Re-run evals to verify improvement

---

## Mode: Eval

For each test case:
1. Record the prompt
2. Run with skill → capture output
3. Run without skill → capture output
4. Score on: correctness, completeness, style, adherence to skill instructions
5. Present comparison table

---

## Mode: Benchmark

Run the skill 10 times on the same prompt, measure:
- Output consistency (variance)
- Time to completion
- Error rate

Report: mean quality score, standard deviation, p50/p95 latency

---

## Security Note

Skills must not contain malware, exploit code, or content that could compromise system security.

Source: https://github.com/anthropics/skills/tree/main/skills/skill-creator

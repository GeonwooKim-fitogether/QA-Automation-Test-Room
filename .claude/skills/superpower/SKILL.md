---
name: superpower
description: Senior developer workflow that forces spec writing, implementation planning, and test code before any production code — dramatically improving first-attempt quality. Use this skill for any non-trivial feature, bug fix, or refactor task.
---

# Superpower — Senior Developer Workflow

This skill enforces a disciplined engineering methodology: spec first, plan second, tests third, code last. It prevents the common AI failure of jumping straight to implementation.

## Core Principle

**Invoke relevant skills BEFORE any response or action.** Even a 1% chance a skill applies demands its invocation. This is not negotiable.

## Priority Hierarchy

1. User's explicit instructions — highest authority
2. Skill instructions — override default behavior
3. Default system behavior — lowest priority

## Workflow: Four Phases Before Code

### Phase 1: Spec (What are we building?)

Before writing a single line of code, produce a concise spec:
- **Goal**: One sentence describing what success looks like
- **Inputs/Outputs**: What goes in, what comes out
- **Constraints**: Performance, compatibility, security requirements
- **Out of scope**: Explicitly list what we are NOT doing
- **Open questions**: Anything that needs user clarification

Do not proceed until the spec is confirmed.

### Phase 2: Implementation Plan (How will we build it?)

Write a numbered step-by-step plan:
```
1. [Component/file to create or modify]
   - What changes and why
   - Expected outcome
2. [Next component]
   ...
```

Identify:
- Which existing code to reuse
- What new abstractions (if any) are justified
- Potential risks or tricky parts

### Phase 3: Test Code First (TDD)

Write failing tests before implementation:
- Unit tests for core logic
- Edge cases and error conditions
- Integration tests if applicable

Run tests to confirm they fail (red phase).

### Phase 4: Implementation

Now write the minimum production code to make tests pass:
- Follow the plan from Phase 2
- Apply Andrepathy rules: simplicity first, surgical changes
- Stop when tests go green — don't gold-plate

### Phase 5: Verify

- Run full test suite
- Review the diff: does every changed line trace to the spec?
- Confirm with the user before closing out

## Red Flags — Don't Skip the Process For:

- "This is just a simple change" — simple tasks still benefit from clarity
- "I need to see the code first" — the spec comes before the code
- "The user seems to want speed" — quality and speed are not opposites

Source: https://github.com/obra/superpowers

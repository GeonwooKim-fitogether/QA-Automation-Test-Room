---
name: frontend-design
description: Guidance for distinctive, intentional visual design when building new UI or reshaping an existing one — prevents generic "AI-slop" aesthetics. Use this skill whenever building or redesigning frontend UI components, pages, or full interfaces.
---

# Frontend Design — Distinctive UI, Not AI Defaults

Act as a design lead at a small studio known for giving every client a unique visual identity. Make deliberate, opinionated choices about palette, typography, and layout specific to the brief, and take one justified aesthetic risk.

## Ground It in the Subject

Before touching code:
- Pin down the concrete subject, audience, and page's single job
- Use the subject's own world — its materials, instruments, artifacts, and vernacular — as the source of distinctive choices
- Build with the brief's real content throughout (not lorem ipsum)

## Design Principles

**Hero is a thesis:** Open with the most characteristic thing in the subject's world — headline, image, animation, or interactive moment.

**Typography carries personality:** Pair display and body faces deliberately. Set a clear type scale with intentional weights, widths, and spacing.

**Structure encodes information:** Use structural devices (numbering, dividers, labels) meaningfully, not decoratively.

**Leverage motion deliberately:** Use animation where it serves the subject. Avoid over-animation that reads as AI-generated.

**Match complexity to vision:** Minimalist designs need precision. Maximalist designs need elaborate execution.

## Process: Two-Pass Approach

### Pass 1: Brainstorm (before code)

Define a compact token system:
- **Color**: 4–6 named hex values (not just `primary` and `secondary`)
- **Type**: 2+ typeface roles (e.g. display / body / mono)
- **Layout**: one-sentence description + ASCII wireframe
- **Signature element**: the one memorable, unexpected thing

Example:
```
Colors: #1A1A2E (void), #E94560 (signal), #0F3460 (depth), #F5F0E8 (paper)
Type: "Syne" (display, weight 800) + "Inter" (body, weight 400)
Signature: Oversized page numbers that bleed off the edge
```

### Pass 2: Review before coding

Check the token system against the brief:
- Does the palette feel generic (purple gradient, teal accent on white)? Rethink.
- Does the typography choice feel deliberate or is it just a safe system font?
- Is there actually a signature element, or is everything equally loud?

## What to Avoid

**Generic defaults (AI slop):**
- Purple or teal gradient hero sections
- Card grids with rounded corners and drop shadows everywhere
- "Get Started" CTA buttons on every page
- System font stacks used as primary display type
- Centered text with a subtitle and a button as the entire hero

**Over-animated patterns:**
- Elements that all fade-in on scroll
- Particle effects or floating orbs as decoration
- Hover states on everything

## Restraint and Self-Critique

- Spend boldness in one place (the signature element)
- Keep everything else quiet and disciplined
- Build to a quality floor: responsive design, keyboard focus, reduced motion respect

## Writing in Design

- Words should make designs easier to understand and use
- Write from the end user's perspective, active voice
- Be specific rather than clever; use plain language
- Treat errors and emptiness as moments for direction, not mood

Source: https://github.com/anthropics/claude-code/tree/main/plugins/frontend-design

---
name: humanizer
description: Remove AI writing patterns from text and rewrite in a natural, human voice — eliminating the 33+ telltale signs of AI-generated content including em-dash overuse, sycophantic openers, hedge stacking, and manufactured drama. Use this skill when the user wants to make text sound more natural or less AI-generated.
---

# Humanizer — Remove AI Writing Patterns

Scan text for AI tells, remove them, and rewrite with natural rhythm and voice. The goal is not just pattern removal — the result should have personality, opinions, and genuine voice.

## The Hard Rule

**The final rewrite contains no em dashes (—) or en dashes (–).** These are among the most reliable AI tells. Use commas, periods, or restructure the sentence instead.

## Voice Calibration (if samples provided)

If the user provides writing samples, analyze before rewriting:
- **Sentence rhythm**: short punchy sentences vs. long flowing ones?
- **Word choice**: formal or casual? Technical or plain?
- **Punctuation habits**: heavy comma use? Semicolons? Parentheticals?
- **Tone**: dry, warm, blunt, playful?

Match the rewrite to these patterns.

## AI Tell Patterns to Eliminate

### Content Patterns
- **Significance inflation**: "pivotal moment", "transformative", "game-changing", "revolutionary" for ordinary things
- **Vague attributions**: "studies show", "experts say", "research indicates" without specifics
- **False urgency**: treating mundane topics as urgent or critically important

### Language Patterns
- **AI vocabulary**: "delve", "leverage", "utilize", "facilitate", "comprehensive", "robust", "seamless", "groundbreaking"
- **Copula avoidance**: contorting sentences to avoid "is/are/was"
- **Negative parallelism**: "not only X but also Y" constructions stacked repeatedly
- **Passive voice overuse**: "it was decided that", "this can be seen in"

### Style Patterns
- **Em dash overuse** (—): the single most reliable AI tell — eliminate all of them
- **Title case headings** when sentence case fits better
- **Rule-of-three forcing**: artificially grouping everything into three points
- **Manufactured punchlines**: ending every section with a "mic drop" conclusion

### Communication Patterns
- **Sycophantic openers**: "Great question!", "Absolutely!", "Of course!", "Certainly!"
- **Collaborative artifacts**: "Let's explore...", "We can see that...", "Together we'll..."
- **Knowledge-cutoff disclaimers** when not needed
- **Hedge stacking**: "may possibly tend to somewhat suggest"

### Filler and Hedging
- **False ranges**: "anywhere from X to Y" when a single number would do
- **Persuasive authority tropes**: "As we know...", "It's worth noting that..."
- **Staccato drama**: Short. Sentences. For. Effect. (when not genuine)
- **Generic conclusions**: "In conclusion, X is important because it matters"

## Rewrite Process

1. **Detect**: Mark every AI tell in the text
2. **Score**: Rate overall AI-ness (0-100, where 100 = obvious AI)
3. **Rewrite**: Remove patterns and restore natural voice
4. **Check**: Verify no em dashes remain; verify the text has personality

## What Not to Change

**False positives** — do not flag these:
- Formal vocabulary used genuinely (not forced)
- A single em dash used strategically
- Passive voice when it genuinely fits
- Hedging when genuine uncertainty exists

Only clusters of tells indicate AI writing — isolated instances don't.

## Output Format

Present:
1. AI-tell score before rewrite (e.g. "72/100 — clearly AI")
2. Key patterns found (bulleted list)
3. Rewritten text
4. Brief note on what changed

Source: https://github.com/blader/humanizer

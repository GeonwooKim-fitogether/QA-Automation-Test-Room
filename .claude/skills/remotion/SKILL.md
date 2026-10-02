---
name: remotion
description: Guidance for building programmatic videos with Remotion (React-based video framework). Corrects common AI mistakes in animation timing, audio sync, composition structure, and media handling. Use this skill for any Remotion video development task.
---

# Remotion — Programmatic Video with React

This skill loads Remotion-specific best practices so Claude generates correct, idiomatic Remotion code. It corrects the animation timing errors, audio sync mistakes, and composition structure issues that AI models commonly produce.

## Core Concepts

### Time Model
Remotion uses **frames**, not milliseconds or seconds, as the primary time unit:
- `useCurrentFrame()` returns the current frame number (0-indexed)
- `fps` is defined in the `<Composition>` component
- Convert: `seconds = frame / fps`

**Never use `Date.now()`, `setTimeout()`, or CSS animations** — all timing must derive from `useCurrentFrame()`.

### Composition Structure
```tsx
// Root.tsx — register all compositions here
export const RemotionRoot = () => (
  <>
    <Composition
      id="MyVideo"
      component={MyVideo}
      durationInFrames={150}  // 5 seconds at 30fps
      fps={30}
      width={1920}
      height={1080}
    />
  </>
);
```

## Animation Timing Rules

### Use `interpolate()` for smooth transitions
```tsx
import { useCurrentFrame, interpolate } from 'remotion';

const frame = useCurrentFrame();
const opacity = interpolate(
  frame,
  [0, 30],          // input range: frames 0 to 30
  [0, 1],           // output range: 0 to 1
  { extrapolateRight: 'clamp' }  // always clamp unless you want extrapolation
);
```

**Always specify `extrapolateLeft` and `extrapolateRight`** — default behavior extrapolates infinitely, which almost never what you want.

### Use `spring()` for natural motion
```tsx
import { spring, useCurrentFrame, useVideoConfig } from 'remotion';

const frame = useCurrentFrame();
const { fps } = useVideoConfig();
const scale = spring({ frame, fps, config: { damping: 10, mass: 0.5 } });
```

### Sequence and Series for timing
```tsx
import { Sequence, Series } from 'remotion';

// Sequence: offset a component's frame 0 by N frames
<Sequence from={30} durationInFrames={60}>
  <MyComponent />
</Sequence>

// Series: stack components one after another
<Series>
  <Series.Sequence durationInFrames={60}><SceneA /></Series.Sequence>
  <Series.Sequence durationInFrames={90}><SceneB /></Series.Sequence>
</Series>
```

## Audio Sync

```tsx
import { Audio, useCurrentFrame } from 'remotion';

// Correct: use staticFile() for local assets
<Audio src={staticFile('audio.mp3')} />

// To sync animation to audio beats:
// Pre-calculate beat frames and use interpolate() with those keyframes
const beatFrames = [0, 15, 30, 45]; // frames where beats land
```

**Never** use Web Audio API or `AudioContext` — Remotion handles all audio internally.

## Media Handling

```tsx
import { Img, Video, staticFile } from 'remotion';

// Always use staticFile() for local assets
<Img src={staticFile('logo.png')} />
<Video src={staticFile('clip.mp4')} />

// For remote assets, preload them
import { prefetch } from 'remotion';
await prefetch('https://example.com/asset.mp4');
```

## Common Mistakes to Avoid

| Wrong | Correct |
|-------|---------|
| `Date.now()` for timing | `useCurrentFrame()` |
| CSS `animation:` or `transition:` | `interpolate()` / `spring()` |
| `setTimeout` / `setInterval` | `<Sequence from={N}>` |
| `Math.random()` in render | Pre-calculated random values (use seed) |
| Mutating state in render | Pure functional components only |

## Rendering

```bash
# Preview
npx remotion studio

# Render to file
npx remotion render MyVideo out/video.mp4

# Render with Lambda
npx remotion lambda render MyVideo
```

Source: https://www.remotion.dev/docs/ai/skills (remotion-dev/skills)

---
name: claude-video-learning
description: Turn a video (YouTube URL or local file) into a polished, reusable LEARNING PACKET — a per-video folder containing a Top-down STEP-structured educational document, delivered as a landscape A4 PDF plus a self-contained interactive HTML, with the key diagrams REDRAWN as clean vector (SVG) graphics in a unified design. Use this whenever the user wants to study, learn from, teach, archive, or build study/교육/강의 material from a video — not just a quick answer. For a quick "what does this video say" summary or one-off Q&A, use the lighter `claude-video` skill instead.
---

# Claude Video for Learning — Build a Study Packet from Any Video

This skill produces a **durable educational deliverable** from a video, not an ephemeral chat summary. The output is a per-video folder with a landscape PDF + interactive HTML "study packet": conclusion-first, segmented into STEPs the reader can stop at by depth, with every key diagram **reconstructed as inline SVG** so it is crisp, on-brand, and free of webcam/subtitle/cropping artifacts.

It is the "learning" evolution of `claude-video`. Division of labor:
- **`claude-video`** → quick: watch, summarize, answer questions in chat. No files.
- **`claude-video-learning`** (this) → produce a saved, designed study packet (folder + PDF + HTML).

## Trigger

Use when the user wants lasting study material from a video, e.g.: "이 강의 교육자료로 만들어줘", "정리해서 저장해줘", "공부할 수 있게", "study guide / learning material from this video", "이 폴더에 계속 영상 정리". Also use when they ask to save a video analysis to a folder, or build a slide-deck-like document from a talk/lecture/tutorial.

## Prerequisites

See `reference/pipeline.md` for the full, OS-aware list. In short: `ffmpeg`/`ffprobe`, `yt-dlp` (may only resolve via `python -m yt_dlp`), a headless **Chrome/Edge** (HTML→PDF), and Python with `pypdf` + `PyMuPDF`(fitz) for verification, `Pillow` only if you fall back to raster frames. Detect, don't assume the platform — this runs on Windows often.

## Workflow

### Step 0 — Resolve the library and per-video folder
- The user's named folder is the **library root**. Inside it, create a **per-video subfolder** so many videos stay separated.
- **Folder name = `[분류][날짜][제목]`** (category / upload-date `YYYY-MM-DD` / short slug). Category controlled vocab: `AI·에이전트 · 데이터 · 개발 · 프로덕트 · 비즈니스 · 스포츠·축구 · 기타` (tag from content; ask only if genuinely ambiguous). Brackets are valid on Windows.
- Maintain a root **`00_README.md`** index: a table of 분류 · 날짜 · 제목 · 한 줄 요약 · 폴더 링크. Create if absent; append a row each run.
- Use a separate **work dir** (temp) for the raw video and intermediate frames. Only finished artifacts land in the user's folder.

### Step 1 — Preflight
Probe dependencies and pick what resolves (`yt-dlp` → `python -m yt_dlp` → `py -m yt_dlp`). Give OS-correct install hints if missing. Details + commands: `reference/pipeline.md`.

### Step 2 — Download video (alone) + Step 3 — frames + Step 4 — transcript
Follow `reference/pipeline.md` exactly. The non-obvious, hard-won rules:
- **Download the video by itself** — never bundle `--write-sub` with it (a subtitle 429 aborts the whole download). Detect the real output file (it may be `.webm`/`.mkv`, not `.mp4`).
- Frames: a ~512px pass is fine for *reading/understanding*; re-extract any diagram you will redraw at **1280px** so you can read its fine labels.
- Subtitles: separate fault-tolerant command, prefer the **original language** (`*-orig`), **retry on HTTP 429**, write UTF-8. Never trust the terminal echo for non-ASCII — verify by reading the file.

### Step 5 — Understand deeply (this is where quality comes from)
- **Read the frames densely** (most of them for slide/lecture/demo content) and the **full transcript in chunks**. You cannot redraw a diagram you have not actually looked at in hi-res.
- Note the video's logical structure (its natural sections become your STEPs) and list the diagrams worth reconstructing.
- **Capture the resource list — for a tutorial this IS the payoff.** The specific **tools / sites / URLs** the video names are the actionable core, but they hide where auto-captions can't reach: the **full video description**, the **pinned comment / linked guide**, and the **on-screen end-card** ("툴 바로가기"). Auto-captions mangle brand names (real example: caption "디자인 스펠스 워브 포트폴리오스" = `designspells.com` + `walloffportfolios.com`; "21st.dev" never appeared in the captions at all). So: read the **full `description`** (info-json, not just the head), re-extract & read the frames showing URLs/end-cards, and **verify exact spellings**. **Never genericize or guess a brand/URL from a garbled caption** — a real failure was guessing "Awwwards" instead of the creator's actual `designspells.com` / `walloffportfolios.com` / `21st.dev`. If you genuinely can't verify a name, say so rather than invent one.

### Step 6 — Author the educational document
Write `분석.html` using `reference/document-template.html` as the skeleton. Two pillars:

**(a) Document architecture — Top-down, easy→hard, STEP-segmented.** Brief the reader like a CEO: conclusion first, details last. Number the sections as **STEPs**, each tagged with audience + reading time (e.g. `누구나 · 30초`, `실무 · 어려움`) so they stop at the depth they need. A clickable TOC + STEP banners + appendix replace collapsibles (PDF can't collapse). Typical flow: STEP 0 (30초 결론) → STEP 1 (큰 그림 + 대표 도식) → STEP 2 (핵심 개념) → STEP 3…N (단계별 심층, 도식 + 설명을 *풀어서* 서술 + "왜 중요한가") → 실습 재현 → 적용 포인트(사용자 맥락) → 부록. **Preserve detail** — expand explanations into prose; do not compress to bullet skeletons.

**(b) Choose each figure's medium by WHERE THE VALUE LIVES — this is the core judgment of this skill.** Two media, two jobs:
- **Concept / structure → reconstruct as inline SVG.** Architectures, taxonomies, workflows, relationships, mental models the speaker conveys verbally, or a slide that is itself a diagram. Redraw it clean in the document palette. See `reference/svg-diagrams.md` (shared style, global arrowhead marker, node-color classes, patterns: box-grid / flow / tree / node-link graph).
- **The actual thing → use the REAL captured frame, cleaned only.** Live UI, the product/result, before→after, "proof it actually works." For a screencast/demo, **the screenshot IS the content** — it shows the real software running. Crop off the webcam/subtitle bar/overlay, but **do NOT redraw it into abstract boxes** — that "각색" kills the authenticity and impact (this was a real failure: a tutorial reconstructed as SVG felt dead). Treatment = clean crop only (no heavy annotation). Base64-inline it (see `reference/pipeline.md` §6) so the HTML stays self-contained.

**Content-type default:**
- **Slide / whiteboard lecture** (e.g. an architecture talk) → SVG-heavy; the slides are diagrams worth redrawing.
- **Screencast / demo / tutorial / product walkthrough** → **real-capture-heavy**; use cleaned screenshots for every "show the actual screen" moment, and SVG ONLY for the overview/workflow and concepts the video never shows cleanly.
- Most docs are a **hybrid**: SVG for the mental model, real screenshots for the demos/results. Decide per figure.

Give every conceptual STEP at least one figure; never leave a concept text-only when a diagram or screenshot illustrates it. Wrap each `<figure>` (SVG or `<img class="shot">`) in the consistent accent-tinted card.

**Include a 도구·참고 사이트 (바로가기) table** — list every tool/site/URL the video names (verbatim, exact spelling). For a tutorial this is the single most-used part of the doc. Put it in the appendix, and surface key sites inline at the STEP where each is used. Dropping these is critical-level data loss, not a cosmetic omission.

### Step 7 — Render the PDF + deliver interactive HTML
Per `reference/pipeline.md`:
- The template is **landscape A4** in `@media print` (video is 16:9 — portrait crams/cuts it) and a comfortable web layout + **click-to-zoom lightbox** in `@media screen`.
- **Pack pages densely — do NOT force a page-break before every STEP.** Per-STEP `pagebreak`s leave page bottoms empty; let content flow (STEP banners are enough separation). **Cap figure heights** (`@media print` — concept SVG ~48–64mm, screenshots ~74–92mm) so a figure + its surrounding text share a page. Images can't split across pages, so a too-tall figure jumps wholesale to the next page leaving a gap — keep figures modest, and tighten block spacing if pages still look sparse.
- Render with **`chrome --headless=new … --print-to-pdf`** to an **absolute temp path**, then copy into the destination (old headless drops to 1 page; writing directly into the Drive/bracketed folder hits `액세스 거부`; Chrome can *read* input from there fine).
- Pure inline-SVG HTML is self-contained as-is. If you used any real screenshots (`<img class="shot">`), **base64-inline them** (pipeline.md §6) so the delivered `분석.html` stays portable (0 `src="shots/"` / `src="frames/"` left). Verify `data:image` count == screenshot count.

### Step 8 — Verify before delivering
- pypdf: confirm **landscape** + sane page count.
- **Rasterize EVERY page** to PNG (PyMuPDF `get_pixmap`) and actually view them. SVG bugs are silent: an **undefined CSS color class makes a `<rect>` default to solid black**; overlapping coordinates make labels unreadable. Fix before delivering.

### Step 9 — Index + report
Append the row to `00_README.md`. Tell the user the folder layout: `[교육자료] *.pdf` is the quick-view file, `분석.html` is the interactive web version (click diagrams to zoom), `frames/` + transcript are reference assets.

## Key Principles (the why)

- **Right medium per figure — concept vs. reality.** Redrawn SVG turns a slide diagram into a designed textbook; but for a live demo, the real screenshot is the value — it proves the software actually works, and abstracting it into boxes kills the impact. Ask each time: "is the value the CONCEPT (→ SVG) or seeing the ACTUAL screen (→ cleaned capture)?" Either way, accuracy comes from first *looking* at the hi-res frame.
- **Progressive depth.** Readers have different needs; the STEP/audience-tag structure lets one document serve a CEO (STEP 0–1) and an implementer (through the appendix). Order easy→hard, conclusion-first.
- **Detail is the point.** This is a learning artifact, not a teaser. Expand the speaker's reasoning; keep the actionable specifics (queries, schemas, parameters).
- **Robust over clever.** Most failures are environmental (platform, encoding, rate-limits, file paths). Follow the `reference/pipeline.md` gotchas literally.
- **Verify what's silent.** Always rasterize and look — SVG/render bugs don't throw.
- **Specifics over guesses — capture, don't genericize.** Named tools, sites, URLs, prompts, and parameters are the actionable payoff (especially for tutorials). Take them verbatim from the description / pinned comment / on-screen end-card, not from garbled auto-captions. Inventing a plausible-but-wrong brand/URL is worse than omitting it — and omitting the resource list entirely is critical data loss.

## Notes
- Best for talks/lectures/tutorials/demos under ~30 min. For longer, lean on sparse frames + transcript and reconstruct only the hero diagrams.
- If the video is private/region-locked/sign-in, tell the user.
- Cite original diagram sources (e.g. the slide's footer URL) in figure captions; the body shows your reconstruction.

Lineage: evolved from `claude-video` (source: https://github.com/bradautomates/claude-video).

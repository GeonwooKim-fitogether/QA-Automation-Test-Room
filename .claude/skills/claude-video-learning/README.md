# Claude Video for Learning 🎬📚

A Claude Code **skill** that turns any video (YouTube URL or local file) into a polished, reusable **learning packet** — not a throwaway chat summary.

For each video it produces a dedicated folder containing:
- **`[교육자료] *.pdf`** — a landscape‑A4 educational document: conclusion‑first, segmented into **STEP**s the reader can stop at by depth (CEO → implementer), with the key diagrams **redrawn as clean vector (SVG) graphics** in one unified design.
- **`분석.html`** — the same document as a self‑contained interactive web page (click any diagram to zoom full‑screen).
- **`frames/`, `transcript_*.txt`, `subtitles_*.vtt`** — reference assets.
- A root **`00_README.md`** index across all videos in the library.

It is the "learning" evolution of the lighter [`claude-video`] skill (quick watch/summarize/Q&A). Keep both:
| Skill | Use it for |
|-------|-----------|
| `claude-video` | "What does this video say?" — fast summary / Q&A in chat, no files |
| `claude-video-learning` | "Make this into study material" — saved, designed PDF + HTML packet |

## What makes the output good
- **Diagrams are reconstructed, not screenshotted.** Source frames carry a presenter webcam, burned‑in subtitles, low resolution, and a clashing style. This skill *understands* each diagram and redraws it as inline SVG — crisp at any zoom, on‑brand, text‑selectable.
- **Top‑down, progressive depth.** STEP 0 (30‑sec conclusion) → big picture → concepts → deep dives → hands‑on → "how it applies to us" → appendix. Each STEP tagged with audience + reading time.
- **Landscape A4** because video is 16:9 — wide diagrams fill the page instead of being cramped.
- **Self‑contained & portable.** Inline SVG means the HTML works anywhere with no external image files.

## Install

This is a standard Claude Code skill — copy the folder into a skills directory:

```bash
# Per‑user (available in every project)
cp -r claude-video-learning ~/.claude/skills/

# …or per‑project (share via your repo)
cp -r claude-video-learning <repo>/.claude/skills/
```
Restart/refresh Claude Code; it auto‑discovers the skill. Verify by asking: *"이 강의 영상 교육자료로 만들어줘 <URL>"*.

### Dependencies (install once per machine)
| Tool | Why | Install |
|------|-----|---------|
| ffmpeg / ffprobe | frame extraction | Win: `winget install Gyan.FFmpeg` · mac: `brew install ffmpeg` · linux: `apt install ffmpeg` |
| yt-dlp | video + caption download | `pip install yt-dlp` (skill auto‑falls back to `python -m yt_dlp` if not on PATH) |
| Chrome or Edge (headless) | HTML → PDF | usually already installed |
| Python: `pypdf`, `pymupdf` | PDF verify + page rasterization | `pip install pypdf pymupdf` |
| Python: `pillow` | *only* if falling back to raster frames | `pip install pillow` |

No API key needed when the video has captions. (Whisper key only for the no‑caption fallback.)

## Usage

> "https://youtu.be/… 이 강의를 `G:\내 드라이브\학습자료` 폴더에 교육자료로 정리해줘"

The skill will: make a `[분류][날짜][제목]` subfolder → download/extract/transcribe → understand the content → author the STEP document with reconstructed SVG diagrams → render the landscape PDF + interactive HTML → verify by rasterizing every page → update the library index.

## Files in this skill
```
claude-video-learning/
├── SKILL.md                        # the skill instructions Claude follows
├── README.md                       # this file (humans)
└── reference/
    ├── pipeline.md                 # exact download/frame/subtitle/render commands + platform gotchas
    ├── document-template.html      # the PDF/HTML skeleton (CSS, STEP structure, lightbox, landscape)
    └── svg-diagrams.md             # SVG reconstruction patterns + shared style + checklist
```

## Notes & known gotchas (baked into the skill)
- Download the video *separately* from subtitles (a caption 429 otherwise aborts the whole download).
- Output the PDF to a temp path then copy — writing directly into a Drive‑synced / bracketed‑Korean folder throws `액세스 거부`.
- Use `chrome --headless=new` (old headless renders only ~1 page of a long doc).
- Every SVG color class must be defined, or `<rect>`s render solid black — always rasterize and eyeball the pages.

Best results for talks / lectures / tutorials / demos under ~30 minutes.

Lineage: evolved from `claude-video` (https://github.com/bradautomates/claude-video).

---
name: claude-video
description: Quickly watch and analyze a video (YouTube URL or local file) and answer in chat — summarize it, describe what's on screen, pull key moments with timestamps, or answer specific questions. Downloads the video, extracts frames, and reads captions for multimodal analysis. Use for fast, throwaway analysis. If the user instead wants a SAVED, designed study/교육 packet (a per-video folder with a PDF + interactive HTML and redrawn diagrams), use the `claude-video-learning` skill.
---

# Claude Video — Watch Any Video (quick analysis)

Download a video, extract frames, read its captions, and analyze it **in chat** — summary, on-screen description, timestamped key moments, or answers to the user's specific questions. Lightweight and file-free by default.

> **Routing:** This skill answers *about* a video in the conversation. If the user wants a durable, designed **learning packet** ("교육자료/학습자료로 만들어줘", "정리해서 저장", "study guide", per-video folder + PDF + HTML with reconstructed diagrams), switch to **`claude-video-learning`**.

## Trigger
- A YouTube/Vimeo/X/TikTok/Twitch URL or a local video file path, plus
- a request to **watch / analyze / summarize / understand / 요약 / 무슨 내용** or to **answer questions** about it.

## Workflow

### 1. Preflight — detect, don't assume the platform
- `ffmpeg`/`ffprobe` (`command -v ffmpeg`).
- `yt-dlp` may not be on PATH — probe `yt-dlp --version` → `python -m yt_dlp --version` → `py -m yt_dlp --version` and use whichever resolves as `<ytdlp>`.
- Whisper API key only if the video has no captions (Groq/OpenAI; `~/.config/watch/.env`).
- Install hints — Win: `winget install Gyan.FFmpeg` + `pip install yt-dlp`; mac: `brew install ffmpeg yt-dlp`; linux: `apt install ffmpeg` + `pip install yt-dlp`.

### 2. Download the video ALONE (never bundle subtitles)
A subtitle error (HTTP 429) aborts the whole download if bundled. Download video by itself + metadata; cap resolution:
```bash
<ytdlp> -o "<workdir>/video.%(ext)s" -f "bv*[height<=720]+ba/b[height<=720]/b" --write-info-json "<URL>"
```
The merged file may be `.webm`/`.mkv`, not `.mp4` — detect it: `VID=$(ls <workdir>/video.* | grep -vE '\.(json|vtt|srt)$' | head -1)`. Read title/duration from `video.info.json` (UTF-8; parse the file, don't trust terminal echo).

### 3. Extract frames (density by length)
< 2 min ~2 fps · 2–10 min ~1 fps · 10–30 min 1/30s · > 30 min ~100 frames total. 512px wide is fine for analysis:
```bash
ffmpeg -i "$VID" -vf "fps=1/30,scale=512:-1" -q:v 3 "<workdir>/frames/frame_%03d.jpg" -y
```
Support `--start HH:MM:SS --end HH:MM:SS` for denser extraction on a segment when the user asks about one part.

### 4. Transcript (separate, fault-tolerant)
```bash
<ytdlp> --skip-download --write-auto-sub --sub-lang "<lang>" --convert-subs vtt -o "<workdir>/video.%(ext)s" "<URL>"
```
- Prefer the **original language** (`*-orig`); check `automatic_captions` keys in the info-json.
- **On HTTP 429:** retry once or twice (rate-limit, not fatal). If no captions exist, fall back to Whisper on the audio.
- Parse VTT → clean timestamped text; write UTF-8.
- **Encoding:** Windows consoles garble non-ASCII — verify by reading the saved file, not the terminal echo.

### 5. Analyze and answer
- **Read frames densely** (most of them for slide/lecture/demo content; sampling is fine for talking-head/B-roll) and the **full transcript in chunks** — a shallow pass yields a thin answer and forces a redo.
- Describe what's visually happening, summarize the content/arguments/story, cite **timestamps** for key moments, and answer the user's specific questions.
- Output to chat. If the user later wants this saved as a polished document, hand off to `claude-video-learning`.

## Notes
- Best for clips under ~25 min; for longer, lean on sparse frames + transcript.
- Always cite timestamps when referencing specific moments.
- If the video is private/region-locked/sign-in-required, tell the user.
- yt-dlp may warn about a missing JS runtime / impersonation target — usually non-fatal; proceed unless the download actually fails.

Source: https://github.com/bradautomates/claude-video

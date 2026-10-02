# Pipeline Reference — exact commands & hard-won gotchas

Placeholders: `<WORK>` = temp work dir (e.g. `C:/Users/<you>/AppData/Local/Temp/cvwork`), `<DEST>` = the per-video folder inside the user's library, `<URL>` = video URL, `<YTDLP>` = whatever resolves below, `<CHROME>` = headless browser path.

## 0. Dependencies — detect, don't assume

```bash
command -v ffmpeg; command -v ffprobe         # frame extraction
yt-dlp --version || python -m yt_dlp --version || py -m yt_dlp --version   # use whichever works as <YTDLP>
python -c "import pypdf, fitz; print('verify libs ok')"                    # pypdf + PyMuPDF for verification
```
Headless browser (Windows typical paths):
- `C:/Program Files/Google/Chrome/Application/chrome.exe`
- `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`

Install hints:
- Windows: `winget install Gyan.FFmpeg` · `pip install yt-dlp pypdf pymupdf pillow` (call yt-dlp via `python -m yt_dlp` if not on PATH)
- macOS: `brew install ffmpeg yt-dlp` · `pip install pypdf pymupdf pillow`
- Linux: `sudo apt install ffmpeg && pip install yt-dlp pypdf pymupdf pillow`

## 1. Download the VIDEO ONLY (never bundle subtitles)
A subtitle error (HTTP 429) aborts the whole download if bundled. Download video alone, subtitles separately.
```bash
cd <WORK> && <YTDLP> -o "video.%(ext)s" -f "bv*[height<=720]+ba/b[height<=720]/b" --write-info-json "<URL>"
```
The merged file is **not guaranteed `.mp4`** (often `.webm`/`.mkv`). Detect it:
```bash
VID=$(ls <WORK>/video.* | grep -vE '\.(json|vtt|srt)$' | head -1)
```
Read title/duration/auto-caption languages from `video.info.json` (UTF-8 — parse the file, the terminal may garble it). **Also read the full `description`** (`d['description']`, not just the first lines) and pull any URLs from it — the description + pinned comment hold the tool/site links the tutorial relies on, which **auto-captions mangle** (brand names come through garbled or missing). Dump it to a UTF-8 file and read it: `pathlib.Path('desc.txt').write_text(d.get('description') or '', encoding='utf-8')`.

## 2. Frames
Density by duration: <2min ~2fps · 2–10min ~1fps · 10–30min 1/30s · >30min ~100 frames total. Max 512px for the reading pass:
```bash
ffmpeg -i "$VID" -vf "fps=1/30,scale=512:-1" -q:v 3 <WORK>/frames/frame_%03d.jpg -y
```
**Re-extract at 1280px any frame you will redraw** (512px slide text is too blurry to read accurately):
```bash
ffmpeg -i "$VID" -vf "fps=1/30,scale=1280:-1" -q:v 2 <WORK>/hi/frame_%03d.jpg -y
```
(`frame_NNN` source time ≈ `(NNN-1)×30s` for the 1/30s rate — handy for re-extracting a specific moment with `-ss`.)

## 3. Transcript (separate, fault-tolerant)
Prefer the **original language** (`*-orig`); check `automatic_captions` keys in the info-json. **Retry on 429** (rate-limit, not fatal — the video already downloaded).
```bash
<YTDLP> --skip-download --write-auto-sub --sub-lang "ko-orig" --convert-subs vtt -o "video.%(ext)s" "<URL>"
```
Parse VTT → clean timestamped text, collapsing duplicated rolling-caption lines; write `transcript_<lang>.txt` as **UTF-8**. If no captions exist at all, fall back to Whisper on the audio.

> **Encoding:** Windows consoles (cp949) garble non-ASCII output. Never judge correctness from the terminal — read the saved file with the Read tool, and always write UTF-8.

## 4. Render HTML → PDF (landscape)
Inline-SVG `분석.html` is self-contained; render it directly. Output to an ABSOLUTE TEMP path, then copy.
```bash
<CHROME> --headless=new --disable-gpu --no-pdf-header-footer \
  --virtual-time-budget=20000 --run-all-compositor-stages-before-draw \
  --print-to-pdf="<WORK>/out.pdf" "<ABS path to 분석.html>"
cp <WORK>/out.pdf "<DEST>/[교육자료] <제목>.pdf"
```
Gotchas baked in above:
- **`--headless=new`** (old/default headless renders only ~1 page from a long doc).
- **Output to temp, not the destination** — writing a PDF straight into a Drive-synced / bracketed-Korean folder throws `액세스 거부 (0x5)`. Chrome can *read* the input HTML from there fine.
- **Absolute output path** — new-headless resolves a relative `--print-to-pdf` path to an unexpected (often protected) dir.

## 5. Verify (never skip)
```bash
python - <<'PY'
from pypdf import PdfReader
import fitz
r = PdfReader(r"<WORK>/out.pdf")
p = r.pages[0]; w, h = float(p.mediabox.width), float(p.mediabox.height)
print("pages:", len(r.pages), "orient:", "LANDSCAPE" if w > h else "PORTRAIT")
d = fitz.open(r"<WORK>/out.pdf")
for i in range(d.page_count):
    d[i].get_pixmap(dpi=90).save(rf"<WORK>/p_{i:02d}.png")
print("rasterized", d.page_count, "pages — now VIEW them")
PY
```
Then **open each `p_NN.png` and look**. Catch: solid-black boxes (an SVG element used an undefined CSS color class), overlapping/clipped labels, off-page figures. Fix the `분석.html` SVG and re-render.

## 6. Real screenshots (demos / live UI / results) — clean crop + inline
For "show the actual screen" figures (screencast/demo/tutorial — see SKILL.md Step 6b), embed the **real captured frame**, not a redrawn version. Re-extract the moment at 1280px, then with Pillow **crop off the subtitle bar / webcam / overlay only** (clean treatment — keep the authentic look; no heavy annotation). Pick the subtitle-free region; if subtitle is white text over content, crop to the part of the frame above it. Then **base64-inline** so the HTML stays self-contained:
```python
import base64, re, pathlib
html = pathlib.Path("분석.html").read_text(encoding="utf-8")
html = re.sub(r'src="(frames/[^"]+)"',
              lambda m: 'src="data:image/jpeg;base64,'+base64.b64encode(pathlib.Path(m.group(1)).read_bytes()).decode()+'"',
              html)
pathlib.Path("분석.html").write_text(html, encoding="utf-8")
```
But prefer SVG reconstruction — it is almost always better than any screenshot.

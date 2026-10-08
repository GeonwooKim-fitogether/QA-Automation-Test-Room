"""클라우드 결과판 배포 폴더를 만든다 — board/index.html 을 그대로 복사하고 cloud-config.js 를 옆에 둔다.

  python tools/build_cloud_board.py
  → deploy/board/index.html + deploy/board/cloud-config.js  (이 폴더를 Vercel 등 정적 호스팅에 올린다)

anon(publishable) 키는 공개용 키라 저장소에 두어도 된다. service_role 키는 절대 여기 넣지 않는다.
"""
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = {
    "url": "https://rmlxafxegabeqqtuvyyb.supabase.co",
    "anon": "sb_publishable_sruZzge00vhwAImxg8sloQ_NTviNa01",
    "bench": "hq-bench-1",
}

out = ROOT / "deploy" / "board"
out.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "board" / "index.html", out / "index.html")
(out / "cloud-config.js").write_text(
    "// 클라우드 결과판 설정 — 이 파일이 있으면 index.html 이 Supabase 에서 읽는다. anon 키는 공개용.\n"
    f"window.CLOUD_CONFIG = {{ url: {CFG['url']!r}, anon: {CFG['anon']!r}, bench: {CFG['bench']!r} }};\n".replace("'", '"'),
    encoding="utf-8")
print(f"배포 폴더: {out}  (index.html {((out/'index.html').stat().st_size/1024):.0f} KB · cloud-config.js)")

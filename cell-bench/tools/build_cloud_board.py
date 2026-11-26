"""클라우드 결과판 배포 폴더를 만든다 — board/index.html 과 board/rail-live.html(레일 시험 > 실시간 화면)을 그대로 복사하고
cloud-config.js 를 옆에 둔다.

  python tools/build_cloud_board.py
  → deploy/board/index.html + deploy/board/rail-live.html + deploy/board/cloud-config.js  (이 폴더를 Vercel 등 정적 호스팅에 올린다)

복사하기 전에 board/rail-live.html 의 CELL_OK 덩어리(셀 신호 칸이 '판정 불가'를 가리는 셀 목록)를 기종 등록부
motionbench/criteria/cell_models.json 에서 다시 쓴다 — 등록부의 cell_models.check 로 읽을 수 있는 셀만 싣는다.
등록부가 바뀌면 이 도구를 다시 돌려야 화면에 반영된다.

anon(publishable) 키는 공개용 키라 저장소에 두어도 된다. service_role 키는 절대 여기 넣지 않는다.
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from motionbench import cell_models  # noqa: E402

CFG = {
    "url": "https://rmlxafxegabeqqtuvyyb.supabase.co",
    "anon": "sb_publishable_sruZzge00vhwAImxg8sloQ_NTviNa01",
    "bench": "hq-bench-1",
}
LIVE = ROOT / "board" / "rail-live.html"
CELL_OK_RE = re.compile(r"/\* CELL_OK:BEGIN \*/.*?/\* CELL_OK:END \*/", re.S)


def cell_ok_block(table: dict | None = None) -> str:
    """등록부에서 읽을 수 있다고 확인된 셀 번호(오름차순)를 rail-live.html 의 한 줄로."""
    t = cell_models.load() if table is None else table
    ok = sorted(int(s) for s in (t.get("cells") or {}) if cell_models.check(int(s), t)[0])
    return "/* CELL_OK:BEGIN */const CELL_OK=new Set('" + " ".join(map(str, ok)) + "'.split(' '));/* CELL_OK:END */"


def write_cell_ok(path: Path = LIVE) -> bool:
    """rail-live.html 의 CELL_OK 덩어리를 등록부 값으로 바꾼다. 바뀌었으면 True."""
    src = path.read_bytes().decode("utf-8")          # 줄 끝(LF · CRLF)을 그대로 둔다
    if not CELL_OK_RE.search(src):
        raise SystemExit(f"{path} 에 CELL_OK 덩어리가 없다")
    new = CELL_OK_RE.sub(lambda _: cell_ok_block(), src, count=1)
    if new != src:
        path.write_bytes(new.encode("utf-8"))
    return new != src


def main() -> None:
    changed = write_cell_ok()
    out = ROOT / "deploy" / "board"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "board" / "index.html", out / "index.html")
    shutil.copy2(LIVE, out / "rail-live.html")
    (out / "cloud-config.js").write_text(
        "// 클라우드 결과판 설정 — 이 파일이 있으면 index.html 이 Supabase 에서 읽는다. anon 키는 공개용.\n"
        f"window.CLOUD_CONFIG = {{ url: {CFG['url']!r}, anon: {CFG['anon']!r}, bench: {CFG['bench']!r} }};\n".replace("'", '"'),
        encoding="utf-8")
    print(f"배포 폴더: {out}  (index.html {((out/'index.html').stat().st_size/1024):.0f} KB · rail-live.html · cloud-config.js)"
          + (" · 셀 목록 갱신" if changed else ""))


if __name__ == "__main__":
    main()

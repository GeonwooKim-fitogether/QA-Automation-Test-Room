"""심박 짝 표 — 보기 · bench.json 에 넣기 · 보드 읽기 (cellbench/hr.py).

  python -X utf8 tools/hr_pair.py                         # 짝 표 · 지금 셀 심박 · 보드 상태를 보여 준다 (아무것도 보내지 않음)
  python -X utf8 tools/hr_pair.py --boards                # 짝 표의 보드를 지금 USB 로 한 번 읽어 보여 준다 (읽기만)
  python -X utf8 tools/hr_pair.py --import-csv 짝.csv     # 짝 CSV(no,hub,port,name[,serial]) → 세트 1 의 hr_pairs 로 바꿔 보여 준다
  python -X utf8 tools/hr_pair.py --import-csv 짝.csv --write   # 위 내용을 bench.json 에 넣는다 (검사 → 임시 파일 → 바꿔치기)
  python -X utf8 tools/hr_pair.py --go                    # 짝 표대로 셀마다 이름(0x20)을 보내고 0x26 으로 측정에 돌려보낸다
  python -X utf8 tools/hr_pair.py --go --serials 11740,11742   # 그 셀만

--go 가 지키는 것 (엔진의 자동 재연결 cycle._watch_hr 과 같은 순서 — 2026-10-15 .claude/state/pair_cells.py 로 24/24 실측)
  · 추출(EXTRACT) 중이면 하지 않는다 — 엔진이 TCP 60222 를 쓰고 있다.
  · TCP 60222 를 독점으로 잡는다. 엔진이 쓰는 중(추출 · 대기 셀 복귀 · 자동 재연결)이면 잡히지 않으므로 아무것도 하지 않고 끝난다.
  · 셀 주소는 엔진의 now.json 에서 읽는다(라이브 포트는 엔진이 쥐고 있다). 깨운 셀이 아닌 주소의 접속은 0x26 으로 돌려보낸다.
  · 셀마다 결과를 data/hr_pair_log.csv 에 남긴다. 셀이 재부팅하므로 엔진에 live_gap 이 셀마다 1건 남는 것이 정상이다.

짝 CSV 의 hub 칸을 P 로 시작하게 적으면("P3") 그 보드는 전원만 — USB 데이터 연결이 없어 --boards · 감시자가 읽지 않고, 판정은 셀 심박만 본다.
짝 CSV 에 serial 칸이 없으면 보드 번호 N ↔ 세트의 첫 셀 + N − 1 (세트 1: 보드 1 ↔ 셀 11733). --set 으로 세트를 고른다.
bench.json 은 운전 중에 등록 화면이 고치는 파일이라 손으로 고치지 말고 --write 로 넣는다(config.set_hr_pairs — 다른 키는 그대로 둔다).
엔진 · 감시자 · 결과판은 bench.json 을 다시 읽어야 짝 표를 안다 — 감시자와 결과판은 다음 점검·요청에서, 엔진은 다음에 시작할 때.
"""
from __future__ import annotations

import argparse
import csv
import json
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cellbench import hr  # noqa: E402
from cellbench import protocol as P  # noqa: E402
from cellbench.config import BENCH_FILE, Config, expand_serials, set_entry, set_hr_pairs  # noqa: E402
from cellbench.control import read_json  # noqa: E402


def say(*a) -> None:
    print(*a, flush=True)


def pairs_from_csv(path: Path, first_serial: int) -> dict:
    """짝 CSV → hr_pairs. 칸: no · hub · port · name (· serial — 없으면 first_serial + no − 1)."""
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            no = int(r["no"])
            serial = int(r["serial"]) if (r.get("serial") or "").strip() else first_serial + no - 1
            port = r.get("port", "").strip()
            out[str(serial)] = {"board": r["name"].strip().upper(), "no": no, "hub": r.get("hub", "").strip(),
                                "port": int(port) if port.isdigit() else port}
    return out


def show(cfg: Config, data: Path, sid: int) -> None:
    pairs = hr.pairs_of(set_entry(cfg, sid))
    if not pairs:
        say(f"세트 {sid} 에는 심박 짝 표(hr_pairs)가 없다 — --import-csv 로 넣는다")
        return
    now = read_json(data / ("now.json" if sid == 1 else f"set{sid}/now.json")) or {}
    cells = {c.get("serial"): c for c in now.get("cells") or [] if isinstance(c, dict)}
    boards = read_json(data / hr.BOARDS_FILE)
    th = hr.thresholds(cfg)
    say(f"\n세트 {sid} · 단계 {now.get('phase', '-')} · 보드 기록 {time.strftime('%H:%M:%S', time.localtime(boards['t'])) if boards else '없음'}\n")
    say(" 번호  허브   보드          셀      심박  보드 상태")
    for s, p in sorted(pairs.items(), key=lambda x: (x[1].get("no") or 0, x[0])):
        st, b = (("전원만 · 안 읽음", None) if hr.is_power(p["hub"])      # P 허브 — USB 데이터 연결이 없어 보드를 읽지 않는다
                 else hr.board_state(p["board"], boards, time.time(), th["hr_board_stale_s"]))
        c = cells.get(s, {})
        conns = f" · 연결 {b.get('conns')}" if b and st in ("free", "joined") else ""
        say(f" {str(p.get('no') or '-'):>3}  {p['hub'] or '-'}-{str(p.get('port') or '-'):<3} {p['board']:<12}  {s}  "
            f"{str(c.get('hr', '-')):>4}  {st}{conns}")


def _send_back(conn) -> None:
    try:
        conn.settimeout(6); conn.recv(64); conn.sendall(P.frame(P.MSG_RESET_NORMAL))
    except OSError:
        pass
    finally:
        conn.close()


def pair_one(cfg: Config, srv, ip: str, name: str, say_=say) -> tuple[str, str]:
    """셀 하나 — 깨우기 → 접속(그 주소만) → 첫 인사 → 0x20 → 응답 → finally 0x26. (결과 ok · no-ack · no-connect, 응답 16진)."""
    snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); snd.bind((cfg.pc_ip, 0))
    snd.sendto(P.wake_frame(), (ip, cfg.wake_port)); snd.close()
    t0 = time.time()
    while True:
        srv.settimeout(max(1.0, 25 - (time.time() - t0)))
        try:
            conn, addr = srv.accept()
        except socket.timeout:
            return "no-connect", "25초 안에 접속 없음"
        if addr[0] == ip:
            break
        say_(f"     다른 주소 {addr[0]} 의 접속 — 0x26 으로 돌려보냄")
        _send_back(conn)
        if time.time() - t0 > 25:
            return "no-connect", "25초 안에 접속 없음"
    ack = b""
    try:
        conn.settimeout(6); conn.recv(256)                       # 첫 인사
        conn.sendall(P.ble_id_frame(name))
        conn.settimeout(3)
        try:
            ack = conn.recv(256)
        except socket.timeout:
            pass
    finally:
        try:
            conn.sendall(P.frame(P.MSG_RESET_NORMAL)); conn.settimeout(3); conn.recv(64)
        except OSError:
            pass
        conn.close()
    return ("ok" if P.ble_id_ok(ack) else "no-ack"), (ack[:8].hex(" ") or "응답 없음")


def go(cfg: Config, data: Path, sid: int, only: set[int] | None) -> int:
    pairs = hr.pairs_of(set_entry(cfg, sid))
    if not pairs:
        sys.exit(f"세트 {sid} 에는 심박 짝 표(hr_pairs)가 없다")
    now = read_json(data / ("now.json" if sid == 1 else f"set{sid}/now.json")) or {}
    if now.get("phase") == "EXTRACT":
        sys.exit("추출 중이라 하지 않는다 (엔진이 TCP 60222 를 쓰는 중)")
    ips = {c.get("serial"): c.get("ip") for c in now.get("cells") or [] if isinstance(c, dict)}
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        srv.bind((cfg.pc_ip, cfg.port))
    except OSError as e:
        sys.exit(f"TCP {cfg.port} 를 엔진이 쓰는 중으로 보여 하지 않는다 ({e})")
    srv.listen(4)
    log = data / "hr_pair_log.csv"
    ok = n = 0
    try:
        for s, p in sorted(pairs.items(), key=lambda x: (x[1].get("no") or 0, x[0])):
            if only and s not in only:
                continue
            n += 1
            ip = ips.get(s)
            res, detail = pair_one(cfg, srv, ip, p["board"]) if ip else ("skip", "주소 없음 (now.json 에 없다)")
            ok += res == "ok"
            new = not log.exists()
            with log.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["time", "set", "serial", "ip", "board", "result", "detail"])
                w.writerow([datetime.now().isoformat(timespec="seconds"), sid, s, ip, p["board"], res, detail])
            say(f" {s} ← {p['board']}  {res} {detail}")
            time.sleep(1)
    finally:
        srv.close()
    say("")
    say(f"끝: {ok}/{n} 성공 · 기록 {log}")
    return 0 if ok == n else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="심박 짝 표 — 보기 · bench.json 에 넣기 · 보드 읽기")
    ap.add_argument("--set", type=int, default=1)
    ap.add_argument("--bench", default=str(BENCH_FILE))
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--import-csv", default=None, help="짝 CSV (no,hub,port,name[,serial])")
    ap.add_argument("--first-serial", type=int, default=None, help="serial 칸이 없을 때 보드 1 의 셀 (기본: 세트의 첫 셀)")
    ap.add_argument("--write", action="store_true", help="--import-csv 의 결과를 bench.json 에 넣는다")
    ap.add_argument("--boards", action="store_true", help="짝 표의 보드를 지금 USB 로 읽는다 (읽기만)")
    ap.add_argument("--go", action="store_true", help="짝 표대로 셀에 이름(0x20)을 보낸다")
    ap.add_argument("--serials", default="", help="--go 를 이 셀들에만 (쉼표)")
    a = ap.parse_args()
    cfg = Config.load(None, bench_path=a.bench)
    if a.go:
        only = {int(x) for x in a.serials.split(",") if x.strip()} or None
        return go(cfg, Path(a.data), a.set, only)
    if a.import_csv:
        entry = set_entry(cfg, a.set)
        if entry is None:
            sys.exit(f"bench.json 에 세트 {a.set} 가 없다")
        first = a.first_serial or min(expand_serials(entry.get("serials", [])))
        pairs = pairs_from_csv(Path(a.import_csv), first)
        say(json.dumps({"hr_pairs": pairs}, ensure_ascii=False, indent=1))
        if not a.write:
            say(f"\n(보기만 함 — 세트 {a.set} 에 넣으려면 --write)")
            return 0
        set_hr_pairs(a.set, pairs, a.bench)
        say(f"\n{a.bench} 세트 {a.set} 에 짝 {len(pairs)}개를 넣었다")
        return 0
    if a.boards:
        names = hr.watched_names(cfg.sets)
        if not names:
            sys.exit("USB 로 읽을 보드가 없다 — 짝 표가 없거나 모두 전원만(허브 P…) 보드다")
        doc = hr.scan(names, None, time.time(), hr.thresholds(cfg)["hr_board_read_s"])
        say(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    show(cfg, Path(a.data), a.set)
    return 0


if __name__ == "__main__":
    sys.exit(main())

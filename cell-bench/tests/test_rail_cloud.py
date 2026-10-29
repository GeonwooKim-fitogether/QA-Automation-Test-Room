"""레일 판정 파일 클라우드 올리기 — cellbench/rail_cloud.py 와 serve_board 의 /api/rail/cloud · --rail-sync.

클라우드(Supabase PostgREST)는 이 검사 안의 가짜 서버가 대신한다. 보는 것:
파일을 키 순서까지 그대로 올리는가, 목록 한 줄은 결과판 서버와 같은 요약인가, 이미 올린 것은 다시 보내지 않고 다시 쓴 것은 다시
보내는가, 쓰는 중인 파일 · NaN 파일 · 클라우드 실패를 어떻게 다루는가, 판정 파일이 없는 PC 는 클라우드에 묻지도 않는가."""
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serve_board as sb
from cellbench.control import PinGuard
from cellbench.rail_cloud import RailCloud

FIX = Path(__file__).resolve().parent / "fixtures" / "rail"
V1 = "protocol_20261029-155408.json"         # 실제 v1 시험
SIM = "protocol_20261029-172000.json"        # v4 표본
KEY = "test-service-key"


class FakeRest:
    """PostgREST 의 rail_run 표 하나만 흉내 낸다 — GET 은 name · file_mtime 목록, POST 는 upsert."""

    def __init__(self):
        self.rows, self.calls, self.fail_post = {}, [], None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, obj=None):
                body = b"" if obj is None else json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                fake.calls.append(("GET", self.path, {k.lower(): v for k, v in self.headers.items()}, None))
                self._reply(200, [{"name": n, "file_mtime": r["file_mtime"]} for n, r in fake.rows.items()])

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                fake.calls.append(("POST", self.path, {k.lower(): v for k, v in self.headers.items()}, raw))
                if fake.fail_post:
                    return self._reply(fake.fail_post, {"message": "boom"})
                row = json.loads(raw)
                fake.rows[row["name"]] = row
                self._reply(201)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def posts(self):
        return [json.loads(c[3]) for c in self.calls if c[0] == "POST"]


def rc(data, url, clock=None, log=None):
    return RailCloud(data, sb.rail_summary, sb.rail_name_ok, url=url, key=KEY, source="control-pc2",
                     log=log or (lambda m: None), clock=clock or (lambda: time.time() + 10))


def put(data: Path, *names):
    for n in names:
        shutil.copy(FIX / n, data / n)


def test_no_key_is_off_and_sends_nothing(tmp_path):
    put(tmp_path, V1)
    fake = FakeRest()
    try:
        r = RailCloud(tmp_path, sb.rail_summary, sb.rail_name_ok, url=fake.url, key="", log=lambda m: None)
        assert r.enabled is False and r.sync() == 0 and r.start() is None
        assert r.status()["state"] == "off" and fake.calls == []
    finally:
        fake.srv.shutdown()


def test_pc_without_protocol_files_never_asks_the_cloud(tmp_path):
    """TestPC — 키는 있지만 판정 파일이 없다. 1분마다 클라우드에 묻지 않는다."""
    (tmp_path / "now.json").write_text("{}", encoding="utf-8")
    fake = FakeRest()
    try:
        r = rc(tmp_path, fake.url)
        assert r.sync() == 0 and fake.calls == []
        assert r.status() == {**r.status(), "state": "ok", "local": 0, "uploaded": 0}
    finally:
        fake.srv.shutdown()


def test_new_files_go_up_whole_with_the_board_summary(tmp_path):
    put(tmp_path, V1, SIM)
    (tmp_path / "protocol_x.json").write_text("{}", encoding="utf-8")      # 이름 규칙 밖 — 올리지 않는다
    fake = FakeRest()
    try:
        r = rc(tmp_path, fake.url)
        assert r.sync() == 2
        assert [c[0] for c in fake.calls] == ["GET", "POST", "POST"]
        get, post = fake.calls[0], fake.calls[1]
        assert get[1] == "/rest/v1/rail_run?select=name,file_mtime"
        assert post[1] == "/rest/v1/rail_run?on_conflict=name"
        assert post[2]["apikey"] == KEY and post[2]["authorization"] == f"Bearer {KEY}"
        assert post[2]["prefer"] == "resolution=merge-duplicates,return=minimal"
        for row in fake.posts():
            src = json.loads((FIX / row["name"]).read_text(encoding="utf-8"))
            assert row["doc"] == src
            assert list(row["doc"]) == list(src)                                         # 키 순서까지 그대로
            assert list(row["doc"]["cells"]) == list(src["cells"])                       # 셀 순서 — 결과판이 이 순서로 그린다
            assert row["summary"] == json.loads(json.dumps(sb.rail_summary(row["name"], src), ensure_ascii=False))
            assert row["source"] == "control-pc2"
            assert abs(sb.rail_summary(row["name"], src)["t"] - _ts(row["t"])) < 1e-3
            assert abs(os.path.getmtime(tmp_path / row["name"]) - _ts(row["file_mtime"])) < 1e-3
        assert {p["name"] for p in fake.posts()} == {V1, SIM}
        st = r.status()
        assert st["state"] == "ok" and st["local"] == 2 and st["uploaded"] == 2 and st["bad"] == {}
    finally:
        fake.srv.shutdown()


def test_already_uploaded_is_not_sent_again_but_a_rewrite_is(tmp_path):
    put(tmp_path, V1)
    fake = FakeRest()
    try:
        assert rc(tmp_path, fake.url).sync() == 1
        fake.calls.clear()
        r = rc(tmp_path, fake.url)                                   # 결과판을 다시 띄웠다 — 클라우드 목록으로 안다
        assert r.sync() == 0 and [c[0] for c in fake.calls] == ["GET"]
        assert r.status()["uploaded"] == 1
        assert r.sync() == 0 and len(fake.calls) == 1                # 할 일이 없으면 다시 묻지도 않는다
        t = os.path.getmtime(tmp_path / V1) - 60                     # 판정을 다시 썼다 (mtime 이 바뀌었다)
        os.utime(tmp_path / V1, (t, t))
        assert r.status()["uploaded"] == 0
        assert r.sync() == 1 and [c[0] for c in fake.calls] == ["GET", "POST"]
    finally:
        fake.srv.shutdown()


def test_file_still_being_written_waits_for_the_next_round(tmp_path):
    put(tmp_path, V1)
    m = os.path.getmtime(tmp_path / V1)
    now = [m + 0.5]
    fake = FakeRest()
    try:
        r = rc(tmp_path, fake.url, clock=lambda: now[0])
        assert r.sync() == 0 and fake.calls == []
        now[0] = m + 3
        assert r.sync() == 1
    finally:
        fake.srv.shutdown()


def test_nan_file_is_not_sent_and_is_named_until_rewritten(tmp_path):
    put(tmp_path, V1)
    bad = tmp_path / SIM
    bad.write_text((FIX / SIM).read_text(encoding="utf-8").replace('"value": null', '"value": NaN', 1), encoding="utf-8")
    assert "NaN" in bad.read_text(encoding="utf-8")
    fake = FakeRest()
    try:
        r = rc(tmp_path, fake.url)
        assert r.sync() == 1 and [p["name"] for p in fake.posts()] == [V1]
        st = r.status()
        assert st["state"] == "ok" and st["uploaded"] == 1 and st["bad"] == {SIM: "파일 형식 오류 — NaN"}
        assert r.sync() == 0                                         # 같은 파일은 다시 해 보지 않는다
        shutil.copy(FIX / SIM, bad)                                  # 쓰는 프로그램이 고쳐 다시 썼다
        t = time.time() - 5
        os.utime(bad, (t, t))
        assert r.sync() == 1 and r.status()["bad"] == {}
    finally:
        fake.srv.shutdown()


def test_cloud_failure_is_kept_logged_once_and_retried(tmp_path):
    put(tmp_path, V1)
    logs = []
    fake = FakeRest()
    fake.fail_post = 500
    try:
        r = rc(tmp_path, fake.url, log=logs.append)
        assert r.sync() == 0 and r.sync() == 0
        st = r.status()
        assert st["state"] == "fail" and st["error"] == "HTTP 500" and st["uploaded"] == 0
        assert len([m for m in logs if "실패" in m]) == 1           # 같은 실패를 1분마다 적지 않는다
        fake.fail_post = None
        assert r.sync() == 1
        st = r.status()
        assert st["state"] == "ok" and st["error"] is None and st["uploaded"] == 1
    finally:
        fake.srv.shutdown()


def test_unreachable_cloud_is_a_failure_not_a_crash(tmp_path):
    put(tmp_path, V1)
    r = rc(tmp_path, "http://127.0.0.1:9")                          # 아무도 듣지 않는 곳
    assert r.sync() == 0 and r.status()["state"] == "fail"


def test_board_endpoint_reports_the_upload_state(tmp_path):
    put(tmp_path, V1)
    fake = FakeRest()
    r = rc(tmp_path, fake.url)
    r.sync()
    for cloud, want in ((r, {"state": "ok", "local": 1, "uploaded": 1}), (None, {"state": "off"})):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), sb.make_handler(tmp_path, PinGuard(None), tmp_path / "bench.json",
                                                                     rail_cloud=cloud))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/rail/cloud", timeout=5) as resp:
                got = json.loads(resp.read())
            assert {k: got[k] for k in want} == want
        finally:
            srv.shutdown()
    fake.srv.shutdown()


def test_rail_sync_flag_uploads_once_without_starting_the_board(tmp_path, monkeypatch, capsys):
    seen = []

    class Fake:
        def __init__(self, data, summarize, name_ok, log=print):
            seen.append(("new", Path(data), summarize is sb.rail_summary, name_ok is sb.rail_name_ok))

        def sync(self):
            seen.append("sync")
            return 0

        def status(self):
            return {"state": "off"}

        def start(self):
            raise AssertionError("--rail-sync 는 결과판을 띄우지 않는다")

    monkeypatch.setattr(sb, "RailCloud", Fake)
    monkeypatch.setattr(sb, "ThreadingHTTPServer", lambda *a, **k: (_ for _ in ()).throw(AssertionError("서버를 띄웠다")))
    monkeypatch.setattr(sys, "argv", ["serve_board.py", "--data", str(tmp_path), "--rail-sync"])
    sb.main()
    assert seen == [("new", tmp_path, True, True), "sync"]
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"state": "off"}


def _ts(s):
    from datetime import datetime
    return datetime.fromisoformat(s).timestamp()

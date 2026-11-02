"""레일 시험 QC 판정(세트 단위) 저장 통로 — serve_board 의 GET /api/rail/dispositions · POST /api/rail/disposition.
판정 파일(protocol_*.json)과 따로 data/rail_disposition.json 에 {셀 구성: 판정} 으로 쌓이는지, 칸을 거르는지, 깨진 파일을 덮어쓰지
않는지 본다. 화면 쪽 흐름(세트 펼침 → 저장)은 test_rail_screens 가 브라우저로 본다."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import serve_board as sb
from cellbench.control import PinGuard

SER = [str(10677 + i) for i in range(24)]
KEY = ",".join(sorted(SER))


def rec(**kw):
    r = {"set": "세트 1", "verdict": "pass", "actions": {"10679": {"act": "retest", "note": ""}}, "comment": "",
         "by": "QC 담당자", "at": 1793600000000, "criteria_version": None, "tentative": True,
         "runs": ["protocol_20261030-164830.json"], "history": []}
    r.update(kw)
    return r


def test_key_is_the_sorted_serial_list():
    assert sb.rail_disp_key_ok(KEY)
    assert not sb.rail_disp_key_ok(",".join(reversed(sorted(SER))))          # 정렬 안 됨
    assert not sb.rail_disp_key_ok("10677,10677")                            # 중복
    assert not sb.rail_disp_key_ok("세트 1") and not sb.rail_disp_key_ok("") and not sb.rail_disp_key_ok("../x")


def test_clean_keeps_the_verdict_and_drops_unknown_fields():
    out, err = sb.rail_disp_clean(KEY, rec(extra="x", runs=["protocol_20261030-164830.json", "../etc"]), who="127.0.0.1")
    assert err is None
    assert out["verdict"] == "pass" and out["actions"] == {"10679": {"act": "retest", "note": ""}} and out["set"] == "세트 1"
    assert "extra" not in out and out["runs"] == ["protocol_20261030-164830.json"]   # 판정 파일 이름 모양만
    assert out["by_addr"] == "127.0.0.1" and out["saved_at"] > 0 and out["tentative"] is True


@pytest.mark.parametrize("bad,msg", [
    (rec(verdict="ok"), "verdict"),
    (rec(verdict="fail", comment="  "), "의견"),
    (rec(actions={"99999": {"act": "retest"}}), "없는 셀"),
    (rec(actions={"10679": {"act": "delete"}}), "retest"),
    (rec(actions={"10679": {"act": "hold", "note": ""}}), "사유"),
])
def test_clean_refuses_a_verdict_that_loses_its_meaning(bad, msg):
    out, err = sb.rail_disp_clean(KEY, bad)
    assert out is None and msg in err


def test_history_is_cleaned_and_capped():
    old = rec(verdict="fail", comment="첫 판정", history=[])
    out, err = sb.rail_disp_clean(KEY, rec(history=[old, {"verdict": "??"}] + [old] * 80))
    assert err is None and len(out["history"]) == sb.RAIL_DISP_HISTORY - 1 and out["history"][0]["comment"] == "첫 판정"


@pytest.fixture()
def server(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), sb.make_handler(tmp_path, PinGuard(None), tmp_path / "bench.json"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()


def call(url, body=None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_post_then_get_keeps_each_set_apart(server):
    base, data = server
    assert call(base + "/api/rail/dispositions") == (200, {})                # 파일이 없으면 빈 판정
    other = ",".join(sorted(str(20000 + i) for i in range(3)))
    assert call(base + "/api/rail/disposition", {"name": KEY, "rec": rec()})[0] == 200
    assert call(base + "/api/rail/disposition", {"name": other, "rec": rec(actions={})})[0] == 200
    st, m = call(base + "/api/rail/dispositions")
    assert st == 200 and set(m) == {KEY, other} and m[KEY]["actions"]["10679"]["act"] == "retest"
    assert json.loads((data / sb.RAIL_DISP_FILE).read_text(encoding="utf-8")) == m   # 판정 파일과 따로 한 파일에


def test_post_refuses_bad_input_and_never_overwrites_a_broken_file(server):
    base, data = server
    st, j = call(base + "/api/rail/disposition", {"name": "세트 1", "rec": rec()})
    assert st == 400 and j["ok"] is False
    st, j = call(base + "/api/rail/disposition?set=9", {"name": KEY, "rec": rec()})
    assert st == 400
    (data / sb.RAIL_DISP_FILE).write_text("{깨진", encoding="utf-8")
    st, j = call(base + "/api/rail/disposition", {"name": KEY, "rec": rec()})
    assert st == 500 and "읽지 못해" in j["error"]
    assert (data / sb.RAIL_DISP_FILE).read_text(encoding="utf-8") == "{깨진"     # 다른 세트의 판정을 덮어쓰지 않는다

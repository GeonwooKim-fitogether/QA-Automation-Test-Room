"""클라우드 전송 설정 — Supabase 주소 · service_role 키 · 시험대 ID 를 Windows 자격 증명 관리자에 저장한다.

  python tools/cloud_setup.py           # 입력 창
  python tools/cloud_setup.py --show    # 저장 여부만 (값은 안 보여줌)
  python tools/cloud_setup.py --test    # 저장된 값으로 bench 표를 한 번 읽어 연결 확인

service_role 키는 Supabase 대시보드 → Project Settings → API → service_role (secret) 에 있다. 이 키는 RLS 를 넘으므로
TestPC 에만 넣고 결과판·저장소·채팅에는 절대 넣지 않는다.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keyring
from cellbench.cloud import SERVICE, Cloud

DEFAULT_URL = "https://rmlxafxegabeqqtuvyyb.supabase.co"
DEFAULT_BENCH = "hq-bench-1"

if "--show" in sys.argv:
    for k in ("url", "service_key", "bench_id"):
        v = keyring.get_password(SERVICE, k)
        print(f"{k:>12}: {'있음' if v else '없음'}" + (f" ({v})" if v and k != 'service_key' else ""))
    sys.exit(0)

if "--test" in sys.argv:
    c = Cloud(log=print)
    if not c.enabled:
        sys.exit(1)
    rows = c._req("GET", "bench", None, {"id": f"eq.{c.bench_id}"})
    print("연결 확인:", rows[0]["name"] if rows else "시험대 행이 없음 — bench 표에 등록 필요")
    sys.exit(0)

import tkinter as tk
root = tk.Tk(); root.title("셀 시험대 - 클라우드 전송 설정"); root.attributes("-topmost", True); root.resizable(False, False)
tk.Label(root, justify="left", text="Supabase 프로젝트 cell-bench 에 올리기 위한 값입니다. 이 PC 의 Windows 자격 증명 관리자에만 저장됩니다.").grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")
tk.Label(root, text="Supabase URL").grid(row=1, column=0, sticky="e", padx=8); e_url = tk.Entry(root, width=56); e_url.insert(0, keyring.get_password(SERVICE, "url") or DEFAULT_URL); e_url.grid(row=1, column=1, padx=12, pady=3)
tk.Label(root, text="service_role 키").grid(row=2, column=0, sticky="e", padx=8); e_key = tk.Entry(root, width=56, show="*"); e_key.grid(row=2, column=1, padx=12, pady=3)
tk.Label(root, text="시험대 ID").grid(row=3, column=0, sticky="e", padx=8); e_id = tk.Entry(root, width=56); e_id.insert(0, keyring.get_password(SERVICE, "bench_id") or DEFAULT_BENCH); e_id.grid(row=3, column=1, padx=12, pady=3)
msg = tk.Label(root, text="", fg="#B00020"); msg.grid(row=5, column=0, columnspan=2, pady=(0, 8))
done = {"ok": False}

def save(_=None):
    url, key, bid = e_url.get().strip().rstrip("/"), e_key.get().strip(), e_id.get().strip()
    if not url.startswith("https://"): msg.config(text="URL 은 https:// 로 시작"); return
    if len(key) < 40: msg.config(text="service_role 키가 너무 짧습니다"); return
    if not bid: msg.config(text="시험대 ID 를 입력"); return
    keyring.set_password(SERVICE, "url", url); keyring.set_password(SERVICE, "service_key", key); keyring.set_password(SERVICE, "bench_id", bid)
    done["ok"] = True; root.destroy()

tk.Button(root, text="저장", width=10, command=save).grid(row=4, column=1, sticky="e", padx=12, pady=8)
root.bind("<Return>", save); e_key.focus_set(); root.mainloop()
print("저장됨 — python tools/cloud_setup.py --test 로 연결을 확인한 뒤 run_cycle.py 를 다시 시작하면 전송이 켜진다" if done["ok"] else "취소됨")

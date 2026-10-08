"""원격 제어 PIN 과 Slack 알림 웹훅을 Windows 자격 증명 관리자에 저장한다. 값은 어디에도 찍지 않는다.

  python tools/remote_setup.py            # 입력 창
  python tools/remote_setup.py --show     # 무엇이 저장돼 있는지 (값은 아니고 있음/없음만)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import keyring
from cellbench.alert import SERVICE

if "--show" in sys.argv:
    for k in ("pin", "slack_webhook"):
        print(f"{k:>14}: {'있음' if keyring.get_password(SERVICE, k) else '없음'}")
    sys.exit(0)

import tkinter as tk
root = tk.Tk(); root.title("셀 시험대 - 원격 제어 설정"); root.attributes("-topmost", True); root.resizable(False, False)
tk.Label(root, justify="left", text=(
    "원격 제어 PIN (숫자 4~8자리) — 결과판에서 플러그를 켜고 끄거나 멈출 때 묻습니다.\n"
    "Slack 웹훅 주소 (선택) — 이상이 나면 이 채널로 메시지를 보냅니다. 비우면 알림 없음.\n"
    "둘 다 이 PC 의 Windows 자격 증명 관리자에만 저장됩니다.")).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")
tk.Label(root, text="PIN").grid(row=1, column=0, sticky="e", padx=8)
pin = tk.Entry(root, width=48, show="*"); pin.grid(row=1, column=1, padx=12, pady=3)
tk.Label(root, text="PIN 확인").grid(row=2, column=0, sticky="e", padx=8)
pin2 = tk.Entry(root, width=48, show="*"); pin2.grid(row=2, column=1, padx=12, pady=3)
tk.Label(root, text="Slack 웹훅").grid(row=3, column=0, sticky="e", padx=8)
hook = tk.Entry(root, width=48); hook.grid(row=3, column=1, padx=12, pady=3)
msg = tk.Label(root, text="", fg="#B00020"); msg.grid(row=5, column=0, columnspan=2, pady=(0, 8))
done = {"ok": False}

def save(_=None):
    p = pin.get().strip()
    if not (p.isdigit() and 4 <= len(p) <= 8):
        msg.config(text="PIN 은 숫자 4~8자리"); return
    if p != pin2.get().strip():
        msg.config(text="PIN 확인이 다릅니다"); return
    h = hook.get().strip()
    if h and not h.startswith("https://hooks.slack.com/"):
        msg.config(text="Slack 웹훅은 https://hooks.slack.com/ 로 시작해야 합니다"); return
    keyring.set_password(SERVICE, "pin", p)
    if h:
        keyring.set_password(SERVICE, "slack_webhook", h)
    done["ok"] = True; root.destroy()

tk.Button(root, text="저장", width=10, command=save).grid(row=4, column=1, sticky="e", padx=12, pady=8)
root.bind("<Return>", save); pin.focus_set(); root.mainloop()
print("저장됨 — serve_board.py 를 다시 시작하면 원격 제어가 켜진다" if done["ok"] else "취소됨")

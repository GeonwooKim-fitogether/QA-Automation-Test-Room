"""스마트 플러그 수동 조작.

  python tools/plug_cli.py status
  python tools/plug_cli.py on | off
  python tools/plug_cli.py setup      # TP-Link 계정을 입력 창으로 받아 자격 증명 관리자에 저장 (값은 출력하지 않음)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cellbench.config import Config

cfg = Config()
cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

if cmd == "setup":
    import tkinter as tk, keyring
    root = tk.Tk(); root.title("셀 시험대 - Tapo 계정 저장"); root.attributes("-topmost", True)
    tk.Label(root, text="TP-Link(Tapo) 계정. 이 PC 의 Windows 자격 증명 관리자에만 저장됩니다.").grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 6))
    tk.Label(root, text="이메일").grid(row=1, column=0, sticky="e", padx=8); e = tk.Entry(root, width=32); e.grid(row=1, column=1, padx=12, pady=3)
    tk.Label(root, text="비밀번호").grid(row=2, column=0, sticky="e", padx=8); p = tk.Entry(root, width=32, show="*"); p.grid(row=2, column=1, padx=12, pady=3)
    saved = {"ok": False}
    def save(_=None):
        if e.get().strip() and p.get():
            keyring.set_password(cfg.keyring_service, "username", e.get().strip())
            keyring.set_password(cfg.keyring_service, "password", p.get()); saved["ok"] = True; root.destroy()
    tk.Button(root, text="저장", command=save).grid(row=3, column=1, sticky="e", padx=12, pady=8); root.bind("<Return>", save)
    e.focus_set(); root.mainloop()
    print("저장됨" if saved["ok"] else "취소됨"); sys.exit(0)

from cellbench.plug import Plug
plug = Plug(cfg)
r = {"status": plug.read, "on": plug.on, "off": plug.off}[cmd]()
print(f"플러그 {plug.ip} · {'켜짐' if r.on else '꺼짐'} · {r.watts:.1f} W")

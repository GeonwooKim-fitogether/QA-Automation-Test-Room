"""입회 등록 창 — 명령 창 없이 레일 옆 운전자가 직접 입회 표지를 만든다(2026-11-18 사용자 요청: 'PowerShell 없이 등록').

바탕화면의 '레일 입회 등록' 아이콘을 두 번 누르면 이 창이 뜬다(pythonw 로 열어 검은 창이 없다).
확인 세 가지를 모두 체크하고 이름 · 시간을 넣은 뒤 '현장 확인' 을 누르면 attend.py 와 같은 표지(cell-bench/data/motion_attended)가 생긴다.
확인 문구 · 시간 범위 · 표지 형식은 attend.py 가 정본이다. 원격(채팅)으로는 만들지 않는다 — 레일 옆에 사람이 있는 것이 이 표지의 뜻이다.
"""
from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))
import attend as A  # noqa: E402

DEFAULT_BY = "김건우"
DEFAULT_MIN = 90


def current() -> str:
    """지금 표지 상태 한 줄."""
    try:
        m = json.loads(A.MARKER.read_text(encoding="utf-8"))
        left = m["until"] - time.time()
        if left > 0:
            return f"지금 입회: {m.get('by', '?')} · {time.strftime('%H:%M', time.localtime(m['until']))} 까지 ({left / 60:.0f}분 남음)"
        return f"지난 입회는 {time.strftime('%m-%d %H:%M', time.localtime(m['until']))} 에 끝났다"
    except (OSError, ValueError, KeyError):
        return "입회 표지 없음"


def main() -> int:
    root = tk.Tk()
    root.title("레일 입회 등록")
    root.resizable(False, False)
    font = ("맑은 고딕", 12)
    pad = {"padx": 14, "pady": 4, "sticky": "w"}

    tk.Label(root, text="모터 시험 전 확인 — 모두 맞을 때만 체크한다", font=("맑은 고딕", 13, "bold")).grid(row=0, column=0, columnspan=2, **pad)
    vars_ = []
    for i, c in enumerate(A.CHECKS, 1):
        v = tk.BooleanVar(value=False)
        tk.Checkbutton(root, text=c, variable=v, font=font).grid(row=i, column=0, columnspan=2, **pad)
        vars_.append(v)

    r = len(A.CHECKS) + 1
    tk.Label(root, text="운전자", font=font).grid(row=r, column=0, **pad)
    by = tk.Entry(root, font=font, width=12)
    by.insert(0, DEFAULT_BY)
    by.grid(row=r, column=1, **pad)
    tk.Label(root, text="시간(분, 10~600)", font=font).grid(row=r + 1, column=0, **pad)
    mins = tk.Spinbox(root, from_=10, to=600, increment=10, font=font, width=6)
    mins.delete(0, "end")
    mins.insert(0, str(DEFAULT_MIN))
    mins.grid(row=r + 1, column=1, **pad)

    status = tk.Label(root, text=current(), font=("맑은 고딕", 11), fg="#555")
    status.grid(row=r + 3, column=0, columnspan=2, **pad)

    def go():
        if not all(v.get() for v in vars_):
            messagebox.showwarning("레일 입회 등록", "확인 세 가지를 모두 체크해야 등록된다")
            return
        try:
            until = A.make_marker(by.get(), int(mins.get()))
        except ValueError as e:
            messagebox.showerror("레일 입회 등록", str(e))
            return
        status.config(text=current())
        messagebox.showinfo("레일 입회 등록", f"등록했다 — {time.strftime('%H:%M', time.localtime(until))} 까지 모터 시험을 할 수 있다")
        root.destroy()

    tk.Button(root, text="현장 확인", font=("맑은 고딕", 13, "bold"), width=14, command=go).grid(row=r + 2, column=0, columnspan=2, padx=14, pady=10)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""레일 멈춤 창 — 레일 옆 운전자가 버튼 하나로 레일을 멈추고, 멈춘 뒤 현장에서 정지를 푼다(#17, 2026-11-26).

바탕화면 아이콘 '레일 멈춤'(pythonw 로 열어 검은 창이 없다 — '레일 입회 등록'과 같은 방식으로 사람이 만든다)을 두 번 누르면 이 창이 뜬다.
- 큰 빨간 '레일 멈춤': 늘 누를 수 있다. 확인 창 없이 바로 정지 표지를 만들고, 모터 기록으로 확인된 뒤에만 '멈춤 확인'(초록)을 띄운다.
  3초 안에 확인이 없으면 '멈춤 확인 못 함 — 레일에 손대지 마세요. 드라이브 전원 스위치를 끄세요'(빨강).
- '정지 풀기': 모터 프로그램이 돌지 않고, 확인 두 개를 체크하고 이름을 넣었을 때만 눌린다. 풀면 표지는 기록으로 옮겨진다.
  원격(채팅)으로는 풀지 않는다 — 다시 움직이게 하는 일이라 레일 옆 사람이 해야 한다(사용자 확정 D2). 다음 운전에는 입회 표지도 따로 필요하다.
문구 · 판단은 tools/rail_stop.py · motionbench/rail_stop.py 와 같다.
"""
from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rail_stop as S  # noqa: E402  (tools/rail_stop.py — 정지 명령)
from motionbench import rail_stop as R  # noqa: E402

DEFAULT_BY = "김건우"
CHECKS = ("레일 위와 둘레에 사람 손 · 물건이 없다", "멈춘 까닭을 확인했다")
RED, GREEN, GRAY = "#b3261e", "#1b6e3a", "#555"


def main() -> int:
    root = tk.Tk()
    root.title("레일 멈춤")
    root.resizable(False, False)
    font, big = ("맑은 고딕", 12), ("맑은 고딕", 20, "bold")
    pad = {"padx": 14, "pady": 4, "sticky": "w"}

    line1 = tk.Label(root, font=font, fg=GRAY, justify="left", wraplength=560)
    line1.grid(row=0, column=0, columnspan=2, **pad)
    line2 = tk.Label(root, font=font, fg=GRAY, justify="left", wraplength=560)
    line2.grid(row=1, column=0, columnspan=2, **pad)
    result = tk.Label(root, font=("맑은 고딕", 13, "bold"), justify="left", wraplength=560, text="")
    result.grid(row=3, column=0, columnspan=2, **pad)

    def stop():
        result.config(text="멈추는 중…", fg=GRAY)
        lines: list[str] = []

        def work():
            code = S.stop("운전자 화면", "사람 요청", say=lines.append)
            root.after(0, lambda: result.config(text=lines[-1] if lines else "?", fg=GREEN if code == S.OK else RED))
        threading.Thread(target=work, daemon=True).start()

    tk.Button(root, text="레일 멈춤", font=big, bg=RED, fg="white", activebackground="#8c1d18", width=16, height=2,
              command=stop).grid(row=2, column=0, columnspan=2, padx=14, pady=10)

    tk.Label(root, text="── 정지 풀기 (레일 옆에서만) ──", font=("맑은 고딕", 11, "bold")).grid(row=4, column=0, columnspan=2, **pad)
    vars_ = []
    for i, c in enumerate(CHECKS):
        v = tk.BooleanVar(value=False)
        tk.Checkbutton(root, text=c, variable=v, font=font).grid(row=5 + i, column=0, columnspan=2, **pad)
        vars_.append(v)
    tk.Label(root, text="이름", font=font).grid(row=7, column=0, **pad)
    by = tk.Entry(root, font=font, width=12)
    by.insert(0, DEFAULT_BY)
    by.grid(row=7, column=1, **pad)

    def clear():
        if S.motor_running()[0]:
            result.config(text="모터 프로그램이 돌고 있어 풀지 않는다", fg=RED)
            return
        dst = R.clear(by.get().strip())
        result.config(text=(f"정지 풀림 {time.strftime('%H:%M:%S')} · {by.get().strip()} — 다음 운전을 띄울 수 있다(입회 표지는 따로 필요)"
                            if dst else "풀 정지 표지가 없다"), fg=GREEN if dst else GRAY)

    btn = tk.Button(root, text="정지 풀기", font=("맑은 고딕", 12, "bold"), width=12, command=clear, state="disabled")
    btn.grid(row=8, column=0, columnspan=2, padx=14, pady=8)

    def tick():
        try:
            run, who = S.motor_running()
            st = R.active()
            line1.config(text=f"지금: {'운전 중 — ' + who if run else '운전 없음'} · "
                              + ("정지 표지 없음" if st is None else f"정지 표지 있음 — {st.get('at', '?')} {st.get('by', '?')} · {st.get('why', '?')}"))
            same, l2 = R.watch_matches(S.holder_pid())
            line2.config(text=l2, fg=RED if same is False else GRAY)
            ok = st is not None and not run and all(v.get() for v in vars_) and by.get().strip()
            btn.config(state="normal" if ok else "disabled")
        except Exception as e:            # 상태 줄이 깨져도 '레일 멈춤' 버튼은 늘 살아 있어야 한다
            line1.config(text=f"상태를 읽지 못함 — {type(e).__name__}: {e}", fg=RED)
        root.after(1000, tick)

    tick()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

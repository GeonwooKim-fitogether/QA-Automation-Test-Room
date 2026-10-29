# 레일 시험 결과판 시험 자료

`tests/test_rail_board.py` 와 화면 확인이 읽는 판정 파일이다. 이름은 모두 실제 규칙(`protocol_YYYYMMDD-HHMMSS.json`)을 따른다 — 그래야 서버의 `/api/rail/runs` 가 실제 파일과 같은 길로 잡는다. 그래서 가짜인지는 이름이 아니라 **파일 안의 `_sample: true` 칸**으로 표시하고, 서버 요약은 이것을 `sample: true` 로 넘겨 화면이 "표본"이라고 보일 수 있게 한다.

| 파일 | 진짜 · 가짜 | 모양 | 무엇을 보이려고 |
|---|---|---|---|
| `protocol_20261029-155408.json` | **실제 시험** (2026-10-29, `C:\Dev\dynamic-bench\cell-bench\data\` 에서 그대로 복사) | v1 — `conditions` · `criteria` 없음 | 합격 23 · 불합격 1. 칸이 없는 옛 모양을 화면이 "이 시험에 없음"으로 보이는지 |
| `protocol_20261030-090000.json` | **가짜 표본** (`_sample: true`) | v4 — 시뮬레이터, 파트 A · C | 합격 20 · 불합격 2 · 보류 2, 살펴봄, `sog_band` 다섯 구간, 가속도 단계별 `imu` · `gate_*`, 기준 상태 제안값 · 미정 · 확정 |
| `protocol_20261030-140000.json` | **가짜 표본** (`_sample: true`) | v4 — 중계기, 파트 C, 장면 이름 없음 | 장비 무효(`rig.유효` false + 이유 둘)로 24대 모두 무효, 중계기 조건의 GPS 값(정보용) |

가짜 표본의 칸 이름 가운데 약속(`dynamic-bench/design/qc-protocol-v4.md` 6-3 · 7절)에 정해진 것은 `conditions` · `rig` · `verdict.판정` · `sog_band` 의 `{band, rmsd_mm_s, ratio, n}` · `criteria` 의 `{value, status}` · `grid` 다. 셀 항목 안의 `value` · `unit` · `judge` · `z` · `a_mm_s2` · `dock.col/row` 같은 칸은 약속에 아직 없어 표본이 임시로 정했다 — 판정 코드가 v4 파일을 쓰기 시작하면 그 모양에 맞춰 고친다.

이 파일들을 실제 데이터 폴더(`cell-bench/data/`)로 옮기지 않는다.

# 레일 시험 결과판 시험 자료

`tests/test_rail_board.py` 와 화면 확인이 읽는 판정 파일이다. 이름은 모두 실제 규칙(`protocol_YYYYMMDD-HHMMSS.json`)을 따른다 — 그래야 서버의 `/api/rail/runs` 가 실제 파일과 같은 길로 잡는다. 그래서 가짜인지는 이름이 아니라 **파일 안의 `_sample: true` 칸**으로 표시하고, 서버 요약은 이것을 `sample: true` 로 넘겨 화면이 "표본"이라고 보일 수 있게 한다.

칸 이름의 정본은 `dynamic-bench/design/qc-protocol-v4.md` 7-6 절이다. v4 파일의 셀은 `cells.<시리얼>.items.<id> = {value, unit, verdict, z, basis}`, 셀 종합은 `cells.<시리얼>.verdict = {overall, flags, reasons}`, 독 자리는 `cells.<시리얼>.dock = {col, row}`(1부터), 항목의 갈래는 `criteria.<id>.kind`(합불 · 살펴봄 · 정보용 · 보류)다. 셀 항목의 `verdict` 는 갈래를 따른다 — 합불은 pass · fail · hold, 살펴봄은 벗어나지 않으면 pass · 벗어나면 flag(재료가 없으면 hold), 정보용은 늘 info, 보류는 늘 hold. `z` 는 살펴봄 항목에만 숫자가 있다.

## 표본 세트

가짜 표본은 모두 **`make_samples.py` 한 곳에서 만든다.** 손으로 고치지 않는다 — 검사(`test_sample_set_is_exactly_what_the_generator_makes`)가 이 폴더의 파일이 생성기 출력과 글자 하나까지 같은지 본다. 10-29 의 가짜 넷은 디자이너가 결과판 시안을 그릴 때 쓴 표본과 셀 항목 값(`value`)이 같다. 달라진 것은 7-6 의 7 · 8 번(`criteria.<id>.kind` 를 더하고, 갈래에 맞춰 `sog_band` 를 정보용 — verdict info · z null — 으로, `gps_move` 에 살펴봄 z 를 더함)과 `_sample: true` 뿐이다.

| 파일 | 진짜 · 가짜 | 모양 · 조건 | 무엇을 보이려고 |
|---|---|---|---|
| `protocol_20261029-155408.json` | **실제 시험** (2026-10-29, `C:\Dev\dynamic-bench\cell-bench\data\` 에서 그대로 복사) | v1 — 한글 키 `verdict.판정`, `conditions` · `criteria` 없음 | 합격 23 · 불합격 1. 칸이 없는 옛 모양을 화면이 "이 시험에 없음"으로 보이는지 |
| `protocol_20261029-172000.json` | 가짜 | v4 · 시뮬레이터 · 파트 A · C · 실험실 | 합격 20 · 불합격 2(clock · imu) · 보류 2(장면 고정 못 함). 살펴봄 벗어남(gate_* · pos_h · cn0_sim), `sog_band` **등급 경계 4개**(`[150, 300, 450, 600]`, 제안값), 가속도 단계별 `imu` · `gate_*` |
| `protocol_20261029-184000.json` | 가짜 | v4 · 테라스(실제 하늘) · 파트 C · B | 합격 22 · 불합격 2(clock · rx). 파트 B 의 `gps_move`(살펴봄, 기준 미정) |
| `protocol_20261029-193000.json` | 가짜 | v4 · 테라스 · 파트 C · 장비 무효 | `rig.유효` false + 이유로 24대 모두 무효(void) |
| `protocol_20261029-210000.json` | 가짜 | **v5** — 결과판이 모르는 판 | 화면이 "이 판은 아직 지원 안 함"을 보이고 멈추지 않는지. 서버는 거부하지 않고 판 이름을 그대로 넘긴다 |
| `protocol_20261030-090000.json` | 가짜 | v4 · 시뮬레이터 · 파트 A · C · 공장(같은 세트의 다른 날) | `sog_band` **등급 경계 없음**(`value: null`, 미정 — 화면은 "등급 경계 없음"), `gate_*` 갈래 **보류**(움직임 판단 프록시 확인 전), 보류 이유 둘(장면 고정 못 함 · FTG 못 받음) |
| `protocol_20261030-140000.json` | 가짜 | v4 · 중계기 · 파트 C(50 · 500 두 단계) · 장면 없음 | 합격 23 · 불합격 1(imu). 중계기 조건의 `gps_rx` 를 갈래 **정보용**으로(7-2), 10696 이 낮은 가속도에서 움직임 판단이 켜지지 않는 살펴봄 |

여섯 v4 · v5 표본을 합치면 셀 종합 넷(pass · fail · hold · void)과 갈래 넷(합불 · 살펴봄 · 정보용 · 보류)이 모두 한 번 이상 나온다.

## 다시 만들기

```
python tests/fixtures/rail/make_samples.py                     # 이 폴더의 가짜 표본을 다시 쓴다
python tests/fixtures/rail/make_samples.py --js <rail-data.js>  # 시안 화면용 window.RAIL_RAW 파일도 같은 표본으로 쓴다
```

`cell-bench` 폴더에서 실행한다. 독 배치는 실제 데이터 폴더가 아니라 이 폴더의 v1 실제 파일의 `grid` 에서 읽는다(`dock_layout.json` 의 Y 세트와 같다).

7-6 이 정하지 않아 표본이 정한 것: 기준이 없는 정보용 항목(`sog_lag` · `turn`)의 `criteria` 는 `value: null`, `status: "확정"`(정보용으로 확정이라는 뜻)으로 둔다. 판정 코드가 v4 파일을 쓰기 시작하면 그 모양에 맞춰 생성기를 고친다.

이 파일들을 실제 데이터 폴더(`cell-bench/data/`)로 옮기지 않는다.

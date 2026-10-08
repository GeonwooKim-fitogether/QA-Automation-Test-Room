# 원격 모니터링 · 제어

> 한 줄 요지: **결과판은 Tailscale(내 기기끼리만 통하는 사설망)로 휴대폰에서 열고, 이상은 Slack 으로 받는다.** 원격 명령은 플러그 켜기·끄기·안전 정지 세 가지뿐이고, 모두 PIN 을 한 번 더 묻는다. 외부에 포트를 열지 않는다.

```
휴대폰(Tailscale 앱) ──사설망──▶ 제어 PC :8765 결과판 ──control.json──▶ run_cycle.py ──▶ 플러그 · 셀
                                      ▲                                          │
Slack 채널 ◀──웹훅(유선 인터넷)────────┴──────────────────────────────────────────┘ 이상 · 사이클 끝
```

## 0. 두 가지 길 — 회사 정책에 따라 고른다

| 길 | 어떻게 | 정책 영향 | 상태 |
|---|---|---|---|
| **클라우드 결과판 (기본)** | TestPC 가 Supabase `cell-bench` 에 1분마다 올리고(밖으로만), 결과판은 Supabase 에서 읽는다. 로그인은 Supabase Auth(팀 계정) | 회사망에 들어오는 연결 없음 → 정책 무관 | **2026-10-08 가동** — 주소 https://cell-bench-board.vercel.app (아래 6절) |
| Tailscale 로 로컬 결과판 열기 | 아래 1~5절 | VPN 류라 회사 승인 필요 | 승인 대기 |

## 1. 무엇이 되고 무엇이 안 되나

| 어디서 | 보기 | 플러그 ON/OFF · 안전 정지 | 시험 다시 시작 | 셀 켜기(Dock 버튼) |
|---|---|---|---|---|
| 제어 PC 앞 | ○ | ○ | ○ | ○ |
| 휴대폰·다른 PC (Tailscale) | ○ | ○ (PIN) | ✕ — PC 앞에서 | ✕ — 사람 손 |
| Tapo 앱 | ✕ | ✕ (시험망에 인터넷이 없어 플러그가 Tapo 서버에 안 닿음) | | |

**제어 PC 가 꺼지면 원격으로 할 수 있는 것이 없다.** 그래서 PC 전원·절전 설정(`tools/check_env.py`)이 원격 시스템의 전제다.

## 2. 설치 — 사람이 하는 것 (한 번, 약 15분)

Tailscale 은 계정 로그인이 필요한 프로그램이라 **직접 설치해 주셔야** 합니다.

1. **PC:** https://tailscale.com/download 에서 Windows 용을 받아 설치 → 로그인(회사 Google 계정 등) → 트레이 아이콘이 "Connected" 가 됩니다.
2. **PC, 관리자 PowerShell:** 결과판 포트를 내 사설망에 연다.
   ```powershell
   tailscale serve --bg 8765
   ```
   출력에 `https://<pc이름>.<tailnet>.ts.net/` 주소가 나옵니다. 이것이 휴대폰에서 여는 주소입니다. 한 번 하면 재부팅 뒤에도 유지됩니다.
3. **휴대폰:** 앱스토어에서 Tailscale 설치 → 같은 계정으로 로그인 → 연결 켜기 → 브라우저로 위 주소를 엽니다.
4. **PC:** `python tools/remote_setup.py` → PIN(숫자 4~8자리)과 Slack 웹훅(선택)을 입력 → `serve_board.py` 다시 시작.

**성공하면:** 휴대폰에서 결과판이 뜨고, 운영 탭 아래에 "원격 제어" 칸이 보입니다. Wi-Fi 를 끄고 LTE 로도 열려야 합니다.

## 3. Slack 알림

Slack 에서 **앱 → Incoming Webhooks** 로 채널 하나에 웹훅 주소를 만들어 `remote_setup.py` 에 넣습니다(관리자 승인이 필요할 수 있음). 보내는 것:

- 사이클이 끝날 때 한 줄 요약 (방전 h · 충전 분 · Wh · 추출 · 이상 건수)
- 이상: 셀 전체 끊김 · 사이클 중단 · 프로그램 오류 · Dock 버튼 필요 · 플러그 무응답 · 만충 시간 초과 · 원격 명령 · Wi-Fi 재연결

웹훅이 없으면 아무것도 보내지 않고 시험은 그대로 돈다. 같은 글은 30초 안에 한 번만.

## 4. 원격 명령이 하는 일

| 명령 | 지금 단계가 방전이면 | 충전이면 | 복구 대기면 |
|---|---|---|---|
| **플러그 켜기** | 방전을 끝내고 충전으로 (기록에 `manual_charge`) | 그대로 | 켠다 |
| **플러그 끄기** | 그대로 | 만충으로 치고 다음 사이클 (기록에 `manual_stop_charge`) | 끈다 |
| **안전 정지** | 플러그를 **켠 채** 프로그램 종료. 다시 시작은 PC 에서 `python run_cycle.py` |||

명령은 파일(`data/control.json`)로 전달되고 시험 프로그램이 20초 표본마다 집어 간다. 결과는 결과판의 "마지막 응답"에 나온다. 모든 명령은 `events.csv` 에 `manual` 로 남는다.

## 5. 보안 — 무엇으로 막나

- **Tailscale:** 내 계정으로 로그인한 기기만 이 주소에 닿는다. 공인 인터넷에서는 보이지 않는다.
- **PIN:** 명령마다 묻고, 5번 틀리면 그 기기는 10분 잠긴다. PIN 은 Windows 자격 증명 관리자에만 있다.
- **범위:** 원격으로 할 수 있는 것은 플러그 세 동작뿐이다. 셀 삭제·설정 변경·프로그램 시작은 원격에 없다.
- **기본은 충전 쪽:** 멈출 때는 항상 플러그 ON. 실수해도 셀이 방전돼 꺼지는 쪽으로는 가지 않는다.

**정해 둘 것:** Tailscale 계정을 개인 계정으로 할지 회사 계정으로 할지. 제어 PC 를 다른 노트북으로 옮기면 그 노트북에서 1·2·4 를 다시 한다 (`docs/control-pc-setup.md`).

## 6. 클라우드 결과판 — 설치

```
TestPC ──HTTPS(밖으로만)──▶ Supabase cell-bench ◀──HTTPS── 결과판 (Vercel 등 정적 호스팅 · 어디서든 · 팀 로그인)
   표: bench_state(지금) · bench_sample(1분) · bench_cycle · bench_discharge · bench_event · bench_command(원격 명령)
```

1. **TestPC — 키 저장:** `python tools/cloud_setup.py` → Supabase 대시보드 → cell-bench → Project Settings → API 의 **service_role (secret)** 키를 넣는다. 이 키는 TestPC 에만 둔다. `python tools/cloud_setup.py --test` 로 연결 확인 → `run_cycle.py` 다시 시작하면 전송이 켜진다(로그 첫 줄 "클라우드 전송 켜짐").
2. **결과판 배포:** `python tools/build_cloud_board.py` → `deploy/board/` (index.html + cloud-config.js, anon 키는 공개용). Vercel 프로젝트 `cell-bench-board`(팀 geonwookim-5977's projects)가 이 저장소의 `cell-bench/deploy/board` 폴더에 연결돼 있어 **브랜치에 푸시하면 자동으로 다시 배포된다.** 주소: **https://cell-bench-board.vercel.app** (2026-10-08 첫 배포). Vercel 자체 로그인 보호(Vercel Authentication)는 꺼 두었다 — 결과판의 Supabase 로그인이 그 역할을 한다.
3. **팀 로그인:** Supabase 대시보드 → cell-bench → Authentication → Users → **Add user** 로 팀원 이메일·비밀번호를 만든다(초대 메일 없이). 결과판 첫 화면에서 그 계정으로 로그인한다.
4. **원격 명령:** 로그인한 사용자가 결과판에서 플러그 켜기·끄기·안전 정지를 누르면 `bench_command` 에 한 줄이 들어가고, TestPC 가 20초 안에 집어 가 결과를 적는다. 누가 눌렀는지(`requested_by`)가 남는다. PIN 은 묻지 않는다 — 로그인이 그 역할을 한다.

**보안:** 읽기·명령 넣기는 로그인한 사용자만(RLS). 쓰기(측정값)는 TestPC 의 service_role 키만. 결과판 정적 파일에는 anon 키만 들어가며, anon 키로는 RLS 때문에 아무것도 읽거나 쓸 수 없다.

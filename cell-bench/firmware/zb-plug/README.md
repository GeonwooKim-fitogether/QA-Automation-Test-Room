# zb-plug — ESP32-C6 를 지그비 코디네이터로 만들어 레일 독 충전 플러그를 PC 가 켜고 끄게 하는 펌웨어

> 한 줄 요지: **이 폴더의 `.bin` 세 개를 ESP32-C6 보드에 구워 제어 PC 에 USB 로 꽂으면, 그 보드가 지그비 망을 만들고 아카라 스마트 플러그 한 대를 짝지어 PC 명령으로 켜고 끄고 전력을 읽는다.** PC 쪽은 `cellbench/zbplug.py`(프로그램에서 부를 때)와 `tools/zbplug_cli.py`(사람이 손으로 부를 때)다.

## 왜 이것을 만들었나

레일(Dynamic Bench) 독 충전 플러그였던 Tapo P110M 이 2026-11-05 펌웨어부터 새 인증 방식(TPAP)으로 바뀌어 PC 프로그램이 켜고 끌 수 없게 됐다. 게다가 세트마다 LiveHub 와이파이 망이 바뀌어 플러그도 따라 옮겨야 했다. 지그비 플러그는 허브 · 계정 · 인터넷 · 와이파이가 필요 없다. 이 보드 하나가 망을 만들고 명령을 보낸다(2026-11-06 결정, 부품은 11-07 도착).

## 구성

| 항목 | 값 |
|---|---|
| 보드 | ESP32-C6 Super Mini (칩 ESP32-C6FH4, 플래시 4 MB). 제어 PC2 에 꽂힌 것은 USB 일련번호 `14:C1:9F:E6:6E:E8`, 포트 COM3 |
| 플러그 | 아카라 스마트 플러그 EU (SP-EUC01, 지그비 3.0) |
| 지그비 채널 | **25번**. 시험실 2.4 GHz 와이파이(2 · 6 · 7 · 11번, 셀 라이브 망 FTG-3D93 은 6번 — 2026-11-09 확인)와 주파수가 겹치지 않는다 |
| 개발 도구 | ESP-IDF v5.3.2, esp-zigbee-lib 1.6.8 · esp-zboss-lib 1.6.4 (ZBOSS 기반 1.x 장기 지원판). 2.x 는 함수 모양이 달라 이 원본과 맞지 않는다 |
| 망 정보 | 보드 플래시의 `zb_storage` · `zb_fct` 자리에 남는다. 앱만 다시 구우면(0x10000) 망과 짝지은 플러그가 그대로 남는다 |

## PC 와 주고받는 말

PC 는 한 줄 명령 뒤에 매번 새 표지(`#` 로 시작하는 낱말)를 붙이고, 보드는 같은 표지를 붙여 답한다. 보드는 PC 가 포트를 열지 않은 동안에도 사건 줄(`EVT ...`)을 쌓아 두기 때문에, 표지가 없으면 PC 가 옛 줄을 이번 답으로 잘못 읽는다.

| 명령 | 하는 일 | 답 |
|---|---|---|
| `PING` | 살아 있나 · 망 · 짝지은 플러그 | `OK #t PONG fw=… net=1 ch=25 pan=… plug=<IEEE 주소> …` |
| `JOIN [초]` · `CLOSE` | 짝짓기 창을 연다(최대 254초) · 닫는다 | `OK #t JOIN open=…` |
| `STATE` | 플러그에서 켜짐 · 전력을 새로 읽는다 | `STATE #t on=<0·1·?> w=<W·nan> src=<em·ai·none> …` |
| `ON` · `OFF` | 켜거나 끈 뒤 새로 읽은 상태로 답한다 | `STATE` 와 같고 `ack=1` 이면 플러그가 명령을 받았다고 답한 것 |
| `INFO` | 플러그의 제조사 · 모델 · 펌웨어 판 | `OK #t INFO 0x0004="LUMI" …` |
| `DESC` | 플러그의 엔드포인트 · 클러스터 목록(전력 측정을 지원하는지) | `EVT DESC …` 줄들과 `OK` |
| `SETUP` | 플러그가 켜짐 · 전력 변화를 스스로 보고하게 묶는다(짝지은 직후 자동으로도 한다) | `OK` 와 `EVT BIND` · `EVT REPORTCFG` |
| `ADOPT` · `FORGET` | 마지막에 붙은 기기를 '그 플러그'로 정한다 · 잊는다 | `OK` |
| `FACTORY YES` | 지그비 망을 지우고 새로 만든다(플러그를 다시 짝지어야 한다) | 보드가 다시 켜진다 |

전력은 두 길로 읽는다. 먼저 표준 전기 측정 클러스터(0x0B04 의 순간 전력)를 읽고, 플러그가 지원하지 않는다고 답하면 옛 아카라 방식(엔드포인트 21 의 아날로그 입력)을 읽는다. 아카라 EU 플러그는 펌웨어 판에 따라 표준 전력 값을 내지 않는 경우가 알려져 있어 두 길을 둔다.

포트를 열 때 DTR · RTS 를 내린 채 연다. ESP32-C6 의 USB 시리얼은 RTS 를 올리면 보드가 다시 켜지기 때문이다. 기록(로그)은 보드의 UART 로만 나가고 USB 에는 경고 이상만 `LOG …` 로 섞인다.

## 사람이 손으로 쓰는 법

제어 PC2 의 `C:\Dev\QA-Automation-Test-Room\cell-bench` 에서(파이썬은 pyserial 이 깔린 것):

```bash
python tools/zbplug_cli.py ping
```

```bash
python tools/zbplug_cli.py pair 300
```

```bash
python tools/zbplug_cli.py status
```

`pair` 를 켠 뒤 플러그 버튼을 파란 불이 빠르게 깜빡일 때까지 약 5초 누르면 `EVT JOIN …` 이 뜨고 창이 닫힌다. 켜기 · 끄기는 `on` · `off` 인데, 독 충전이 바로 바뀌므로 시험 중에는 부르지 않는다.

## 다시 굽기

ESP-IDF 없이 굽는 법 — esptool 5 가 있으면 이 폴더에서:

```bash
python -m esptool --chip esp32c6 -p COM3 -b 460800 --before default-reset --after hard-reset write-flash --flash-mode dio --flash-freq 80m --flash-size 4MB 0x0 bin/bootloader.bin 0x8000 bin/partition-table.bin 0x10000 bin/zb-plug.bin
```

앱만 바꿀 때는 마지막 쌍(`0x10000 bin/zb-plug.bin`)만 구우면 망과 짝지은 플러그가 남는다. 플래시를 통째로 지우면(`erase-flash`) 망이 사라져 플러그를 다시 짝지어야 한다.

빌드는 제어 PC2 에 따로 깐 ESP-IDF v5.3.2(`C:\esp\esp-idf-v5.3.2`, 도구는 `C:\esp\tools-5.3`)로 한다. 이 PC 에는 옛 5.0 이 `IDF_PATH` 로 걸려 있어 그 두 환경 변수를 이번 창에서만 바꿔 쓴다.

```powershell
$env:IDF_TOOLS_PATH='C:\esp\tools-5.3'; $env:IDF_PATH='C:\esp\esp-idf-v5.3.2'; . C:\esp\esp-idf-v5.3.2\export.ps1; idf.py build
```

`partitions.csv` 와 `sdkconfig.defaults` 에는 한글을 쓰지 않는다. ESP-IDF 의 분할표 도구가 이 파일을 한국어 Windows 기본 인코딩(cp949)으로 읽다 실패해, 앱 굽는 주소가 굽기 목록에서 빠진 일이 있었다(2026-11-09).

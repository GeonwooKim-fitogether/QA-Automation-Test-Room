# hr-sim — ESP32-C3 를 BLE 심박대로 만드는 펌웨어

> 한 줄 요지: **이 폴더의 `.bin` 세 개를 ESP32-C3 보드(4 MB 플래시)에 구우면, 그 보드는 `HRSIM-A1B2` 같은 이름의 표준 심박대가 되어 1초마다 60~160 bpm 사이 심박을 보낸다.** 셀에는 0x20 명령으로 이 이름을 써 주면 그 보드에서 심박을 받는다. 보드 하나는 `flash.cmd`, 25개는 `flash_all.py --watch` 로 굽는다.

**상태: 보드 1개(HRSIM-7180, 칩 v0.4)에서 굽기 · 부팅 · 심박값 변화까지 확인했다(2026-10-13).** 셀이 이 보드의 심박을 받는지와 여러 개를 동시에 켜는 시험은 아직이다 — 순서는 맨 아래 '처음 굽고 확인하는 순서'에 있다.

## 보드가 하는 일

| 항목 | 값 |
|---|---|
| 광고하는 서비스 | 표준 심박 서비스 `0x180D`. 외형(appearance)은 `0x0340`(심박 센서) |
| 광고 이름 | `HRSIM-` 뒤에 **보드 기본 MAC 의 끝 4자리**(16진 대문자). 예: MAC `34:85:18:12:a1:b2` → `HRSIM-A1B2`. 10바이트라 셀의 0x20 이름 한도(UTF-8 24바이트, `cellbench/protocol.py` 의 `ble_id_frame`)에 그대로 들어간다 |
| 심박 측정값 `0x2A37` | 1초마다 알림(notify). 플래그 `0x06`(8비트 심박, 센서 접촉됨) + bpm 1바이트 |
| bpm 이 움직이는 모양 | 110 ± 50, 즉 60~160 사이를 **5분에 한 바퀴** 도는 사인파. 1초에 많아야 약 1 bpm 바뀐다. 보드마다 MAC 으로 출발점이 달라 25개가 같은 값을 내지 않는다 |
| 몸 위치 `0x2A38` | 읽으면 `1`(가슴) |
| 연결 | 동시에 3개까지. 연결된 뒤에도 자리가 남으면 계속 광고한다(셀이 붙어 있어도 휴대폰으로 확인할 수 있게). 짝짓기(pairing)는 요구하지 않는다 |
| USB 시리얼 출력 | 5초마다 `HRSIM name=HRSIM-A1B2 bpm=117 conns=1`. 켜질 때 `HRSIM boot name=… base_mac=…`, BLE 주소 `HRSIM ble_addr=…`, 연결·끊김·구독도 한 줄씩 |

이름에 쓰는 MAC 은 esptool 이 굽는 중에 `MAC:` 으로 출력하는 **기본(공장) MAC** 이다. 휴대폰 스캐너에 보이는 BLE 주소는 ESP32-C3 규약상 기본 MAC 에 2를 더한 값이라 끝자리가 다를 수 있다 — 주소가 아니라 이름으로 찾으면 된다.

## 파일

| 파일 | 내용 |
|---|---|
| `bin/bootloader.bin` · `bin/partition-table.bin` · `bin/hr-sim.bin` | 구울 것 세 개. 어느 빌드에서 왔는지 · SHA-256 · 실측 기록은 `bin/BUILD.txt` |
| `flash.cmd` | 보드 하나 굽기 (아래 한 줄을 담은 Windows 배치 파일) |
| `flash_all.py` | 여러 보드 굽기 + 부팅 확인 + 이름 기록 (`hr-sim-boards.csv`) |
| `main/main.c` · `CMakeLists.txt` · `main/CMakeLists.txt` · `sdkconfig.defaults` | 펌웨어 원본 (ESP-IDF v5.3.2, NimBLE) |
| `../../tests/test_hr_sim.py` | 이름 규칙이 펌웨어 · 굽기 스크립트 · 0x20 프레임에서 같은지, 굽는 주소가 맞는지 |

## 보드 하나 굽기 — 명령 한 줄

준비물은 esptool 5 하나다(`pip install esptool`, TestPC 에는 5.5.0 이 깔려 있다). 보드를 **데이터 선**으로 PC 에 꽂고, 이 폴더(`cell-bench/firmware/hr-sim`)에서 실행한다.

```bash
python -m esptool --chip esp32c3 -b 460800 --before default-reset --after hard-reset write-flash --flash-mode dio --flash-freq 80m --flash-size 4MB 0x0 bin/bootloader.bin 0x8000 bin/partition-table.bin 0x10000 bin/hr-sim.bin
```

- 보드가 하나만 꽂혀 있으면 esptool 이 포트를 스스로 찾는다. 여러 개면 `-p COM5` 처럼 포트를 붙인다.
- Windows 명령 창에서는 같은 일을 `flash.cmd COM5`(포트 생략 가능)로 할 수 있다.
- TestPC 의 Claude 세션에서는 `python` 대신 전체 경로 `C:\Users\사용자 없음\AppData\Local\Programs\Python\Python312\python.exe` 를 쓴다(세션이 PATH 를 늦게 읽는다).

**성공하면 무엇이 보이나:** esptool 출력 끝에 `Hash of data verified.` 가 세 번, 이어서 `Hard resetting via RTS pin...` 이 나온다. 이름을 확인하려면 시리얼 모니터를 연다.

```bash
python -m serial.tools.miniterm COM5 115200
```

5초 안에 `HRSIM name=HRSIM-A1B2 bpm=… conns=0` 이 보이면 동작하는 것이다(나가기는 Ctrl+]).

## 25개 대량 굽기

`flash_all.py` 가 USB 장치 번호(303A:1001, ESP32-C3 의 USB-Serial/JTAG)로 보드를 찾아 굽고, 다시 켜진 보드가 자기 이름을 말하는지까지 확인한 뒤 `hr-sim-boards.csv` 에 한 줄을 남긴다(시각 · 포트 · MAC · 이름 · 결과). 결과는 `ok`(정상 — 상태 줄이 5초 간격으로 두 번, 멈춤 흔적 없음) · `no-boot`(구웠으나 정상 동작을 못 봄) · `flash-failed`(굽기 실패) 셋 중 하나다. 방법은 두 가지이고, **USB 포트가 하나뿐이면 방법 1** 을 쓴다.

### 방법 1 — 꽂는 대로 하나씩 (권장)

```bash
python flash_all.py --watch
```

1. 위 명령을 켜 둔다.
2. 보드를 꽂는다. 화면에 `[COM5] 굽는 중 …` 이 뜨고 약 20~30초 뒤 `[COM5] OK  HRSIM-A1B2 (MAC …) — 뽑아도 됩니다` 가 뜬다.
3. 그 이름을 **보드에 스티커로 붙이고** 뽑는다. 다음 보드를 꽂는다. 이미 구운 보드를 다시 꽂아도 같은 실행 안에서는 다시 굽지 않는다.
4. 25개를 다 하면 Ctrl+C. 보드당 30초 안팎이라 25개에 15분 남짓 걸린다.

실패한 보드는 꽂혀 있는 동안 다시 시도하지 않는다. 뽑았다 다시 꽂으면 그때 다시 굽는다.

### 방법 2 — 허브에 여러 개 꽂고 한꺼번에

```bash
python flash_all.py              # 지금 꽂힌 보드 전부, 동시에 8개씩
python flash_all.py --workers 4  # 허브 전원이 약하면 동시 개수를 줄인다
```

끝에 `끝: 24/25 성공` 처럼 요약과 보드별 결과가 나온다. 허브에 꽂힌 채로는 어느 보드가 어느 이름인지 눈으로 구분하기 어려우므로, 스티커는 CSV 의 MAC 과 보드를 하나씩 맞춰 붙여야 한다 — 그래서 방법 1 을 권한다.

### 굽기가 안 될 때

| 증상 | 할 일 |
|---|---|
| 보드가 목록에 안 보인다 (`ESP32-C3 보드(USB 303A:1001)가 안 보입니다`) | 선이 충전 전용이 아닌지 확인한다. 장치 관리자에 `USB 직렬 장치(COMx)` 가 생기는지 본다 |
| `flash-failed` — 연결 실패(`Failed to connect`) | 보드의 **BOOT 버튼을 누른 채** USB 를 꽂아 다운로드 모드로 넣은 뒤 다시 굽는다 |
| `no-boot` | 굽기는 됐지만 20초 안에 상태 줄이 5초 간격으로 두 번 나오지 않았거나 `Guru Meditation`(멈춤) 이 보였다. 시리얼 모니터로 본다. 켜지자마자 멈추고 다시 켜지기를 되풀이하면 펌웨어 문제이니 그 판의 .bin 을 쓰지 않는다 |

## 셀에 이름 쓰기

셀은 0x20 명령으로 받은 이름의 심박대에 붙는다. 이름은 `hr-sim-boards.csv` 의 `name` 칸이고, 명령 프레임은 `cellbench.protocol.ble_id_frame("HRSIM-A1B2")` 이 만든다.

**어느 셀에 어느 보드를 짝짓는지의 정본은 `bench.json` 세트 줄의 `hr_pairs` 다**(2026-10-15 — 세트 1: 보드 N ↔ 셀 11732+N, 허브 A 16 · B 8). 넣고 보내는 길은 셋이다.

1. **짝 표 넣기** — `python -X utf8 tools/hr_pair.py --import-csv 짝.csv --write` (칸: `no,hub,port,name[,serial]` — serial 이 없으면 보드 N ↔ 세트의 첫 셀 + N − 1).
2. **엔진이 스스로** — 짝 보드가 켜져 있고 연결 0 인데 셀 심박이 10분 넘게 0 이면 엔진이 그 셀에 이름을 다시 보낸다(셀당 30분에 한 번, 추출 중에는 하지 않는다).
   자세한 조건은 [cell-bench README '심박'](../../README.md).
3. **손으로** — `python -X utf8 tools/hr_pair.py --go` (전부) · `--go --serials 11740,11742` (그 셀만). 추출 중이거나 엔진이 TCP 60222 를 쓰는 중이면 하지 않고 끝난다.
   결과는 `data/hr_pair_log.csv`. 이것이 예전의 `.claude/state/pair_cells.py`(10-15 실측 24/24, 응답 `FITO 20 01 01 54`)를 저장소로 옮긴 것이다.

보드 상태(켜짐 · 연결 수 · bpm)는 감시자가 45초마다 USB 로 읽어 `data/hr_boards.json` 에 둔다 — 결과판 시험실 구성의 '심박 허브'를 누르면 포트별로 보인다.
지금 한 번 직접 읽으려면 `python -X utf8 tools/hr_pair.py --boards`(읽기만 — 포트를 여는 순간 DTR · RTS 를 올리지 않아 보드가 리셋되지 않는다).
짝 표에 없는 보드는 감시자가 열지 않으므로, 굽는 중인 새 보드와 부딪히지 않는다. 짝 표에 있는 보드를 다시 구울 때는 감시자가 포트를 잡고 있는 몇 초와 겹치면 굽기가 실패할 수 있다 — 그때는 다시 굽는다.

## 처음 굽고 확인하는 순서 (TestPC 세션용)

결정대로 검증 세트 5개를 먼저 하고, 문제가 없으면 나머지 20개로 넘어간다.

1. **굽기.** 보드 1개를 위 한 줄로 굽는다. `Hash of data verified.` 세 번이 나와야 한다.
2. **이름.** 시리얼 모니터에 `HRSIM name=HRSIM-xxxx` 가 5초마다 나오고, `xxxx` 가 esptool 이 출력한 MAC 끝 4자리와 같아야 한다.
3. **광고와 알림.** 휴대폰의 BLE 스캐너(예: nRF Connect)에서 그 이름이 보이고, 연결하면 Heart Rate 서비스의 Heart Rate Measurement 를 구독했을 때 1초마다 값이 온다. 시리얼에는 `HRSIM connect` · `HRSIM subscribe … notify=1` 이 찍힌다.
4. **셀.** 셀 하나에 0x20 으로 그 이름을 쓰고, 라이브 메시지의 심박(`Live.hr`, 1320B 의 51번째 바이트)이 60~160 사이에서 천천히 바뀌는지 본다. 10-07 에 PC 블루투스로 한 같은 시험(60~159 bpm 수신 성공)과 같은 결과가 나와야 한다. **추출(EXTRACT) 중에는 하지 않는다** — 셀에 명령을 보내려면 UDP 로 깨우고 셀이 PC 의 TCP 60222 로 접속해 와야 하는데, 추출 중에는 엔진이 그 포트를 쓰고 있다.
5. 5개 모두 통과하면 `flash_all.py --watch` 로 나머지 20개를 굽는다.

## 다시 빌드하기

**칩 리비전 하한은 기본(v0.3)을 둔다.** v0.0 으로 내려 빌드한 판은 v0.4 칩에서 BLE 를 켜자마자 `Illegal instruction` 으로 멈추고 다시 켜지기를 되풀이했다(2026-10-13). 받은 보드가 v0.4 이므로 기본값이 맞다. 칩 리비전은 굽는 중 esptool 출력의 `revision v0.x` 로 보인다.

이 PC(TestPC)에는 ESP-IDF 를 깔지 않는다(2026-10-13 결정). 빌드는 GitHub Actions 의 `hr-sim-build` 워크플로가 한다.

1. 원본(`main/` · `CMakeLists.txt` · `sdkconfig.defaults`)을 고쳐 push 하면 자동으로 돈다. 손으로 돌리려면 GitHub → 이 저장소 → Actions → hr-sim-build → Run workflow.
2. 3~5분 뒤 실행 결과 화면 아래 Artifacts 에서 `hr-sim-bin` 을 받는다. 안에 `.bin` 세 개, `flash_args`, `SHA256SUMS` 가 있다.
3. `.bin` 세 개를 `bin/` 에 덮어쓰고, `bin/BUILD.txt` 의 실행 번호 · 커밋 · SHA-256 을 고쳐 함께 커밋한다.

ESP-IDF 가 있는 다른 PC 에서는 이 폴더에서 `idf.py set-target esp32c3` 다음 `idf.py build` 로 같은 결과를 얻는다(v5.3.2 권장). 결과는 `build/bootloader/bootloader.bin` · `build/partition_table/partition-table.bin` · `build/hr-sim.bin` 이다.

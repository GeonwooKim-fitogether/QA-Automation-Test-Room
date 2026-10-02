# -*- coding: utf-8 -*-
# hardware-map DATA (제품별) — 이 파일만 제품에 맞게 채운다. 엔진(hardware_map_engine.py)은 고정.
# 아래는 CLBX-6A 실제 데이터(워크드 예시). 새 제품은 값만 바꿔 재사용.
import json, os, re
import re as _re

# ── 제품 메타(제목/출처/시트명) — 엔진이 화면에 표시 ──────────────────────────
META={
 'model':'CLBX-6A',
 'title':'CLBX-6A 시스템 이해 — 스키매틱 기반 관계도',
 'doc':'CLBX-6A_SCH_251110',
 'source':'CLBX-6A_SCH_251110 (4시트) · PCBA1-0043 / PCB01-0037',
 'basis':'블록다이어그램 · 전원/MCU · GNSS/IMU 시트',
 # 전체 시트 보기 제목 — key = 블록의 sh 값(시트 그룹)
 'sheet_titles':{'block':'블록다이어그램 (Sheet 1)','pwr':'전원·MCU·메모리·커넥터·LED (Sheet 3)','gnss':'GNSS·IMU·LED (Sheet 4)'},
}

# 산출물 경로 — 원하는 위치로
OUT='clbx6a-understand.html'
imgs=json.load(open('imgs.json'))
crops=json.load(open('crops.json'))

# ===== 과외 해설 (블록별 회로도 크롭 설명) =====
# 각: (한줄 소개, [ (아이콘, 굵은말, 설명) ... ])
TUTOR={
 'imu':('움직임을 재는 센서. 두뇌와 SPI로 대화하고, 움직임이 생기면 알려줍니다.',[
   ('🔌','전원 (왼쪽 위)','VDD·VDDIO에 3.0V·3.3V가 들어옵니다. C26·C29는 전원을 깨끗하게 걸러주는 커패시터.'),
   ('🔗','두뇌와의 통로 · SPI','아래 SCL/SPC·SDA/MOSI·SDO/MISO·CS 4가닥이 두뇌와 데이터를 주고받는 선. CS는 "너한테 말할게" 하고 부르는 선.'),
   ('🔔','알림선 INT1','움직임이 생기면 INT-IMU#로 두뇌를 즉시 깨웁니다(인터럽트).'),
   ('🧭','옆의 U16 (LIS2MDL)','방향(지자기)을 재는 센서. IMU 뒤에 붙어 함께 동작(보조).'),
   ('🧩','R33~R34','신호를 안정시키는 조연 저항.'),]),
 'charger':('도크에서 들어온 전기로 배터리를 충전하는 부품.',[
   ('⚡','입력 IN ← VUSB','도크(포고핀)에서 들어온 전기가 여기로.'),
   ('🔋','출력 OUT → SYS','충전한 전기를 시스템 전원(SYS)으로.'),
   ('🎚','ISET·VSET (저항)','충전 전류·전압을 정하는 저항. 여기 값으로 400mA·4.4V가 결정(상단 메모).'),
   ('🌡','TS','배터리 온도 감시(NTC) — 너무 뜨거우면 충전 정지(JEITA).'),
   ('🚦','STAT# → BATT-STAT','충전 중인지 상태를 알리는 선.'),]),
 'rtc':('전원이 꺼져도 시간을 유지하는 시계.',[
   ('🔋','전원 VDD·VBKP','VDD는 동작 전원, VBKP는 백업 — 메인 전원이 나가도 시계가 살아있게.'),
   ('🔗','두뇌와의 통로 · I²C','SCL·SDA 2가닥으로 두뇌와 시간을 주고받음.'),
   ('🔔','INT# → INT-RTC#','정해진 시각에 두뇌를 깨우는 알람선.'),
   ('🧩','R16·R18','I²C·알림선용 풀업 저항.'),]),
 'gnss':('위성 신호로 현재 위치를 계산하는 수신기.',[
   ('📡','RF_IN','안테나에서 정제된 위성 신호가 여기로 들어옴.'),
   ('🔗','두뇌와의 통로 · UART','TXD·RXD가 두뇌와 위치 데이터를 주고받는 선.'),
   ('⏱','TIMEPULSE','아주 정확한 1초 신호를 두뇌에 보냄(시계 보정용).'),
   ('♻️','RESET_N ← GNSS-RST#','두뇌가 GPS를 리셋하는 선.'),
   ('🔋','VCC·V_BCKP','동작 전원과 백업 전원(빠른 재측위용).'),]),
 'gnssfe':('안테나와 수신기 사이에서 약한 위성 신호를 증폭·정제.',[
   ('🔈','BGA525N6 (LNA)','아주 약한 위성 신호를 키우는 증폭기.'),
   ('🧹','필터 (NJG·SAW)','원하는 위성 주파수만 남기고 잡음 제거.'),
   ('➡️','신호 순서','안테나 → ESD보호 → 증폭(LNA) → 필터 → 수신기(RF_IN).'),]),
 'gnssant':('하늘의 위성 신호를 받는 안테나.',[
   ('📡','패치 안테나','L1/L5 두 주파수를 받는 25mm 패치형.'),
   ('🛡','D12 (ESD)','정전기로부터 안테나 입력을 보호.'),]),
 'mcu':('전체를 지휘하는 두뇌. 각 IO핀이 곧 관계도의 버스.',[
   ('🔌','전원 3V3·EN','왼쪽 위로 3.3V 전원과 켜짐(EN) 신호.'),
   ('🔗','IO핀 = 버스','IO01/02=I²C, IO03~06=SPI, IO17/18=UART(GNSS), IO35~40=SD, IO19/20=USB.'),
   ('💡','LED·전원 제어','IO27~29·33/34/47=LED, IO21=PWR-HOLD(전원 유지).'),
   ('📶','무선 내장','WiFi/BT는 이 모듈 안에 내장 — 별도 부품 없음.'),
   ('🧩','C1~C8','전원을 안정시키는 커패시터.'),]),
 'power':('배터리 전기를 부품마다 맞는 전압으로 바꾸는 곳.',[
   ('🔽','U5 벅(TPS62291)','SYS를 효율 좋게 3.3V로 (메인 전원).'),
   ('🔧','LDO (U9·U10·U20)','3.0V(센서용)·백업 3.0V를 깨끗하게 생성.'),
   ('🧲','L3 인덕터','벅이 전압을 낮출 때 쓰는 코일.'),
   ('🎛','EN (PWR-EN·SENS-EN)','두뇌가 각 전원을 켜고 끄는 스위치선.'),]),
 'gauge':('배터리에 얼마 남았나(%)를 정밀하게 재는 부품.',[
   ('🔗','두뇌와 I²C','잔량·전압·온도를 두뇌에 알려줌.'),
   ('📈','BATT핀','배터리 전압을 읽어 내부 모델로 잔량(SOC %)을 계산.'),]),
 'battery':('실제 배터리 셀.',[
   ('🔋','Li-Po 500mAh','CON1 커넥터로 보드에 연결.'),
   ('⚡','VBAT','여기서 나온 전기가 충전IC·게이지·전원부로 감.'),]),
 'sw':('전원 On/Off 버튼.',[
   ('🔘','SW1','누름 신호(PWR-DECT)를 두뇌가 읽어 전원을 켜고 끔.'),]),
 'pogo':('도크에 올리면 닿는 포고핀 커넥터.',[
   ('⚡','VUSB','도크에서 충전 전기가 들어옴.'),
   ('🔗','USB','데이터·펌웨어 통로(USB-DM/DP).'),
   ('🛡','D5~D8','USB 데이터선 정전기 보호.'),]),
 'sd':('측정 데이터를 파일로 저장하는 카드.',[
   ('🔗','SDIO 6가닥','SD-D0~D3·CLK·CMD로 두뇌와 고속 통신.'),
   ('🛡','U6 (EMIF)','SD 신호의 정전기·노이즈를 걸러주는 필터.'),]),
 'led':('상태·충전을 불빛으로 알리는 부분.',[
   ('🔴','RGB LED (D1·D2)','두뇌가 빨강·초록·파랑을 조합해 상태 표시.'),
   ('🔌','MP1·MP2 (MOSFET)','여러 LED를 켜는 스위치.'),
   ('🔋','U12 (TS5A3159)','충전 상태에 따라 충전 LED를 전환.'),]),
}

# ===== 블록별 흐름(in→out) + 부품별 역할 =====
# FLOW: {'in':[(신호,설명)], 'core':핵심, 'out':[(신호,설명)]}
FLOW={
 'mcu':{'in':[('3V3','전원'),('EN','칩 켜짐'),('센서 신호','SPI·I²C·UART 수집')],'core':'ESP32-S3 (U3)','out':[('WiFi/BT','무선전송'),('SD·USB','저장·통신'),('LED·PWR-HOLD','제어')]},
 'gnss':{'in':[('V30/V30B','전원'),('RF_IN','정제된 위성신호'),('GNSS-RST#','두뇌 리셋')],'core':'NEO-F10N (U13)','out':[('UART-GTX/GRX','위치→두뇌'),('TIMEPULSE','1초 정밀신호')]},
 'gnssfe':{'in':[('안테나 신호','약한 위성신호'),('V30-RF','전원')],'core':'LNA+필터','out':[('RF_IN','증폭·정제→수신기')]},
 'gnssant':{'in':[('위성 전파','하늘')],'core':'패치 안테나 (ANT1)','out':[('RF','→프론트엔드')]},
 'imu':{'in':[('V30/V33','전원'),('SPI (CLK/MOSI/CS)','두뇌 명령')],'core':'LSM6DSOX (U15)','out':[('SPI-MISO','측정 데이터'),('INT-IMU#','움직임 알림')]},
 'mag':{'in':[('V30','전원'),('IMU 보조 I²C','IMU 경유')],'core':'LIS2MDL (U16)','out':[('INT/DRDY','데이터 준비')]},
 'sd':{'in':[('V33','전원'),('SD버스 (D0~3/CLK/CMD)','두뇌 데이터')],'core':'microSD + EMIF','out':[('파일 저장','비휘발)')]},
 'rtc':{'in':[('V33/V30B','전원'),('VBKP','백업 전원'),('I²C (SCL/SDA)','두뇌 시간동기')],'core':'RV-3028 (U8)','out':[('INT-RTC#','두뇌 알람'),('CLKOUT','클럭')]},
 'gauge':{'in':[('VBAT','배터리 전압'),('I²C','두뇌 통신')],'core':'MAX17262 (U2)','out':[('잔량·전압·온도','→두뇌')]},
 'charger':{'in':[('VUSB','도크 전기'),('ISET/VSET','충전조건'),('TS/NTC','배터리 온도')],'core':'BQ25176 (U1)','out':[('SYS','시스템 전원'),('BATT-STAT','충전 상태')]},
 'battery':{'in':[('충전 전류','충전IC에서')],'core':'Li-Po (CON1)','out':[('VBAT','→충전·게이지·전원부')]},
 'power':{'in':[('SYS','충전IC 출력'),('EN (PWR/SENS-EN)','두뇌 켬')],'core':'벅 + LDO','out':[('3.3V','메인'),('3.0V','센서'),('3.0V 백업','RTC 등')]},
 'sw':{'in':[('SYS','전원'),('버튼 누름','사람')],'core':'스위치 (SW1)','out':[('PWR-DECT','두뇌에 알림')]},
 'pogo':{'in':[('도크 접점','충전·데이터')],'core':'POGO 5핀 (CON2)','out':[('VUSB','충전 전기'),('USB-DM/DP','데이터')]},
 'led':{'in':[('V33/VUSB','전원'),('MCU LED 신호','제어'),('CHRGLED-CTRL','충전LED')],'core':'RGB LED + 스위치','out':[('빛','상태 표시')]},
}
# PARTS: [(refdes, value, 역할 — "무엇 + 왜 + 무엇과")]
# ===== 블록별 부품 — 스키매틱에서 "전 부품" 자동 추출(누락 없음) + 핵심부품 풍부한 설명 오버레이 =====
# 개별 RefDes → "무엇+왜+관계" 큐레이션 설명. (parts_clbx.json = 크롭에 보이는 모든 부품)
_RICH={
 'U3':'메인 컨트롤러(두뇌). 각 IO핀으로 센서·저장·통신을 지휘하고, WiFi/BT 무선을 내장해 데이터를 밖으로 보냄.',
 'R22':'GNSS 리셋(GNSS-RST#)선 풀업 — 평소 High로 유지해 GPS가 멋대로 리셋 안 되게.',
 'U13':'GNSS 수신기. RF_IN으로 받은 위성 신호를 계산해 UART로 위치를, TIMEPULSE로 정밀 1초를 두뇌에 보냄.',
 'C33':'RF 전원(VCC_RF) 전용 안정 커패시터 — 민감한 고주파부 전원을 따로 챙김.',
 'U14':'LNA(저잡음 증폭기) — 안테나가 받은 아주 약한 위성 신호를 맨 먼저 키움. 없으면 신호가 잡음에 묻혀 못 씀.',
 'U21':'RF 필터(NJG1186) — GNSS 주파수(L1/L5)만 통과시키고 휴대폰·WiFi 등 다른 전파는 차단.',
 'U11':'RF 필터(NJG1159) — GNSS 대역만 통과.', 'U4':'SAW 필터(B9973) — 원하는 대역만 더 날카롭게 골라냄.',
 'U22':'RF 스위치/필터(MC2601).',
 'ANT1':'L1/L5 패치 안테나 — 하늘의 위성 전파를 전기 신호로.',
 'D12':'안테나 입력 ESD 보호 다이오드 — 정전기가 안테나로 들어와 회로를 태우는 것 방지.',
 'U15':'6축 관성센서(가속도+자이로). 움직임을 재서 SPI로 두뇌에 보냄. ★ 아래 U16(지자기)도 이 칩이 대신 읽어 함께 전달.',
 'U16':'3축 지자기센서(방향/헤딩). ★ 두뇌가 직접 안 읽음 — U15의 보조 포트(SDx/SCx)에 매달려 IMU가 대신 읽어 두뇌로 넘김(센서-허브 구조).',
 'L5':'VDD 전원선 페라이트 비드. ★ C26·C29와 짝을 이뤄 "전원 필터"를 만들어, 무선·벅 노이즈가 센서 전원에 새어드는 걸 막음.',
 'R32':'전원 라인 직렬 저항 — L5와 함께 노이즈를 한 번 더 눌러줌.',
 'C38':'내부 기준전압(C1) 안정 커패시터 — 센서가 흔들림 없이 기준을 잡게.',
 'U7':'microSD 소켓 — SD카드 슬롯, 측정 데이터를 파일로 저장.',
 'U6':'SD 6선 ESD/EMI 필터 — 카드를 뺐다 꽂을 때의 정전기와 고속 신호 노이즈를 막아 두뇌 IO를 보호.',
 'U8':'실시간 시계칩 — 시간을 계속 세고, 정해진 시각에 INT로 두뇌를 깨움. GPS 시각으로 보정.',
 'R17':'메인 전원(V33)에서 백업핀(VBKP)으로 전기를 흘려 백업 커패시터를 충전하는 저항.',
 'C15':'백업 커패시터 — ★ 메인 전원이 끊긴 순간에도 이 커패시터에 모아둔 전기로 시계를 잠깐 살려 시간을 안 잃음.',
 'R16':'INT#(알람) 풀업 — 알람선을 평소 High로 두고, 알람 때 Low로 떨궈 두뇌에 알림.',
 'R18':'EVI/PD 풀업 저항.',
 'U2':'연료게이지 — 배터리 전압·전류를 재고 내부 모델로 잔량(%)을 계산해 I²C로 두뇌에 알림.',
 'C6':'내부 기준 커패시터 — 정밀 측정 기준.',
 'U1':'리니어 충전 IC — VUSB(도크)로 배터리를 충전하고, SYS로 시스템에 전원 공급. ★ ISET 저항으로 충전전류(≈400mA), VSET 저항으로 만충전압(4.4V) 결정.',
 'NR1':'배터리에 붙은 온도센서(NTC) — 너무 뜨겁/차가우면 충전을 멈춤(JEITA 안전).',
 'R1':'STAT(충전상태) 신호 풀업.',
 'CON1':'배터리 커넥터 — 실제 셀을 보드에 연결.',
 'C23':'VBAT 안정 커패시터 — ★ 무선 송신처럼 순간 큰 전류를 당길 때 배터리 전압이 훅 처지는 걸 완화.',
 'U5':'벅 컨버터 — SYS를 3.3V로 "효율 좋게"(열 적게) 낮춤. 스위칭 방식이라 손실이 작음.',
 'L3':'벅 인덕터(코일) — ★ 벅이 전압을 낮추는 원리의 핵심. 에너지를 잠깐 저장했다 내보내며 3.3V를 만듦(없으면 벅 동작 불가).',
 'U9':'LDO(NCP163) — 3.0V를 "아주 깨끗하게(노이즈 적게)" 만들어 예민한 센서에 공급(벅보다 조용함).',
 'U10':'LDO(TPS7A0230) — 백업/보조 3.0V.', 'U20':'LDO(TPS7A0230) — 백업/보조 3.0V.',
 'SW1':'전원 버튼 — 누르면 PWR-DECT 신호로 두뇌에 알림.',
 'R12':'PWR-DECT 풀다운 — 안 눌렀을 땐 기본 Low로 확실히 고정(오인식 방지).',
 'C10':'디바운스 커패시터 — ★ 버튼 누를 때 생기는 "떨림(채터)"을 매끄럽게 눌러 한 번의 누름으로 인식.',
 'CON2':'도크 접점 포고핀 — 도크에 올리면 충전·USB·전원제어가 이 핀으로 오감.',
 'D7':'보호/신호 다이오드.',
 'D1':'RGB LED — 빨강·초록·파랑을 조합해 다양한 상태색 표시.', 'D2':'RGB LED — 상태색 표시.',
 'MP1':'LED 구동 MOSFET(스위치) — 두뇌 신호로 여러 LED를 한꺼번에 켜고 끔(IO 하나로 큰 전류 제어).',
 'MP2':'LED 구동 MOSFET(스위치).',
 'U12':'충전 LED 전환 아날로그 스위치 — 충전상태 신호(BATT-STAT)에 따라 LED 경로를 자동으로 바꿈.',
}
# 블록별 기본 역할(접두별) — 개별 큐레이션이 없는 부품에 붙이는 문맥형 설명
_BROLE={
 'mcu':{'C':'칩 옆 디커플링 커패시터 — 두뇌가 순간 전류를 당길 때 전원이 처지지 않게 받쳐줌','R':'부팅·IO를 기본값으로 잡는 풀업/설정 저항','L':'통신선 고주파 노이즈를 막는 페라이트 비드'},
 'gnss':{'C':'VCC 전원 디커플링 — 위성 신호 계산용 전원을 깨끗이','R':'신호 풀업/직렬','L':'정합/필터'},
 'gnssfe':{'C':'RF 결합 커패시터 — 신호(AC)는 통과, 전원(DC)은 차단해 단을 분리','L':'임피던스 정합용 코일 — 단계마다 신호 손실을 최소화','R':'RF 바이어스/풀업','D':'ESD 보호'},
 'gnssant':{'C':'RF 결합 커패시터','D':'ESD 보호 다이오드'},
 'imu':{'C':'전원(VDD·VDDIO) 옆 디커플링 — 순간 변동을 흡수해 센서값이 흔들리지 않게','R':'SPI·보조 신호 풀업/직렬 저항 (NC=자리만, 미실장 옵션)','L':'전원 페라이트 비드'},
 'mag':{'C':'전원(VDD·VDDIO) 디커플링','R':'보조 I²C 풀업 — 통신선을 기본 High로'},
 'sd':{'C':'전원 디커플링','R':'SD 라인 풀업','U':'SD 소켓/필터'},
 'rtc':{'C':'디커플링','R':'풀업'},
 'gauge':{'C':'전원 디커플링 — 정밀 측정을 위해 전원 안정','R':'I²C 통신선 풀업'},
 'charger':{'C':'입·출력 커패시터 — 충전 전류 변동을 흡수해 전압 안정','R':'충전전류/전압 설정·STAT 저항','D':'보호 다이오드'},
 'battery':{'C':'VBAT 안정 커패시터','D':'ESD/보호 다이오드'},
 'power':{'C':'입·출력 커패시터 — 전압 안정','R':'출력 전압을 정하는 피드백/EN 저항','L':'벅 인덕터','D':'보호 다이오드'},
 'sw':{'C':'디바운스 커패시터','R':'풀다운 저항'},
 'pogo':{'C':'VUSB 안정 커패시터','D':'USB 라인 ESD 보호 — 정전기가 두뇌 USB핀을 태우는 것 방지','J':'테스트/커넥터 핀'},
 'led':{'C':'디커플링','R':'LED 전류제한 저항 — ★ 그대로 전원을 대면 과전류로 타버림, 밝기(전류)를 안전하게 제한','D':'개별 색 LED — 위치별 상태 표시등','MP':'LED 구동 MOSFET'},
}
import re as _re
_auto=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'parts.json'),encoding='utf-8'))
def _pref(r):
    m=_re.match(r'^([A-Z]+)',r); return m.group(1) if m else r
def _rows(bid):
    out=[]
    for refdes,val,role in _auto.get(bid,[]):
        desc=_RICH.get(refdes)
        if not desc:
            base=_BROLE.get(bid,{}).get(_pref(refdes)) or role
            desc=base if not val else f'{base} ({val})'
        out.append((refdes,val,desc))
    return out
PARTS={bid:_rows(bid) for bid in _auto}

# ===== 부품 주요 스펙 (데이터시트 요약·대표값) =====
SPEC={
 'mcu':[('코어','Xtensa LX7 듀얼 240MHz'),('메모리','Flash 4MB · PSRAM 2MB'),('무선','WiFi b/g/n · BT 5 LE'),('동작전원','3.3V'),('I/O','GPIO 약 36개')],
 'gnss':[('밴드','L1 + L5 멀티밴드'),('측위정확도','수 m (보정 시 cm급)'),('인터페이스','UART · I²C · USB'),('동작전원','약 3.0V'),('Cold TTFF','약 30초')],
 'gnssfe':[('핵심부품','BGA525N6 LNA'),('이득(Gain)','약 19 dB'),('잡음지수(NF)','약 0.8 dB'),('대역','GNSS L1/L5')],
 'gnssant':[('형식','패치(Patch) 안테나'),('크기','25 × 25 mm'),('대역','L1 + L5')],
 'imu':[('종류','6축 (가속도+자이로)'),('가속도 범위','±2/4/8/16 g'),('자이로 범위','±125~2000 dps'),('ODR','최대 6.66 kHz'),('인터페이스','SPI · I²C'),('동작전원','1.71~3.6V'),('특징','FIFO · ML 코어 내장')],
 'mag':[('종류','3축 지자기'),('측정범위','±50 gauss'),('분해능','16-bit'),('ODR','최대 100 Hz'),('인터페이스','I²C · SPI'),('동작전원','1.71~3.6V')],
 'sd':[('용량','32 GB'),('인터페이스','SDIO 4-bit'),('용도','측정 데이터 로깅')],
 'rtc':[('정확도','±1 ppm'),('인터페이스','I²C'),('동작전원','1.1~5.5V'),('소비전류','약 45 nA'),('특징','타임스탬프·백업')],
 'gauge':[('방식','ModelGauge m5'),('측정','전압·전류·잔량(SOC)·온도'),('인터페이스','I²C'),('동작전원','2.5~4.9V')],
 'charger':[('충전전압','4.2 / 4.35V'),('충전전류','약 400 mA (프로그램)'),('입력','VUSB (도크)'),('보호','JEITA NTC 온도')],
 'battery':[('타입','리튬폴리머(Li-Po)'),('용량','500 mAh · 1.9 Wh'),('전압','3.8V (최대 4.35V)')],
 'power':[('벅(Buck)','TPS62291 → 3.3V, 1A, 2.25MHz'),('LDO','3.0V 센서용 · 3.0V 백업'),('입력','SYS')],
 'pogo':[('핀수','5핀 포고(Pogo)'),('기능','충전 · USB · 전원제어'),('상대','도크(Dock)')],
 'sw':[('타입','택트 스위치'),('기능','전원 On/Off (길게 눌러 제어)')],
 'led':[('구성','RGB 상태 LED + 충전 LED'),('제어','MCU GPIO + 아날로그 스위치'),('표시','GNSS·전원·충전 상태')],
}

# ===== 블록(노드) — 스키매틱에서 도출 =====
# id: (이름, 칩, 그룹, 한줄역할, x, y, 근거시트)
B={
 'mcu': ('두뇌 (MCU)','ESP32-S3-MINI-1','mcu','전체를 지휘하고 데이터를 처리·저장하며 WiFi/BT로 무선 전송',500,375,'pwr'),
 'gnss':('측위 수신기 (GPS)','NEO-F10N','gnss','위성 신호로 현재 위치를 계산 (GPS·갈릴레오 등 L1/L5)',255,370,'gnss'),
 'gnssfe':('GNSS 프론트엔드','BGA525N6 LNA + SAW','gnss','약한 위성 신호를 증폭·정제해 수신기로',110,270,'gnss'),
 'gnssant':('GNSS 안테나','패치 25×25mm','gnss','하늘의 위성 신호를 받아들임',110,140,'gnss'),
 'imu':('관성센서 (IMU)','LSM6DSOX (6축)','sensor','가속도·각속도로 움직임을 측정',420,120,'gnss'),
 'mag':('지자기센서','LIS2MDL (3축)','sensor','자기장으로 방향(헤딩)을 측정',610,120,'gnss'),
 'sd':('저장 (SD)','microSD 32GB','storage','측정 데이터를 파일로 기록',825,180,'pwr'),
 'rtc':('실시간시계 (RTC)','RV-3028-C7','timing','전원이 꺼져도 시간 유지, GPS로 보정',860,325,'pwr'),
 'gauge':('배터리 게이지','MAX17262','power','배터리 잔량(%)을 정밀 계측',860,480,'pwr'),
 'charger':('충전 IC','BQ25176J','power','배터리를 충전 (4.3V·400mA, JEITA)',345,650,'pwr'),
 'battery':('배터리','GRP693027 · 500mAh','power','전원원 (Li-Po 3.8V, 1.9Wh)',160,600,'pwr'),
 'power':('전원 변환','TPS62291 벅 + LDO','power','3.3V·3.0V를 만들어 각 부품에 분배',525,615,'pwr'),
 'pogo':('도크 커넥터','POGO 5핀','iface','도크에서 충전·USB·전원제어',720,610,'pwr'),
 'sw':('전원 스위치','DJT-1114S','iface','전원 On/Off',440,722,'pwr'),
 'led':('상태 표시등','RGB + 충전 LED','led','불빛으로 상태·충전을 표시',285,505,'gnss'),
}
# ===== 관계(엣지) — 스키매틱의 넷/버스 =====
# (a,b,bus,라벨)
E=[
 ('mcu','rtc','I2C','I²C','bi'),('mcu','gauge','I2C','I²C','bi'),
 ('mcu','imu','SPI','SPI','bi'),('imu','mag','SPI','보조','bi'),
 ('mcu','gnss','UART','UART','bi'),
 ('mcu','sd','SDIO','SDIO','bi'),
 ('mcu','pogo','USB','USB','bi'),
 ('gnssant','gnssfe','RF','RF','ab'),('gnssfe','gnss','RF','RF','ab'),
 ('battery','charger','PWR','','bi'),('pogo','charger','PWR','VUSB','ab'),('charger','power','PWR','SYS','ab'),
 ('power','mcu','PWR','3.3V','ab'),('power','imu','PWR','3.0V','ab'),('power','gnss','PWR','3.0V','ab'),
 ('power','rtc','PWR','백업','ab'),('gauge','battery','PWR','계측','ba'),
 ('mcu','led','CTRL','','ab'),('mcu','sw','CTRL','','bi'),('mcu','charger','CTRL','상태','ba'),
]
GC={'mcu':'#e0567a','gnss':'#12a3b4','sensor':'#7c5cff','storage':'#2e9e68','timing':'#e08a1e','power':'#d99320','iface':'#e0703a','led':'#c9a91e'}
GLAB={'mcu':'두뇌','gnss':'측위','sensor':'감지','storage':'저장','timing':'시계','power':'전원','iface':'인터페이스','led':'표시'}
BC={'I2C':'#3b82d6','SPI':'#7c5cff','UART':'#2e9e68','SDIO':'#e0703a','RF':'#12a3b4','USB':'#d24a68','PWR':'#e0a020','CTRL':'#9aa7b2'}
BLAB={'I2C':'I²C 버스','SPI':'SPI 버스','UART':'UART','SDIO':'SD 버스','RF':'RF 체인','USB':'USB','PWR':'전원','CTRL':'제어신호'}

# ===== 데이터시트 원본(Google Drive, HTS 공용 폴더) — (라벨, Drive file id) =====
# 요약이 부족할 때 원문 PDF로 바로 이동. id는 실제 Drive 파일에서 확인함.
# 빈 목록 = 공용 드라이브에 원본 미등록(DRBFM 관점에서 '자료 공백'으로 표시).
DS={
 'mcu':[('ESP32-S3-MINI-1 데이터시트','1hfSMGXRlSmtfA-jscKdJoHicqjdgu5Ru')],
 'gnss':[('NEO-F10N 데이터시트','19cy6R81dZbFei9sf_Y7RIalQF8tBhk9n'),('NEO-F10N 통합 매뉴얼','1jSWQ4LEh9jGCiAkjvTIKDiQFMlRFjHoB')],
 'gnssfe':[('BGA525N6 LNA','1zus0wM7ychC4JLHFUhI8CKQPDUiM-ppc'),('NJG1186 필터','1w75yddDZA8HTWczOrNwq1Q4LF2Ngfgya'),('NJG1159 필터','19KXmFjIKd5-iVEtwgw6bY-BaXOkAIVmQ'),('B39162B9973 SAW','1yBaq3gxbVqwCyJu-PLSyw4Nq9EfSa0ko')],
 'gnssant':[('2JCP2583101 안테나(L1/L5)','1kji4Nsy678oKUYWqHrSzOE2FcsR2bAhn')],
 'imu':[],
 'mag':[],
 'sd':[],
 'rtc':[('RV-3028-C7 데이터시트','1UKWTOAWL3M2xE9Gd-vVnN_rhqqzQi6NW')],
 'gauge':[('MAX17262 데이터시트','1RmnkI9V6y1ACgnWeaouNZR_5swXvD0eo')],
 'charger':[('BQ25176J 데이터시트','1t5XIiYilLNXcHgbdCUygpKNbtvi2APAq')],
 'battery':[('GRP693027 셀 스펙','1sa8ZI55UZFEfQPkUBPZ5Li4ZYTe3JD-O')],
 'power':[('TPS62291 벅','1er8Kd11cnxrq_xY7PJVGuQ4UTraH_oOT'),('TPS7A0230 LDO','1v_6B81pyeHjjtUpLOpFc3qrvZ4tH_fW8')],
 'pogo':[('포고 커넥터(BP259521-05254)','1ozMzM5btxzYQkRllJzX2cz4UCnWI8zrj')],
 'sw':[],
 'led':[('ASMB-KTF0 RGB LED','1olDXJHfAITYHvaYfAFv3tJ1Tet-4XJjs'),('TS5A3159 스위치','1dw0joxXILc4XwPTwrOelz1C1iSdaRFFM')],
}

# ===== 부품(RefDes/값)별 데이터시트 매칭 — 부품표 각 행에 원본 링크 =====
# toks: 부품 값/RefDes에 이 토큰이 들어 있으면 그 부품의 데이터시트로 간주(정규화 후 부분일치).
# id=None → 실물 IC지만 공용 드라이브에 원본 미등록(행에 '원본 없음'으로 표시).
PART_DS=[
 (['BGA525N6'],[('원본','1zus0wM7ychC4JLHFUhI8CKQPDUiM-ppc')]),
 (['NJG1186'],[('NJG1186','1w75yddDZA8HTWczOrNwq1Q4LF2Ngfgya')]),
 (['NJG1159','/1159'],[('NJG1159','19KXmFjIKd5-iVEtwgw6bY-BaXOkAIVmQ')]),
 (['B9973','B39162'],[('원본','1yBaq3gxbVqwCyJu-PLSyw4Nq9EfSa0ko')]),
 (['MC2601'],None),
 (['ESP32'],[('원본','1hfSMGXRlSmtfA-jscKdJoHicqjdgu5Ru')]),
 (['NEO-F10N','NEOF10N'],[('데이터시트','19cy6R81dZbFei9sf_Y7RIalQF8tBhk9n'),('통합매뉴얼','1jSWQ4LEh9jGCiAkjvTIKDiQFMlRFjHoB')]),
 (['LSM6DSOX'],None),
 (['LIS2MDL'],None),
 (['RV-3028','RV3028'],[('원본','1UKWTOAWL3M2xE9Gd-vVnN_rhqqzQi6NW')]),
 (['MAX17262'],[('원본','1RmnkI9V6y1ACgnWeaouNZR_5swXvD0eo')]),
 (['BQ25176'],[('원본','1t5XIiYilLNXcHgbdCUygpKNbtvi2APAq')]),
 (['GRP693027','693027'],[('원본','1sa8ZI55UZFEfQPkUBPZ5Li4ZYTe3JD-O')]),
 (['TPS62291'],[('원본','1er8Kd11cnxrq_xY7PJVGuQ4UTraH_oOT')]),
 (['TPS7A02'],[('원본','1v_6B81pyeHjjtUpLOpFc3qrvZ4tH_fW8')]),
 (['2JCP2583','2JCP2582'],[('원본','1kji4Nsy678oKUYWqHrSzOE2FcsR2bAhn')]),
 (['ASMB-KTF','ASMB'],[('원본','1olDXJHfAITYHvaYfAFv3tJ1Tet-4XJjs')]),
 (['TS5A3159'],[('원본','1dw0joxXILc4XwPTwrOelz1C1iSdaRFFM')]),
 (['BLM15HD102','BLM15'],[('원본','1QQBTgF3rGXBDxDHBc9adRbafBhN_2Jjn')]),
 (['LQP03HQ'],[('원본','10P2ZhzoJcJaH6jS_0Esm1oIGJKeMw3NK')]),
 (['1N4148'],[('원본','1AxXExMSbPiDIo6AIsWm8FEjHCEuMj8jW')]),
 (['TPD2E2U06'],[('원본','1MCDSfoUn10UuxaILjwYGKYVoRfK4QazI')]),
 (['EMIF06'],[('원본','1WWQzXSTvMo8zizr_14x18hj5ZLDDK6RC')]),
]

# ===== 부품별 스펙 요약(= DRBFM DB의 씨앗) — 데이터시트에서 추출 =====
# absmax=절대최대정격(넘으면 파손·DRBFM 1순위), op=동작조건, key=핵심성능,
# applied=(회로 적용조건 → 정격 대비 마진 판정). 이 구조가 그대로 Supabase로 간다.
# part_spec.json 에서 로드(각 부품 데이터시트에서 추출·구조화).
_PSJ=json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'part_spec.json'),encoding='utf-8'))
PART_SPEC=[(e['toks'], e['spec']) for e in _PSJ]

# ===== 시스템 워크플로우(엔드-투-엔드 흐름) — 블록이 관여하는 시나리오 =====
# (이름, [블록 id 순서(흐름 방향)], 한줄 설명). L2 관계를 "흐름"으로 표현.
WF=[
 ('측위 (위치 획득)', ['gnssant','gnssfe','gnss','mcu'], '안테나가 위성신호를 받아 → LNA로 증폭·정제 → 수신기가 위치 계산 → MCU로 전송'),
 ('정밀 시각 (1PPS)', ['gnss','mcu'], '수신기가 위성 기준 1초 펄스(TIMEPULSE)를 MCU에 줘 정밀 시각 동기'),
 ('움직임·방향 감지', ['mag','imu','mcu'], '지자기(방향)는 IMU 보조포트에 물려 IMU가 함께 읽어 MCU로 넘김(센서-허브)'),
 ('데이터 로깅', ['mcu','sd'], 'MCU가 측정 데이터를 SD 카드에 파일로 기록'),
 ('전원 분배', ['battery','charger','power','mcu'], '배터리 → 충전IC(SYS) → 전원변환(벅/LDO) → 3.3/3.0V로 각 부품에 분배'),
 ('충전', ['pogo','charger','battery'], '도크에서 포고로 들어온 전기로 충전IC가 배터리를 충전'),
 ('배터리 잔량 측정', ['gauge','battery'], '게이지가 배터리 전압·전류를 측정해 잔량(%)을 I²C로 MCU에 보고'),
 ('시간 유지·보정', ['rtc','mcu'], 'RTC가 전원 꺼져도 시간 유지, MCU가 I²C로 읽고 GPS로 보정'),
 ('상태 표시', ['mcu','led'], 'MCU가 GPIO·아날로그 스위치로 RGB·충전 LED를 제어해 상태 표시'),
 ('도크·USB 통신', ['pogo','mcu'], '도크의 포고 커넥터를 통해 MCU가 USB로 통신·펌웨어'),
 ('전원 On/Off·충전제어', ['sw','mcu','charger'], '전원 스위치로 MCU 제어, MCU가 충전 상태 신호를 주고받음'),
]

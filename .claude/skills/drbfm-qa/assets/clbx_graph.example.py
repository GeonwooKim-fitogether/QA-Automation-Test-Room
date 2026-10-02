# CLBX-6A/6B 관계도 데이터(hardware-map hw_data에서 추출) — 블록/연결/색
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

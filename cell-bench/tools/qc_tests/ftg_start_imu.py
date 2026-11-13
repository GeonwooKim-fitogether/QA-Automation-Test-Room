"""폴더별로 셀 기록의 START 메시지(기종 · 판 · 펌웨어 · IMU 보정값)와 IMU 메시지 수를 한 줄씩."""
import sys, glob, os
sys.path.insert(0, r'C:\Dev\ohcoach-cell-tools\cores\fitogether_data_parser')
from fitogether_data_parser.ftg.parser import FtgParser
from fitogether_data_parser.ftg.messages.start_message import StartMessage
from collections import Counter
for d in sys.argv[1:]:
    print('==', os.path.basename(d))
    for f in sorted(glob.glob(os.path.join(d, 'ftg_*.bin'))):
        data = open(f, 'rb').read()
        p = FtgParser('CLBY-4B-00000_0.99_0_1761724328_0.ftg', filecontents=data)
        types = Counter(); starts = []
        for t, cd, pl, crc in p.raw_parser(memoryview(data)):
            types[t] += 1
            if t == 0x04 and len(starts) < 3:
                try:
                    starts.append(StartMessage.create(bytes(pl[:StartMessage.message_length]), 'V2'))
                except Exception as e:
                    starts.append(e)
        s = starts[0] if starts else None
        desc = (f"{s.device_model}-{s.device_version} fw {s.firmware_version} cal acc {tuple(round(x,2) for x in s.imu_accel_calibration)} gyr {tuple(round(x,2) for x in s.imu_gyro_calibration)}"
                if isinstance(s, StartMessage) else f"START {s!r}")
        print(f"{os.path.basename(f)[4:9]} {len(data)/2**20:5.1f}MB START {types[4]:>3} GPS {types[1]:>7} IMU0E {types[0x0e]:>6} IMU0F {types[0x0f]:>7} | {desc}", flush=True)

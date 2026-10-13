@echo off
rem Flash hr-sim to one ESP32-C3. Usage: flash.cmd [COMx]  (no port = esptool auto-detects)
if "%~1"=="" (set PORTARG=) else (set PORTARG=-p %~1)
python -m esptool --chip esp32c3 %PORTARG% -b 460800 --before default-reset --after hard-reset write-flash --flash-mode dio --flash-freq 80m --flash-size 4MB 0x0 "%~dp0bin\bootloader.bin" 0x8000 "%~dp0bin\partition-table.bin" 0x10000 "%~dp0bin\hr-sim.bin"

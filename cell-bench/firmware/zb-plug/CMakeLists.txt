# zb-plug — ESP32-C6 를 지그비 코디네이터로 만들어 아카라 스마트 플러그 하나를 USB 시리얼 명령으로 켜고 끈다.
# 빌드: ESP-IDF v5.3.2 (esp-zigbee-lib · esp-zboss-lib 1.6.x). 자세한 것은 README.md.
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(zb-plug)

/*
 * hr-sim — ESP32-C3 를 표준 BLE 심박대처럼 보이게 하는 펌웨어.
 *
 * 하는 일
 *   1. 심박 서비스(0x180D)를 광고한다. 광고 이름은 "HRSIM-" + 보드 기본 MAC 끝 4자리(16진 대문자),
 *      예: 기본 MAC 34:85:18:12:A1:B2 → "HRSIM-A1B2". 셀에 0x20 으로 쓰는 이름이 이것이다
 *      (cellbench/protocol.py ble_id_frame — UTF-8 24바이트까지, 이 이름은 10바이트).
 *   2. 심박 측정값(0x2A37)을 1초마다 알린다(notify). 값은 60~160 bpm 사이를 5분 주기 사인파로
 *      천천히 오간다(초당 최대 약 1 bpm 변화). 보드마다 MAC 으로 위상을 달리해 25개가 같은 값을 내지 않는다.
 *   3. USB-Serial/JTAG 로 5초마다 "HRSIM name=... bpm=... conns=..." 한 줄을 낸다 — 굽는 스크립트가
 *      이 줄로 부팅을 확인하고, 사람은 시리얼 모니터로 이름을 읽는다.
 *
 * 이름에 쓰는 MAC 은 esptool 이 "MAC:" 로 출력하는 기본(공장) MAC 이다. BLE 주소는 ESP32-C3 규약상
 * 기본 MAC + 2 라서 휴대폰 스캐너에 보이는 주소 끝자리와는 다를 수 있다 — 이름으로 찾으면 된다.
 */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs_flash.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#define UUID_HR_SERVICE      0x180D
#define UUID_HR_MEASUREMENT  0x2A37
#define UUID_BODY_LOCATION   0x2A38
#define APPEARANCE_HR_SENSOR 0x0340  /* Generic Heart Rate Sensor */

/* 0x2A37 플래그: bit0=0 심박 8비트, bit1·2=11 센서 접촉 지원·접촉됨 */
#define HRM_FLAGS 0x06

#define BPM_MID      110.0f
#define BPM_SWING    50.0f      /* 110 ± 50 → 60~160 */
#define BPM_PERIOD_S 300.0f     /* 한 바퀴 5분 */
#define STATUS_EVERY 5          /* 상태 줄 간격(초) */

static const char *TAG = "hr-sim";

static char s_name[16];
static float s_phase;           /* 0~1, 보드마다 다른 위상 */
static uint8_t s_own_addr_type;
static uint16_t s_hrm_handle;
static volatile uint8_t s_bpm = 110;
static volatile bool s_synced;
static volatile int s_conns;

static int gap_event(struct ble_gap_event *event, void *arg);

static int hrs_access(uint16_t conn_handle, uint16_t attr_handle,
                      struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    uint16_t uuid = ble_uuid_u16(ctxt->chr->uuid);
    if (uuid == UUID_HR_MEASUREMENT) {
        uint8_t v[2] = {HRM_FLAGS, s_bpm};
        return os_mbuf_append(ctxt->om, v, sizeof v) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    if (uuid == UUID_BODY_LOCATION) {
        uint8_t chest = 1;
        return os_mbuf_append(ctxt->om, &chest, 1) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
    }
    return BLE_ATT_ERR_UNLIKELY;
}

static const struct ble_gatt_svc_def s_services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = BLE_UUID16_DECLARE(UUID_HR_SERVICE),
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = BLE_UUID16_DECLARE(UUID_HR_MEASUREMENT),
                .access_cb = hrs_access,
                .val_handle = &s_hrm_handle,
                .flags = BLE_GATT_CHR_F_NOTIFY,
            },
            {
                .uuid = BLE_UUID16_DECLARE(UUID_BODY_LOCATION),
                .access_cb = hrs_access,
                .flags = BLE_GATT_CHR_F_READ,
            },
            {0},
        },
    },
    {0},
};

static void advertise(void)
{
    if (ble_gap_adv_active() || s_conns >= MYNEWT_VAL(BLE_MAX_CONNECTIONS)) {
        return;
    }
    /* 광고 31바이트 안: 플래그 3 + 서비스 UUID 4 + 외형 4 + 이름 2+10 = 23 */
    struct ble_hs_adv_fields f = {0};
    ble_uuid16_t hrs = BLE_UUID16_INIT(UUID_HR_SERVICE);
    f.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    f.uuids16 = &hrs;
    f.num_uuids16 = 1;
    f.uuids16_is_complete = 1;
    f.appearance = APPEARANCE_HR_SENSOR;
    f.appearance_is_present = 1;
    f.name = (uint8_t *)s_name;
    f.name_len = strlen(s_name);
    f.name_is_complete = 1;
    int rc = ble_gap_adv_set_fields(&f);
    if (rc != 0) {
        ESP_LOGE(TAG, "adv_set_fields rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params p = {0};
    p.conn_mode = BLE_GAP_CONN_MODE_UND;
    p.disc_mode = BLE_GAP_DISC_MODE_GEN;
    p.itvl_min = BLE_GAP_ADV_ITVL_MS(100);
    p.itvl_max = BLE_GAP_ADV_ITVL_MS(150);
    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER, &p, gap_event, NULL);
    if (rc != 0 && rc != BLE_HS_EALREADY) {
        ESP_LOGE(TAG, "adv_start rc=%d", rc);
    }
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            s_conns++;
            printf("HRSIM connect handle=%d conns=%d\n", event->connect.conn_handle, s_conns);
        }
        advertise();  /* 실패했으면 다시, 성공했어도 자리가 남으면 계속 광고 */
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        if (s_conns > 0) {
            s_conns--;
        }
        printf("HRSIM disconnect reason=0x%x conns=%d\n", event->disconnect.reason, s_conns);
        advertise();
        return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        advertise();
        return 0;
    case BLE_GAP_EVENT_SUBSCRIBE:
        if (event->subscribe.attr_handle == s_hrm_handle) {
            printf("HRSIM subscribe handle=%d notify=%d\n",
                   event->subscribe.conn_handle, event->subscribe.cur_notify);
        }
        return 0;
    default:
        return 0;
    }
}

static void on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc == 0) {
        rc = ble_hs_id_infer_auto(0, &s_own_addr_type);
    }
    if (rc != 0) {
        ESP_LOGE(TAG, "BLE 주소를 정하지 못함 rc=%d", rc);
        return;
    }
    uint8_t a[6] = {0};
    ble_hs_id_copy_addr(s_own_addr_type, a, NULL);
    printf("HRSIM ble_addr=%02X:%02X:%02X:%02X:%02X:%02X\n", a[5], a[4], a[3], a[2], a[1], a[0]);
    s_synced = true;
    advertise();
}

static void on_reset(int reason)
{
    s_synced = false;
    ESP_LOGW(TAG, "BLE 스택 리셋 reason=%d", reason);
}

static void host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void hr_task(void *param)
{
    const float two_pi = 6.2831853f;
    TickType_t last = xTaskGetTickCount();
    for (unsigned tick = 0;; tick++) {
        float t = esp_timer_get_time() / 1e6f;
        float bpm = BPM_MID + BPM_SWING * sinf(two_pi * (t / BPM_PERIOD_S + s_phase));
        s_bpm = (uint8_t)lroundf(bpm);
        if (s_synced) {
            ble_gatts_chr_updated(s_hrm_handle);  /* 구독한 연결 모두에 알림 */
        }
        if (tick % STATUS_EVERY == 0) {
            printf("HRSIM name=%s bpm=%u conns=%d\n", s_name, s_bpm, s_conns);
        }
        vTaskDelayUntil(&last, pdMS_TO_TICKS(1000));
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();  /* BLE 컨트롤러의 PHY 보정값 저장에 필요 */
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_efuse_mac_get_default(mac));
    snprintf(s_name, sizeof s_name, "HRSIM-%02X%02X", mac[4], mac[5]);
    s_phase = mac[5] / 256.0f;
    printf("HRSIM boot name=%s base_mac=%02X:%02X:%02X:%02X:%02X:%02X\n",
           s_name, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    ESP_ERROR_CHECK(nimble_port_init());
    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;

    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_ERROR_CHECK(ble_gatts_count_cfg(s_services) == 0 ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(ble_gatts_add_svcs(s_services) == 0 ? ESP_OK : ESP_FAIL);
    ble_svc_gap_device_name_set(s_name);

    nimble_port_freertos_init(host_task);
    xTaskCreate(hr_task, "hr", 3072, NULL, 5, NULL);
}

/*
 * zb-plug — ESP32-C6 를 지그비 코디네이터로 만들어, 짝지은 스마트 플러그 한 대(아카라 SP-EUC01)를
 * PC 가 USB 시리얼 명령으로 켜고 끄고 전력을 읽게 한다.
 *
 * 왜 이렇게 만들었나
 *   레일 독 충전 플러그(Tapo P110M)가 2026-11-05 펌웨어에서 PC 로 켜고 끌 수 없게 바뀌었다(TPAP 인증).
 *   지그비 플러그는 허브 없이 이 보드 하나가 망을 만들고 직접 명령을 보낸다 — 인터넷 · 계정 · 와이파이가 필요 없다.
 *
 * PC 와 주고받는 말 (한 줄에 하나, 줄 끝 \n)
 *   PC 가 보내는 줄:  <명령> [인자...] [#표지]
 *   보드가 답하는 줄: OK #표지 ... | ERR #표지 <이유> | STATE #표지 on=<0|1|?> w=<W|nan> ...
 *   보드가 혼자 내는 줄: EVT ... (짝짓기 · 보고 · 망 상태), LOG ... (경고 이상 기록)
 *   표지(#로 시작하는 낱말)는 PC 가 매번 새로 붙인다. PC 가 포트를 열지 않은 동안 쌓인 옛 줄과
 *   이번 답을 가르는 데 쓴다.
 *
 *   PING              살아 있나 · 망 정보 · 짝지은 플러그
 *   JOIN [초]         짝짓기 창을 연다(기본 180초). 그동안 플러그 버튼을 길게 누르면 붙는다
 *   CLOSE             짝짓기 창을 닫는다
 *   STATE             켜짐과 전력을 플러그에서 새로 읽어 답한다
 *   ON | OFF          켜거나 끄고, 결과를 STATE 로 답한다
 *   INFO              플러그의 제조사 · 모델 · 펌웨어(기본 클러스터)를 읽는다
 *   DESC              플러그의 엔드포인트와 클러스터 목록을 읽는다(전력 측정을 지원하는지 볼 때)
 *   SETUP             플러그가 켜짐 · 전력 변화를 스스로 보고하게 묶는다(짝지은 직후 자동으로도 한다)
 *   ADOPT             마지막으로 붙은 기기를 '그 플러그'로 정한다
 *   FORGET            짝지은 플러그를 잊는다(망은 그대로)
 *   FACTORY YES       지그비 망을 지우고 새로 만든다(플러그를 다시 짝지어야 한다)
 *   REBOOT            보드를 다시 켠다
 */
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "esp_check.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_zigbee_core.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"

#define FW_NAME "zbplug-1"
#define ZB_CHANNEL 25            /* 와이파이 1~11번(2.4 GHz)과 겹치지 않는 채널. 시험실 와이파이는 2 · 6 · 7 · 11번(2026-11-09) */
#define MY_EP 1                  /* 이 보드의 엔드포인트 */
#define PLUG_EP 1                /* 아카라 플러그의 켜기 · 끄기 엔드포인트 */
#define AI_EP 21                 /* 옛 아카라 플러그가 전력을 아날로그 입력 클러스터로 내는 엔드포인트 */
#define MAX_CHILDREN 10
#define WAIT_MS 2500             /* 플러그 응답 한 번 기다리는 상한 */

#define CL_BASIC 0x0000
#define CL_ONOFF 0x0006
#define CL_AI 0x000C             /* 아날로그 입력 */
#define CL_METER 0x0702
#define CL_EM 0x0B04             /* 전기 측정 */
#define AT_ONOFF 0x0000
#define AT_AI_PV 0x0055          /* 현재 값(float) */
#define AT_EM_W 0x050B           /* 순간 전력(int16) */
#define AT_EM_MUL 0x0604
#define AT_EM_DIV 0x0605


/* ---------- 출력: USB 로만, 막히지 않게 ---------- */

static bool s_usb_ready = false;

static void out_write(const char *s, size_t n)
{
    if (!s_usb_ready || n == 0) return;
    /* PC 가 포트를 열지 않으면 버퍼가 찬다. 그때는 잠깐만 기다리고 버린다 — 지그비 태스크를 붙잡지 않게 */
    usb_serial_jtag_write_bytes(s, n, pdMS_TO_TICKS(5));
}

static void out(const char *fmt, ...)
{
    char buf[320];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
    buf[n++] = '\n';
    out_write(buf, n);
}

static vprintf_like_t s_uart_vprintf;

/* ESP_LOG 기록: UART 로는 그대로, USB 로는 경고 이상만 'LOG ' 를 붙여 한 줄로 */
static int log_vprintf(const char *fmt, va_list ap)
{
    va_list ap2;
    va_copy(ap2, ap);
    int r = s_uart_vprintf ? s_uart_vprintf(fmt, ap) : 0;
    if (fmt && (fmt[0] == 'W' || fmt[0] == 'E')) {
        char buf[240] = "LOG ";
        int n = vsnprintf(buf + 4, sizeof(buf) - 5, fmt, ap2);
        if (n > 0) {
            size_t len = strnlen(buf, sizeof(buf) - 1);
            while (len > 4 && (buf[len - 1] == '\n' || buf[len - 1] == '\r')) len--;
            buf[len++] = '\n';
            out_write(buf, len);
        }
    }
    va_end(ap2);
    return r;
}

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

/* ---------- 상태 ---------- */

typedef struct {
    bool known;
    esp_zb_ieee_addr_t ieee;
    uint16_t short_addr;
} plug_t;

static plug_t s_plug;                 /* 짝지은 플러그 (NVS 에 남긴다) */
static plug_t s_last_join;            /* 마지막으로 붙은 기기 */
static volatile bool s_net_up = false;
static SemaphoreHandle_t s_mx;        /* 아래 캐시를 지킨다 */

static int s_on = -1;                 /* -1 모름 */
static int64_t s_on_ms = 0;
static float s_w = NAN;
static int64_t s_w_ms = 0;
static const char *s_w_src = "none";
static uint16_t s_em_mul = 1, s_em_div = 1;
static bool s_em_md = false;          /* 곱 · 나눔 값을 읽었나 */
static int s_pmode = 0;               /* 전력 읽는 길: 0 모름, 1 전기 측정, 2 아날로그 입력, -1 없음 */

static void cache_on(int on)
{
    xSemaphoreTake(s_mx, portMAX_DELAY);
    s_on = on;
    s_on_ms = now_ms();
    xSemaphoreGive(s_mx);
}

static void cache_w(float w, const char *src)
{
    xSemaphoreTake(s_mx, portMAX_DELAY);
    s_w = w;
    s_w_ms = now_ms();
    s_w_src = src;
    xSemaphoreGive(s_mx);
}

static void ieee_str(const esp_zb_ieee_addr_t a, char *buf)
{
    /* 지그비 주소는 작은 쪽부터 저장된다 — 사람이 읽는 순서(큰 쪽부터)로 */
    sprintf(buf, "%02X:%02X:%02X:%02X:%02X:%02X:%02X:%02X", a[7], a[6], a[5], a[4], a[3], a[2], a[1], a[0]);
}

/* ---------- NVS: 짝지은 플러그 ---------- */

static void plug_save(void)
{
    nvs_handle_t h;
    if (nvs_open("zbplug", NVS_READWRITE, &h) != ESP_OK) return;
    if (s_plug.known) {
        nvs_set_blob(h, "ieee", s_plug.ieee, sizeof(esp_zb_ieee_addr_t));
        nvs_set_u16(h, "short", s_plug.short_addr);
    } else {
        nvs_erase_key(h, "ieee");
        nvs_erase_key(h, "short");
    }
    nvs_commit(h);
    nvs_close(h);
}

static void plug_load(void)
{
    nvs_handle_t h;
    memset(&s_plug, 0, sizeof(s_plug));
    if (nvs_open("zbplug", NVS_READONLY, &h) != ESP_OK) return;
    size_t len = sizeof(esp_zb_ieee_addr_t);
    if (nvs_get_blob(h, "ieee", s_plug.ieee, &len) == ESP_OK && len == sizeof(esp_zb_ieee_addr_t) &&
        nvs_get_u16(h, "short", &s_plug.short_addr) == ESP_OK) {
        s_plug.known = true;
    }
    nvs_close(h);
}

/* ---------- 응답 대기열: 지그비 태스크 → 명령 태스크 ---------- */

typedef enum { R_READ, R_DEFAULT, R_BIND, R_CFGREP, R_EPS, R_DESC } rtype_t;

typedef struct {
    rtype_t type;
    uint16_t cluster;
    uint16_t attr;
    uint8_t status;
    uint8_t dtype;
    uint8_t len;
    uint8_t data[40];
    uint8_t cmd;
} resp_t;

static QueueHandle_t s_q;

static void push(const resp_t *r)
{
    if (xQueueSend(s_q, r, 0) != pdTRUE) {
        resp_t junk;
        xQueueReceive(s_q, &junk, 0); /* 가득 차면 가장 오래된 것을 버린다 */
        xQueueSend(s_q, r, 0);
    }
}

static bool wait_for(rtype_t type, uint16_t cluster, uint16_t attr, resp_t *got, int ms)
{
    int64_t end = now_ms() + ms;
    for (;;) {
        int64_t left = end - now_ms();
        if (left <= 0) return false;
        resp_t r;
        if (xQueueReceive(s_q, &r, pdMS_TO_TICKS(left)) != pdTRUE) return false;
        if (r.type != type) continue;
        if ((type == R_READ || type == R_DEFAULT || type == R_CFGREP) && r.cluster != cluster) continue;
        if (type == R_READ && r.attr != attr) continue;
        if (got) *got = r;
        return true;
    }
}

/* ---------- 값 풀기 ---------- */

static bool val_num(uint8_t t, const uint8_t *d, uint8_t len, double *v)
{
    switch (t) {
    case 0x10: case 0x18: case 0x20: case 0x30: if (len < 1) return false; *v = d[0]; return true;
    case 0x19: case 0x21: case 0x31: if (len < 2) return false; *v = (uint16_t)(d[0] | d[1] << 8); return true;
    case 0x22: if (len < 3) return false; *v = (uint32_t)(d[0] | d[1] << 8 | d[2] << 16); return true;
    case 0x1b: case 0x23: if (len < 4) return false; *v = (uint32_t)(d[0] | d[1] << 8 | d[2] << 16 | (uint32_t)d[3] << 24); return true;
    case 0x25: { /* uint48 — 누적 전력량 */
        if (len < 6) return false;
        uint64_t x = 0;
        for (int i = 5; i >= 0; i--) x = x << 8 | d[i];
        *v = (double)x;
        return true;
    }
    case 0x28: if (len < 1) return false; *v = (int8_t)d[0]; return true;
    case 0x29: if (len < 2) return false; *v = (int16_t)(d[0] | d[1] << 8); return true;
    case 0x2b: if (len < 4) return false; *v = (int32_t)(d[0] | d[1] << 8 | d[2] << 16 | (uint32_t)d[3] << 24); return true;
    case 0x39: { if (len < 4) return false; float f; memcpy(&f, d, 4); *v = f; return true; }
    default: return false;
    }
}

static void val_text(uint8_t t, const uint8_t *d, uint8_t len, char *buf, size_t bufsz)
{
    double v;
    if ((t == 0x41 || t == 0x42) && len >= 1) { /* 길이가 앞에 붙은 문자열 */
        uint8_t n = d[0];
        if (n > len - 1) n = len - 1;
        if (n > bufsz - 3) n = bufsz - 3;
        buf[0] = '"';
        for (uint8_t i = 0; i < n; i++) buf[1 + i] = (d[1 + i] >= 0x20 && d[1 + i] < 0x7f && d[1 + i] != '"') ? d[1 + i] : '?';
        buf[1 + n] = '"';
        buf[2 + n] = 0;
    } else if (val_num(t, d, len, &v)) {
        snprintf(buf, bufsz, "%.6g", v);
    } else {
        size_t o = 0;
        buf[0] = 0;
        o += snprintf(buf, bufsz, "hex:");
        for (uint8_t i = 0; i < len && o + 3 < bufsz; i++) o += snprintf(buf + o, bufsz - o, "%02x", d[i]);
    }
}

static void fill(resp_t *r, const esp_zb_zcl_attribute_t *a)
{
    r->attr = a->id;
    r->dtype = a->data.type;
    uint16_t n = a->data.size;
    if (n > sizeof(r->data)) n = sizeof(r->data);
    if (!a->data.value) n = 0;
    r->len = n;
    if (n) memcpy(r->data, a->data.value, n);
}

/* 켜짐 · 전력에 해당하는 값이면 캐시를 고친다 (읽은 답과 스스로 보낸 보고 모두) */
static void note_value(uint16_t cluster, const resp_t *r)
{
    double v;
    if (!val_num(r->dtype, r->data, r->len, &v)) return;
    if (cluster == CL_ONOFF && r->attr == AT_ONOFF) {
        cache_on(v ? 1 : 0);
    } else if (cluster == CL_EM && r->attr == AT_EM_W) {
        cache_w((float)(v * s_em_mul / (s_em_div ? s_em_div : 1)), "em");
    } else if (cluster == CL_EM && r->attr == AT_EM_MUL) {
        s_em_mul = v > 0 ? (uint16_t)v : 1;
        s_em_md = true;
    } else if (cluster == CL_EM && r->attr == AT_EM_DIV) {
        s_em_div = v > 0 ? (uint16_t)v : 1;
        s_em_md = true;
    } else if (cluster == CL_AI && r->attr == AT_AI_PV) {
        cache_w((float)v, "ai");
    }
}

/* ---------- 지그비 콜백 (지그비 태스크에서 돈다) ---------- */

static esp_err_t zb_action_handler(esp_zb_core_action_callback_id_t id, const void *message)
{
    switch (id) {
    case ESP_ZB_CORE_CMD_READ_ATTR_RESP_CB_ID: {
        const esp_zb_zcl_cmd_read_attr_resp_message_t *m = message;
        for (esp_zb_zcl_read_attr_resp_variable_t *v = m->variables; v; v = v->next) {
            resp_t r = {.type = R_READ, .cluster = m->info.cluster, .status = v->status};
            fill(&r, &v->attribute);
            if (v->status == ESP_ZB_ZCL_STATUS_SUCCESS) note_value(m->info.cluster, &r);
            push(&r);
        }
        break;
    }
    case ESP_ZB_CORE_REPORT_ATTR_CB_ID: {
        const esp_zb_zcl_report_attr_message_t *m = message;
        resp_t r = {.type = R_READ, .cluster = m->cluster};
        fill(&r, &m->attribute);
        note_value(m->cluster, &r);
        char val[96];
        val_text(r.dtype, r.data, r.len, val, sizeof(val));
        out("EVT REPORT src=0x%04x ep=%u cl=0x%04x at=0x%04x type=0x%02x val=%s", m->src_address.u.short_addr,
            m->src_endpoint, m->cluster, r.attr, r.dtype, val);
        break;
    }
    case ESP_ZB_CORE_CMD_DEFAULT_RESP_CB_ID: {
        const esp_zb_zcl_cmd_default_resp_message_t *m = message;
        resp_t r = {.type = R_DEFAULT, .cluster = m->info.cluster, .status = m->status_code, .cmd = m->resp_to_cmd};
        push(&r);
        break;
    }
    case ESP_ZB_CORE_CMD_REPORT_CONFIG_RESP_CB_ID: {
        const esp_zb_zcl_cmd_config_report_resp_message_t *m = message;
        for (esp_zb_zcl_config_report_resp_variable_t *v = m->variables; v; v = v->next) {
            out("EVT REPORTCFG cl=0x%04x at=0x%04x status=0x%02x", m->info.cluster, v->attribute_id, v->status);
        }
        resp_t r = {.type = R_CFGREP, .cluster = m->info.cluster};
        push(&r);
        break;
    }
    default:
        break;
    }
    return ESP_OK;
}

static void bind_cb(esp_zb_zdp_status_t st, void *ctx)
{
    out("EVT BIND cl=0x%04x status=%d", (unsigned)(uintptr_t)ctx, st);
    resp_t r = {.type = R_BIND, .status = st, .cluster = (uint16_t)(uintptr_t)ctx};
    push(&r);
}

static void eps_cb(esp_zb_zdp_status_t st, uint8_t count, uint8_t *list, void *ctx)
{
    char buf[80] = "";
    size_t o = 0;
    for (uint8_t i = 0; i < count && o + 5 < sizeof(buf); i++) o += snprintf(buf + o, sizeof(buf) - o, "%s%u", i ? "," : "", list[i]);
    out("EVT EPS status=%d eps=%s", st, buf);
    resp_t r = {.type = R_EPS, .status = st, .len = count > sizeof(r.data) ? sizeof(r.data) : count};
    if (list && r.len) memcpy(r.data, list, r.len);
    push(&r);
}

static void desc_cb(esp_zb_zdp_status_t st, esp_zb_af_simple_desc_1_1_t *d, void *ctx)
{
    if (st == ESP_ZB_ZDP_STATUS_SUCCESS && d) {
        char in[160] = "", outc[160] = "";
        size_t a = 0, b = 0;
        for (int i = 0; i < d->app_input_cluster_count && a + 8 < sizeof(in); i++)
            a += snprintf(in + a, sizeof(in) - a, "%s0x%04x", i ? "," : "", d->app_cluster_list[i]);
        for (int i = 0; i < d->app_output_cluster_count && b + 8 < sizeof(outc); i++)
            b += snprintf(outc + b, sizeof(outc) - b, "%s0x%04x", i ? "," : "", d->app_cluster_list[d->app_input_cluster_count + i]);
        out("EVT DESC ep=%u profile=0x%04x dev=0x%04x in=%s out=%s", d->endpoint, d->app_profile_id, d->app_device_id, in, outc);
    } else {
        out("EVT DESC ep=%u status=%d", (unsigned)(uintptr_t)ctx, st);
    }
    resp_t r = {.type = R_DESC, .status = st};
    push(&r);
}

/* ---------- 플러그에 보내기 (명령 태스크에서 부른다 — 반드시 잠금 안에서) ---------- */

static void refresh_short(void)
{
    /* 플러그의 짧은 주소는 다시 붙을 때 바뀔 수 있다 — 긴 주소(IEEE)로 지금 주소를 다시 찾는다 */
    uint16_t sa = esp_zb_address_short_by_ieee(s_plug.ieee);
    if (sa != 0xFFFF && sa != 0xFFFE && sa != s_plug.short_addr) {
        s_plug.short_addr = sa;
        plug_save();
    }
}

static void basic_cmd(esp_zb_zcl_basic_cmd_t *b, uint8_t ep)
{
    b->dst_addr_u.addr_short = s_plug.short_addr;
    b->dst_endpoint = ep;
    b->src_endpoint = MY_EP;
}

static void send_read(uint16_t cluster, uint8_t ep, uint16_t *attrs, uint8_t n)
{
    esp_zb_zcl_read_attr_cmd_t c;
    memset(&c, 0, sizeof(c));
    esp_zb_lock_acquire(portMAX_DELAY);
    refresh_short();
    basic_cmd(&c.zcl_basic_cmd, ep);
    c.address_mode = ESP_ZB_APS_ADDR_MODE_16_ENDP_PRESENT;
    c.clusterID = cluster;
    c.attr_number = n;
    c.attr_field = attrs;
    esp_zb_zcl_read_attr_cmd_req(&c);
    esp_zb_lock_release();
}

static void send_onoff(bool on)
{
    esp_zb_zcl_on_off_cmd_t c;
    memset(&c, 0, sizeof(c));
    esp_zb_lock_acquire(portMAX_DELAY);
    refresh_short();
    basic_cmd(&c.zcl_basic_cmd, PLUG_EP);
    c.address_mode = ESP_ZB_APS_ADDR_MODE_16_ENDP_PRESENT;
    c.on_off_cmd_id = on ? ESP_ZB_ZCL_CMD_ON_OFF_ON_ID : ESP_ZB_ZCL_CMD_ON_OFF_OFF_ID;
    esp_zb_zcl_on_off_cmd_req(&c);
    esp_zb_lock_release();
}

/* 플러그 → 이 보드로 보고가 오게 묶는다 (짝지은 직후 한 번, SETUP 으로 다시) */
static void send_bind(uint16_t cluster)
{
    esp_zb_zdo_bind_req_param_t b;
    memset(&b, 0, sizeof(b));
    memcpy(b.src_address, s_plug.ieee, sizeof(esp_zb_ieee_addr_t));
    b.src_endp = PLUG_EP;
    b.cluster_id = cluster;
    b.dst_addr_mode = ESP_ZB_ZDO_BIND_DST_ADDR_MODE_64_BIT_EXTENDED;
    esp_zb_get_long_address(b.dst_address_u.addr_long);
    b.dst_endp = MY_EP;
    b.req_dst_addr = s_plug.short_addr;
    esp_zb_zdo_device_bind_req(&b, bind_cb, (void *)(uintptr_t)cluster);
}

static void send_report_cfg(uint16_t cluster, uint16_t attr, uint8_t type, uint16_t min_s, uint16_t max_s, void *change)
{
    esp_zb_zcl_config_report_record_t rec = {
        .direction = ESP_ZB_ZCL_REPORT_DIRECTION_SEND,
        .attributeID = attr,
        .attrType = type,
        .min_interval = min_s,
        .max_interval = max_s,
        .reportable_change = change,
    };
    esp_zb_zcl_config_report_cmd_t c;
    memset(&c, 0, sizeof(c));
    basic_cmd(&c.zcl_basic_cmd, PLUG_EP);
    c.address_mode = ESP_ZB_APS_ADDR_MODE_16_ENDP_PRESENT;
    c.clusterID = cluster;
    c.record_number = 1;
    c.record_field = &rec;
    esp_zb_zcl_config_report_cmd_req(&c);
}

static void setup_reports(void)
{
    static int16_t w_change = 5; /* 전력이 이만큼(원값) 바뀌면 보고 */
    esp_zb_lock_acquire(portMAX_DELAY);
    refresh_short();
    send_bind(CL_ONOFF);
    send_bind(CL_EM);
    send_report_cfg(CL_ONOFF, AT_ONOFF, ESP_ZB_ZCL_ATTR_TYPE_BOOL, 0, 300, NULL);
    send_report_cfg(CL_EM, AT_EM_W, ESP_ZB_ZCL_ATTR_TYPE_S16, 5, 60, &w_change);
    esp_zb_lock_release();
}

static void read_basic(void)
{
    /* 제조사 · 모델 · 날짜 · 펌웨어 판 · 전원 · 앱 판 */
    static uint16_t a1[] = {0x0004, 0x0005, 0x0006};
    static uint16_t a2[] = {0x4000, 0x0007, 0x0001};
    send_read(CL_BASIC, PLUG_EP, a1, 3);
    send_read(CL_BASIC, PLUG_EP, a2, 3);
}

/* ---------- 명령 처리 (명령 태스크) ---------- */

static void reply_state(const char *tok, int ack)
{
    xSemaphoreTake(s_mx, portMAX_DELAY);
    int on = s_on;
    float w = s_w;
    int64_t on_age = s_on_ms ? now_ms() - s_on_ms : -1, w_age = s_w_ms ? now_ms() - s_w_ms : -1;
    const char *src = s_w_src;
    xSemaphoreGive(s_mx);
    char ons[4];
    snprintf(ons, sizeof(ons), "%s", on < 0 ? "?" : on ? "1" : "0");
    char ws[24];
    if (isnan(w)) snprintf(ws, sizeof(ws), "nan");
    else snprintf(ws, sizeof(ws), "%.1f", w);
    out("STATE %s on=%s w=%s src=%s on_age=%.1f w_age=%.1f ack=%d pmode=%d mul=%u div=%u md=%d", tok, ons, ws, src,
        on_age < 0 ? -1.0 : on_age / 1000.0, w_age < 0 ? -1.0 : w_age / 1000.0, ack, s_pmode, s_em_mul, s_em_div, s_em_md ? 1 : 0);
}

/* 켜짐과 전력을 새로 읽는다. 못 읽은 값은 캐시에 남은 값과 그 나이로 답한다 */
static void do_state(const char *tok, int ack)
{
    resp_t r;
    uint16_t a_on[] = {AT_ONOFF};
    xQueueReset(s_q);
    send_read(CL_ONOFF, PLUG_EP, a_on, 1);
    wait_for(R_READ, CL_ONOFF, AT_ONOFF, &r, WAIT_MS);

    if (s_pmode >= 0 && s_pmode != 2) {
        uint16_t a_em[] = {AT_EM_W, AT_EM_MUL, AT_EM_DIV};
        xQueueReset(s_q);
        send_read(CL_EM, PLUG_EP, a_em, s_em_md ? 1 : 3);
        if (wait_for(R_READ, CL_EM, AT_EM_W, &r, WAIT_MS)) {
            if (r.status == ESP_ZB_ZCL_STATUS_SUCCESS) s_pmode = 1;
            else if (s_pmode == 0) s_pmode = 2; /* 지원하지 않는다 — 아날로그 입력으로 */
        }
    }
    if (s_pmode == 2) {
        uint16_t a_ai[] = {AT_AI_PV};
        xQueueReset(s_q);
        send_read(CL_AI, AI_EP, a_ai, 1);
        if (wait_for(R_READ, CL_AI, AT_AI_PV, &r, WAIT_MS) && r.status != ESP_ZB_ZCL_STATUS_SUCCESS) s_pmode = -1;
    }
    reply_state(tok, ack);
}

static void do_onoff(const char *tok, bool on)
{
    resp_t r;
    xQueueReset(s_q);
    send_onoff(on);
    int ack = 0;
    if (wait_for(R_DEFAULT, CL_ONOFF, 0, &r, WAIT_MS)) ack = r.status == ESP_ZB_ZCL_STATUS_SUCCESS ? 1 : -1;
    vTaskDelay(pdMS_TO_TICKS(300)); /* 릴레이가 움직이고 전력이 바뀔 틈 */
    do_state(tok, ack);
}

static void do_info(const char *tok)
{
    xQueueReset(s_q);
    read_basic();
    int got = 0;
    int64_t end = now_ms() + 2 * WAIT_MS;
    char line[300] = "";
    size_t o = 0;
    while (got < 6) {
        int64_t left = end - now_ms();
        resp_t r;
        if (left <= 0 || xQueueReceive(s_q, &r, pdMS_TO_TICKS(left)) != pdTRUE) break;
        if (r.type != R_READ || r.cluster != CL_BASIC) continue;
        got++;
        char val[64];
        if (r.status == ESP_ZB_ZCL_STATUS_SUCCESS) val_text(r.dtype, r.data, r.len, val, sizeof(val));
        else snprintf(val, sizeof(val), "status:0x%02x", r.status);
        o += snprintf(line + o, sizeof(line) - o, " 0x%04x=%s", r.attr, val);
        if (o >= sizeof(line)) break;
    }
    if (got) out("OK %s INFO%s", tok, line);
    else out("ERR %s no_answer", tok);
}

static void do_desc(const char *tok)
{
    resp_t r;
    xQueueReset(s_q);
    esp_zb_zdo_active_ep_req_param_t a = {0};
    esp_zb_lock_acquire(portMAX_DELAY);
    refresh_short();
    a.addr_of_interest = s_plug.short_addr;
    esp_zb_zdo_active_ep_req(&a, eps_cb, NULL);
    esp_zb_lock_release();
    if (!wait_for(R_EPS, 0, 0, &r, WAIT_MS) || r.status != ESP_ZB_ZDP_STATUS_SUCCESS) {
        out("ERR %s no_answer", tok);
        return;
    }
    uint8_t eps[sizeof(r.data)];
    uint8_t n = r.len;
    memcpy(eps, r.data, n);
    for (uint8_t i = 0; i < n; i++) {
        esp_zb_zdo_simple_desc_req_param_t d = {0};
        esp_zb_lock_acquire(portMAX_DELAY);
        d.addr_of_interest = s_plug.short_addr;
        d.endpoint = eps[i];
        esp_zb_zdo_simple_desc_req(&d, desc_cb, (void *)(uintptr_t)eps[i]);
        esp_zb_lock_release();
        wait_for(R_DESC, 0, 0, &r, WAIT_MS);
    }
    out("OK %s DESC eps=%u", tok, n);
}

static void do_ping(const char *tok)
{
    char me[24], pl[24] = "none";
    esp_zb_ieee_addr_t mine;
    esp_zb_lock_acquire(portMAX_DELAY);
    esp_zb_get_long_address(mine);
    uint16_t pan = esp_zb_get_pan_id();
    uint8_t ch = esp_zb_get_current_channel();
    if (s_plug.known) refresh_short();
    esp_zb_lock_release();
    ieee_str(mine, me);
    if (s_plug.known) ieee_str(s_plug.ieee, pl);
    out("OK %s PONG fw=%s net=%d ch=%u pan=0x%04x me=%s plug=%s short=0x%04x lqi_min=%u", tok, FW_NAME, s_net_up ? 1 : 0, ch, pan, me, pl,
        s_plug.known ? s_plug.short_addr : 0xFFFF, esp_zb_secur_network_min_join_lqi_get());
}

static void adopt(const plug_t *p)
{
    s_plug = *p;
    s_plug.known = true;
    s_pmode = 0;
    s_em_md = false;
    s_em_mul = s_em_div = 1;
    cache_on(-1);
    cache_w(NAN, "none");
    plug_save();
}

static void handle(char *line)
{
    char *argv[6];
    int argc = 0;
    const char *tok = "#-";
    for (char *p = strtok(line, " \t"); p && argc < 6; p = strtok(NULL, " \t")) {
        if (p[0] == '#') tok = p;
        else argv[argc++] = p;
    }
    if (argc == 0) return;
    for (char *c = argv[0]; *c; c++) if (*c >= 'a' && *c <= 'z') *c -= 32;
    const char *cmd = argv[0];

    if (!strcmp(cmd, "PING")) { do_ping(tok); return; }
    if (!strcmp(cmd, "REBOOT")) { out("OK %s REBOOT", tok); vTaskDelay(pdMS_TO_TICKS(200)); esp_restart(); }
    if (!s_net_up) { out("ERR %s net_down", tok); return; }

    if (!strcmp(cmd, "JOIN") || !strcmp(cmd, "CLOSE")) {
        int sec = !strcmp(cmd, "CLOSE") ? 0 : (argc > 1 ? atoi(argv[1]) : 180);
        if (sec < 0) sec = 0;
        if (sec > 254) sec = 254;
        esp_zb_lock_acquire(portMAX_DELAY);
        esp_err_t e = esp_zb_bdb_open_network(sec);
        esp_zb_lock_release();
        if (e == ESP_OK) out("OK %s JOIN open=%d", tok, sec);
        else out("ERR %s %s", tok, esp_err_to_name(e));
        return;
    }
    if (!strcmp(cmd, "FACTORY")) {
        if (argc > 1 && !strcmp(argv[1], "YES")) {
            memset(&s_plug, 0, sizeof(s_plug));
            plug_save();
            out("OK %s FACTORY", tok);
            vTaskDelay(pdMS_TO_TICKS(200));
            esp_zb_factory_reset(); /* 망을 지우고 다시 켠다 */
            return;
        }
        out("ERR %s need_YES", tok);
        return;
    }
    if (!strcmp(cmd, "ADOPT")) {
        if (!s_last_join.known) { out("ERR %s nothing_joined", tok); return; }
        adopt(&s_last_join);
        char b[24];
        ieee_str(s_plug.ieee, b);
        out("OK %s ADOPT plug=%s short=0x%04x", tok, b, s_plug.short_addr);
        return;
    }
    if (!strcmp(cmd, "FORGET")) {
        memset(&s_plug, 0, sizeof(s_plug));
        plug_save();
        out("OK %s FORGET", tok);
        return;
    }
    if (!s_plug.known) { out("ERR %s no_plug", tok); return; }

    if (!strcmp(cmd, "STATE")) do_state(tok, 0);
    else if (!strcmp(cmd, "ON")) do_onoff(tok, true);
    else if (!strcmp(cmd, "OFF")) do_onoff(tok, false);
    else if (!strcmp(cmd, "INFO")) do_info(tok);
    else if (!strcmp(cmd, "DESC")) do_desc(tok);
    else if (!strcmp(cmd, "SETUP")) {
        xQueueReset(s_q);
        setup_reports();
        out("OK %s SETUP", tok);
    } else out("ERR %s unknown_command", tok);
}

static void cmd_task(void *arg)
{
    char line[96];
    size_t n = 0;
    uint8_t buf[64];
    for (;;) {
        int got = usb_serial_jtag_read_bytes(buf, sizeof(buf), portMAX_DELAY);
        for (int i = 0; i < got; i++) {
            char c = (char)buf[i];
            if (c == '\n' || c == '\r') {
                if (n) {
                    line[n] = 0;
                    handle(line);
                    n = 0;
                }
            } else if (n < sizeof(line) - 1) {
                line[n++] = c;
            }
        }
    }
}

/* ---------- 짝짓기 뒤 자동 설정 (지그비 태스크의 예약 실행) ---------- */

static void after_join(uint8_t unused)
{
    if (!s_plug.known) return;
    send_bind(CL_ONOFF);
    send_bind(CL_EM);
    static int16_t w_change = 5;
    send_report_cfg(CL_ONOFF, AT_ONOFF, ESP_ZB_ZCL_ATTR_TYPE_BOOL, 0, 300, NULL);
    send_report_cfg(CL_EM, AT_EM_W, ESP_ZB_ZCL_ATTR_TYPE_S16, 5, 60, &w_change);
}

static void read_basic_alarm(uint8_t unused)
{
    /* 아카라 기기는 붙은 직후 기본 정보를 묻지 않으면 짝짓기가 실패한 것으로 보고 떠나기도 한다 — 바로 묻는다 */
    static uint16_t a1[] = {0x0004, 0x0005, 0x4000};
    esp_zb_zcl_read_attr_cmd_t c;
    memset(&c, 0, sizeof(c));
    basic_cmd(&c.zcl_basic_cmd, PLUG_EP);
    c.address_mode = ESP_ZB_APS_ADDR_MODE_16_ENDP_PRESENT;
    c.clusterID = CL_BASIC;
    c.attr_number = 3;
    c.attr_field = a1;
    esp_zb_zcl_read_attr_cmd_req(&c);
}

/* ---------- 지그비 신호 ---------- */

static void start_commissioning(uint8_t mode) { esp_zb_bdb_start_top_level_commissioning(mode); }

void esp_zb_app_signal_handler(esp_zb_app_signal_t *sig)
{
    uint32_t *p = sig->p_app_signal;
    esp_err_t err = sig->esp_err_status;
    esp_zb_app_signal_type_t type = *p;
    switch (type) {
    case ESP_ZB_ZDO_SIGNAL_SKIP_STARTUP:
        esp_zb_bdb_start_top_level_commissioning(ESP_ZB_BDB_MODE_INITIALIZATION);
        break;
    case ESP_ZB_BDB_SIGNAL_DEVICE_FIRST_START:
    case ESP_ZB_BDB_SIGNAL_DEVICE_REBOOT:
        if (err == ESP_OK) {
            if (esp_zb_bdb_is_factory_new()) {
                out("EVT NET forming ch=%d", ZB_CHANNEL);
                esp_zb_bdb_start_top_level_commissioning(ESP_ZB_BDB_MODE_NETWORK_FORMATION);
            } else {
                s_net_up = true;
                out("EVT NET up ch=%u pan=0x%04x", esp_zb_get_current_channel(), esp_zb_get_pan_id());
            }
        } else {
            out("EVT NET start_failed %s", esp_err_to_name(err));
            esp_zb_scheduler_alarm((esp_zb_callback_t)start_commissioning, ESP_ZB_BDB_MODE_INITIALIZATION, 1000);
        }
        break;
    case ESP_ZB_BDB_SIGNAL_FORMATION:
        if (err == ESP_OK) {
            s_net_up = true;
            out("EVT NET formed ch=%u pan=0x%04x", esp_zb_get_current_channel(), esp_zb_get_pan_id());
        } else {
            out("EVT NET formation_failed %s", esp_err_to_name(err));
            esp_zb_scheduler_alarm((esp_zb_callback_t)start_commissioning, ESP_ZB_BDB_MODE_NETWORK_FORMATION, 1000);
        }
        break;
    case ESP_ZB_ZDO_SIGNAL_DEVICE_ANNCE: {
        esp_zb_zdo_signal_device_annce_params_t *a = esp_zb_app_signal_get_params(p);
        plug_t j = {.known = true, .short_addr = a->device_short_addr};
        memcpy(j.ieee, a->ieee_addr, sizeof(esp_zb_ieee_addr_t));
        s_last_join = j;
        char b[24];
        ieee_str(j.ieee, b);
        bool same = s_plug.known && !memcmp(s_plug.ieee, j.ieee, sizeof(esp_zb_ieee_addr_t));
        if (!s_plug.known) adopt(&j);
        else if (same && s_plug.short_addr != j.short_addr) {
            s_plug.short_addr = j.short_addr;
            plug_save();
        }
        out("EVT JOIN ieee=%s short=0x%04x cap=0x%02x plug=%s", b, j.short_addr, a->capability,
            same ? "same" : (s_plug.known && !memcmp(s_plug.ieee, j.ieee, sizeof(esp_zb_ieee_addr_t))) ? "adopted" : "other");
        if (s_plug.known && !memcmp(s_plug.ieee, j.ieee, sizeof(esp_zb_ieee_addr_t))) {
            esp_zb_scheduler_alarm((esp_zb_callback_t)read_basic_alarm, 0, 200);
            esp_zb_scheduler_alarm((esp_zb_callback_t)after_join, 0, 3000);
        }
        break;
    }
    case ESP_ZB_ZDO_SIGNAL_LEAVE_INDICATION: {
        esp_zb_zdo_signal_leave_indication_params_t *l = esp_zb_app_signal_get_params(p);
        char b[24];
        ieee_str(l->device_addr, b);
        out("EVT LEAVE ieee=%s short=0x%04x rejoin=%u", b, l->short_addr, l->rejoin);
        break;
    }
    case ESP_ZB_NWK_SIGNAL_PERMIT_JOIN_STATUS:
        if (err == ESP_OK) out("EVT PERMIT open=%u", *(uint8_t *)esp_zb_app_signal_get_params(p));
        break;
    case ESP_ZB_ZDO_SIGNAL_DEVICE_UPDATE: { /* 붙으려는 기기가 망에 들어온 첫 단계 — 짝짓기가 안 될 때 어디서 멈췄는지 본다 */
        esp_zb_zdo_signal_device_update_params_t *u = esp_zb_app_signal_get_params(p);
        char b[24];
        ieee_str(u->long_addr, b);
        out("EVT UPDATE ieee=%s short=0x%04x status=%u tc_action=%u parent=0x%04x", b, u->short_addr, u->status, u->tc_action, u->parent_short);
        break;
    }
    case ESP_ZB_ZDO_SIGNAL_DEVICE_AUTHORIZED: { /* 망 키를 받고 인증까지 됐나 */
        esp_zb_zdo_signal_device_authorized_params_t *a = esp_zb_app_signal_get_params(p);
        char b[24];
        ieee_str(a->long_addr, b);
        out("EVT AUTH ieee=%s short=0x%04x type=%u status=%u", b, a->short_addr, a->authorization_type, a->authorization_status);
        break;
    }
    default:
        out("EVT SIG %s 0x%x %s", esp_zb_zdo_signal_to_string(type), type, esp_err_to_name(err));
        break;
    }
}

/* ---------- 시작 ---------- */

static esp_zb_ep_list_t *make_endpoint(void)
{
    esp_zb_cluster_list_t *cl = esp_zb_zcl_cluster_list_create();

    esp_zb_basic_cluster_cfg_t basic_cfg = {
        .zcl_version = ESP_ZB_ZCL_BASIC_ZCL_VERSION_DEFAULT_VALUE,
        .power_source = 0x01, /* 주 전원(USB) */
    };
    esp_zb_attribute_list_t *basic = esp_zb_basic_cluster_create(&basic_cfg);
    esp_zb_basic_cluster_add_attr(basic, ESP_ZB_ZCL_ATTR_BASIC_MANUFACTURER_NAME_ID, (void *)"\x0a""FITOGETHER");
    esp_zb_basic_cluster_add_attr(basic, ESP_ZB_ZCL_ATTR_BASIC_MODEL_IDENTIFIER_ID, (void *)"\x08""zb-plug1");
    esp_zb_cluster_list_add_basic_cluster(cl, basic, ESP_ZB_ZCL_CLUSTER_SERVER_ROLE);

    esp_zb_identify_cluster_cfg_t id_cfg = {.identify_time = 0};
    esp_zb_cluster_list_add_identify_cluster(cl, esp_zb_identify_cluster_create(&id_cfg), ESP_ZB_ZCL_CLUSTER_SERVER_ROLE);

    /* 플러그 쪽 클러스터를 부리는(client) 쪽으로 둔다 — 보고를 받으려면 이 보드 엔드포인트에 있어야 한다 */
    esp_zb_cluster_list_add_on_off_cluster(cl, esp_zb_zcl_attr_list_create(CL_ONOFF), ESP_ZB_ZCL_CLUSTER_CLIENT_ROLE);
    esp_zb_cluster_list_add_electrical_meas_cluster(cl, esp_zb_zcl_attr_list_create(CL_EM), ESP_ZB_ZCL_CLUSTER_CLIENT_ROLE);
    esp_zb_cluster_list_add_metering_cluster(cl, esp_zb_zcl_attr_list_create(CL_METER), ESP_ZB_ZCL_CLUSTER_CLIENT_ROLE);
    esp_zb_cluster_list_add_analog_input_cluster(cl, esp_zb_zcl_attr_list_create(CL_AI), ESP_ZB_ZCL_CLUSTER_CLIENT_ROLE);

    esp_zb_ep_list_t *eps = esp_zb_ep_list_create();
    esp_zb_endpoint_config_t ep_cfg = {
        .endpoint = MY_EP,
        .app_profile_id = ESP_ZB_AF_HA_PROFILE_ID,
        .app_device_id = ESP_ZB_HA_REMOTE_CONTROL_DEVICE_ID,
        .app_device_version = 0,
    };
    esp_zb_ep_list_add_ep(eps, cl, ep_cfg);
    return eps;
}

static void zb_task(void *arg)
{
    esp_zb_cfg_t cfg = {
        .esp_zb_role = ESP_ZB_DEVICE_TYPE_COORDINATOR,
        .install_code_policy = false,
        .nwk_cfg.zczr_cfg = {.max_children = MAX_CHILDREN},
    };
    esp_zb_init(&cfg);
    esp_zb_secur_network_min_join_lqi_set(0); /* 신호 질(LQI)이 낮다는 이유로 붙는 것을 막지 않는다 — 플러그는 보드 옆에 있다 */
    esp_zb_device_register(make_endpoint());
    esp_zb_core_action_handler_register(zb_action_handler);
    esp_zb_set_primary_network_channel_set(1UL << ZB_CHANNEL);
    ESP_ERROR_CHECK(esp_zb_start(false));
    esp_zb_stack_main_loop();
}

void app_main(void)
{
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);

    usb_serial_jtag_driver_config_t ucfg = {.rx_buffer_size = 512, .tx_buffer_size = 2048};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&ucfg));
    s_usb_ready = true;
    s_uart_vprintf = esp_log_set_vprintf(log_vprintf);
    esp_log_level_set("*", ESP_LOG_INFO);

    s_mx = xSemaphoreCreateMutex();
    s_q = xQueueCreate(16, sizeof(resp_t));
    plug_load();
    out("EVT BOOT fw=%s", FW_NAME);

    esp_zb_platform_config_t pcfg = {
        .radio_config = {.radio_mode = ZB_RADIO_MODE_NATIVE},
        .host_config = {.host_connection_mode = ZB_HOST_CONNECTION_MODE_NONE},
    };
    ESP_ERROR_CHECK(esp_zb_platform_config(&pcfg));
    xTaskCreate(zb_task, "zigbee", 8192, NULL, 5, NULL);
    xTaskCreate(cmd_task, "cmd", 6144, NULL, 4, NULL);
}

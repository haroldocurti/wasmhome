#include "sdkconfig.h"
#if defined(CONFIG_WASMHOME_OBSERVER_SELFTEST)
#if !defined(WASMHOME_SCENARIO_NATIVE)
#error "Observer calibration fixture must use the native scenario"
#endif
#include "platform.h"
#include "driver/uart.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* Test-only reference responder. This is not OTA or a campaign module.
 * The mutex serializes fixture state across the UDP and telemetry tasks. */
static SemaphoreHandle_t fixture_lock;
static bool silent;
static uint8_t version = 1;
static uint8_t gpio_state;
static uint64_t last_id;
static int32_t last_input;
static uint8_t last_attempt[16];
static uint8_t last_result[12];

static void receive_fixture(const uint8_t *data, size_t length)
{
    (void)length;
    xSemaphoreTake(fixture_lock, portMAX_DELAY);
    if (silent) {
        xSemaphoreGive(fixture_lock);
        return;
    }
    if (data[5] == WH_UPDATE_TRIGGER) {
        uint8_t body[20] = {0};
        wh_write_be(body, wh_read_be(data + 24, 8), 8);
        memcpy(body + 8, data + 48, 8);
        body[16] = 2; /* REJECTED. No artifact transfer in this fixture. */
        body[17] = wh_read_be(data + 32, 8) == wh_platform_boot_id() ? 6 : 1;
        wh_observation_send(WH_UPDATE_ACCEPTANCE, data + 8, body, sizeof(body));
        wh_platform_event("observer_fixture_update_rejected", ESP_OK);
    } else {
        uint64_t id = wh_read_be(data + 48, 8);
        int32_t input = (int32_t)(uint32_t)wh_read_be(data + 56, 4);
        if (id == last_id && !memcmp(last_attempt, data + 8, 16)) {
            if (input == last_input)
                wh_observation_send(WH_CONTROL_RESULT, data + 8, last_result, sizeof(last_result));
            else wh_platform_event("observer_fixture_id_conflict", ESP_ERR_INVALID_ARG);
        } else {
            int32_t threshold = version == 1 ? 25000 : 30000;
            if (input >= threshold + 1000) gpio_state = 1;
            else if (input <= threshold - 1000) gpio_state = 0;
            uint8_t observed = 255;
            esp_err_t err = wh_gpio_apply(gpio_state, &observed);
            uint8_t body[12] = {0};
            wh_write_be(body, id, 8);
            body[8] = version;
            body[9] = gpio_state;
            body[10] = observed;
            body[11] = err != ESP_OK ? 3 : gpio_state == observed ? 0 : 1;
            last_id = id;
            last_input = input;
            memcpy(last_attempt, data + 8, 16);
            memcpy(last_result, body, sizeof(body));
            wh_observation_send(WH_CONTROL_RESULT, data + 8, body, sizeof(body));
        }
    }
    xSemaphoreGive(fixture_lock);
}

static void command(const char *text)
{
    if (!strcmp(text, "WH_REBOOT")) {
        wh_platform_event("observer_fixture_reboot", ESP_OK);
        fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(50));
        esp_restart();
    } else if (!strcmp(text, "WH_GAP")) {
        xSemaphoreTake(fixture_lock, portMAX_DELAY);
        silent = true;
        /* Both channels are disabled before this timestamp is printed. */
        wh_platform_event("observer_fixture_gap_start", ESP_OK);
        xSemaphoreGive(fixture_lock);
        vTaskDelay(pdMS_TO_TICKS(1000));
        xSemaphoreTake(fixture_lock, portMAX_DELAY);
        silent = false;
        wh_platform_event("observer_fixture_gap_end", ESP_OK);
        xSemaphoreGive(fixture_lock);
    } else if (!strcmp(text, "WH_V1") || !strcmp(text, "WH_V2")) {
        xSemaphoreTake(fixture_lock, portMAX_DELAY);
        version = text[4] == '1' ? 1 : 2;
        gpio_state = 0;
        last_id = 0;
        wh_platform_event(version == 1 ? "observer_fixture_v1" : "observer_fixture_v2", ESP_OK);
        xSemaphoreGive(fixture_lock);
    }
}

static void fixture_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    char text[32] = {0};
    size_t used = 0;
    for (;;) {
        uint8_t incoming[32];
        int count = uart_read_bytes(CONFIG_ESP_CONSOLE_UART_NUM, incoming, sizeof(incoming), 0);
        for (int i = 0; i < count; ++i) {
            if (incoming[i] == '\n') {
                text[used] = 0;
                command(text);
                used = 0;
            } else if (incoming[i] != '\r') {
                if (used < sizeof(text) - 1) text[used++] = incoming[i];
                else used = 0;
            }
        }
        uint8_t attempt[16] = {0}, body[16] = {0};
        wh_sensor_sample_t sample = wh_sensor_latest();
        wh_write_be(body, sample.sample_id, 8);
        wh_write_be(body + 8, (uint32_t)sample.temperature_mC, 4);
        uint64_t age = sample.sample_id ? (esp_timer_get_time() - sample.acquired_us) / 1000 : 65535;
        if (sample.sample_id && age > 65534) age = 65534;
        wh_write_be(body + 12, age, 2);
        body[14] = (uint8_t)sample.status;
        xSemaphoreTake(fixture_lock, portMAX_DELAY);
        if (!silent) wh_observation_send(WH_TELEMETRY, attempt, body, sizeof(body));
        xSemaphoreGive(fixture_lock);
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

esp_err_t wh_observer_selftest_start(void)
{
    fixture_lock = xSemaphoreCreateMutex();
    if (!fixture_lock) return ESP_ERR_NO_MEM;
    if (!uart_is_driver_installed(CONFIG_ESP_CONSOLE_UART_NUM)) {
        esp_err_t err = uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, 1024, 0, 0, NULL, 0);
        if (err != ESP_OK) return err;
    }
    esp_err_t err = wh_observation_set_handler(receive_fixture);
    if (err != ESP_OK) return err;
    wh_platform_event("observer_fixture_no_ota_or_modules", ESP_OK);
    return xTaskCreate(fixture_task, "wh_fixture", 4096, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
#endif

#include "sdkconfig.h"
#if defined(CONFIG_WASMHOME_PLATFORM_SELFTEST)
#include "platform.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* Diagnostic fixture only. It never executes or acknowledges a control/update
 * service. Serial reception proves transport without simulating availability. */
static void receive_probe(const uint8_t *data, size_t length)
{
    (void)length;
    printf("WASMHOME_DIAG_RX,{\"type\":%u,\"sequence\":%" PRIu64 ",\"id\":%" PRIu64 "}\n",
           data[5], wh_read_be(data + 24, 8), wh_read_be(data + 48, 8));
}

static void diagnostic_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    uint8_t attempt[16] = {0};
    TickType_t cycle = xTaskGetTickCount();
    uint32_t ticks = 0;
    char command[16] = {0};
    size_t command_length = 0;
    for (;;) {
        uint8_t incoming[16];
        int count = uart_read_bytes(CONFIG_ESP_CONSOLE_UART_NUM, incoming, sizeof(incoming), 0);
        for (int i = 0; i < count; ++i) {
            if (incoming[i] == '\n') {
                command[command_length] = 0;
                if (!strcmp(command, "WH_WDT")) {
                    wh_platform_event("selftest_watchdog_triggered", ESP_OK);
                    /* Deliberately miss this subscribed task's feed deadline.
                     * This command exists only in the diagnostic firmware. */
                    vTaskDelay(pdMS_TO_TICKS(10000));
                }
                command_length = 0;
            } else if (incoming[i] != '\r') {
                if (command_length < sizeof(command) - 1) command[command_length++] = incoming[i];
                else command_length = 0;
            }
        }
        wh_sensor_sample_t sample = wh_sensor_latest();
        uint8_t body[16] = {0};
        wh_write_be(body, sample.sample_id, 8);
        wh_write_be(body + 8, (uint32_t)sample.temperature_mC, 4);
        uint64_t age = sample.sample_id ? (esp_timer_get_time() - sample.acquired_us) / 1000 : 65535;
        if (sample.sample_id && age > 65534) age = 65534;
        wh_write_be(body + 12, age, 2);
        body[14] = (uint8_t)sample.status;
        /* This stream validates transport/acquisition, not a telemetry module. */
        wh_observation_send(WH_TELEMETRY, attempt, body, sizeof(body));
        if (!(ticks % 10)) {
            uint8_t requested = (ticks / 10) % 2, observed = 255;
            esp_err_t result = wh_gpio_apply(requested, &observed);
            printf("WASMHOME_DIAG,{\"sample_id\":%" PRIu64 ",\"temperature_mC\":%" PRId32
                   ",\"age_ms\":%" PRIu64 ",\"sensor_status\":%u,\"requested_gpio\":%u,"
                   "\"observed_gpio\":%u,\"gpio_error\":%d}\n",
                   sample.sample_id, sample.temperature_mC, age, sample.status,
                   requested, observed, (int)result);
        }
        ticks++;
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        xTaskDelayUntil(&cycle, pdMS_TO_TICKS(100));
    }
}

esp_err_t wh_platform_selftest_start(void)
{
    if (!uart_is_driver_installed(CONFIG_ESP_CONSOLE_UART_NUM)) {
        esp_err_t err = uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, 1024, 0, 0, NULL, 0);
        if (err != ESP_OK) return err;
    }
    esp_err_t err = wh_observation_set_handler(receive_probe);
    if (err != ESP_OK) return err;
    wh_platform_event("platform_selftest_diagnostic_only", ESP_OK);
    return xTaskCreate(diagnostic_task, "wh_diag", 4096, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
#endif

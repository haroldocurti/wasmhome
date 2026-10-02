#include "platform.h"
#include "onewire_bus.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#include "sdkconfig.h"
#include <limits.h>

static portMUX_TYPE sample_lock = portMUX_INITIALIZER_UNLOCKED;
static wh_sensor_sample_t latest = {.temperature_mC = INT32_MIN, .status = WH_SENSOR_NO_VALID_SAMPLE};
static onewire_bus_handle_t bus;

wh_sensor_sample_t wh_sensor_latest(void)
{
    portENTER_CRITICAL(&sample_lock);
    wh_sensor_sample_t copy = latest;
    portEXIT_CRITICAL(&sample_lock);
    return copy;
}

static esp_err_t command(const uint8_t *bytes, size_t n)
{
    esp_err_t err = onewire_bus_reset(bus);
    return err == ESP_OK ? onewire_bus_write_bytes(bus, bytes, n) : err;
}

static void sensor_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    TickType_t cycle = xTaskGetTickCount();
    for (;;) {
        /* Single sensor, externally powered. Configure volatile scratchpad
         * every cycle so a sensor power cycle cannot restore 12-bit timing. */
        const uint8_t configure[] = {0xcc, 0x4e, 0, 0, 0x3f};
        const uint8_t convert[] = {0xcc, 0x44};
        const uint8_t read[] = {0xcc, 0xbe};
        esp_err_t err = command(configure, sizeof(configure));
        if (err == ESP_OK) err = command(convert, sizeof(convert));
        if (err == ESP_OK) {
            portENTER_CRITICAL(&sample_lock);
            if (!latest.sample_id) latest.status = WH_SENSOR_NOT_READY;
            portEXIT_CRITICAL(&sample_lock);
            /* Conversion runs on the sensor; UDP and services keep running. */
            vTaskDelay(pdMS_TO_TICKS(200));
            uint8_t ready = 0;
            err = onewire_bus_read_bit(bus, &ready);
            if (err == ESP_OK && !ready) err = ESP_ERR_INVALID_STATE;
        }
        uint8_t scratchpad[9];
        if (err == ESP_OK) err = command(read, sizeof(read));
        if (err == ESP_OK) err = onewire_bus_read_bytes(bus, scratchpad, sizeof(scratchpad));
        int32_t t = 0;
        if (err == ESP_OK && !wh_ds18b20_decode(scratchpad, &t)) err = ESP_ERR_INVALID_CRC;
        portENTER_CRITICAL(&sample_lock);
        if (err == ESP_OK) {
            latest.sample_id++;
            latest.temperature_mC = t;
            latest.acquired_us = esp_timer_get_time();
            latest.status = WH_SENSOR_VALID;
        } else {
            latest.status = err == ESP_ERR_INVALID_CRC ? WH_SENSOR_CRC_ERROR :
                err == ESP_ERR_INVALID_STATE ? WH_SENSOR_NOT_READY : WH_SENSOR_DISCONNECTED;
        }
        portEXIT_CRITICAL(&sample_lock);
        if (err != ESP_OK) wh_platform_event("sensor_acquisition_failed", err);
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        xTaskDelayUntil(&cycle, pdMS_TO_TICKS(500));
    }
}

esp_err_t wh_sensor_start(void)
{
    onewire_bus_config_t config = {.bus_gpio_num = CONFIG_WASMHOME_SENSOR_GPIO};
    onewire_bus_rmt_config_t rmt = {.max_rx_bytes = 10};
    esp_err_t err = onewire_new_bus_rmt(&config, &rmt, &bus);
    if (err != ESP_OK) return err;
    if (xTaskCreate(sensor_task, "wh_sensor", 4096, NULL, 5, NULL) != pdPASS) {
        onewire_bus_del(bus);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

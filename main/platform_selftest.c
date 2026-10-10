#include "sdkconfig.h"
#if defined(CONFIG_WASMHOME_PLATFORM_SELFTEST)
#include "platform.h"
#include "driver/uart.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

/* The endpoint comes only from ignored local sdkconfig. Serial supplies no URL.
 * The dedicated task never delays the subscribed diagnostic/sensor/UDP tasks. */
static portMUX_TYPE diagnostic_lock = portMUX_INITIALIZER_UNLOCKED;
static bool diagnostic_busy;

/* Both serial commands reserve the same state atomically before starting work.
 * A watchdog reservation persists until reboot; an LXC reservation is released
 * only when its worker finishes or task creation fails. */
static bool diagnostic_claim(void)
{
    taskENTER_CRITICAL(&diagnostic_lock);
    bool accepted = !diagnostic_busy;
    if (accepted) diagnostic_busy = true;
    taskEXIT_CRITICAL(&diagnostic_lock);
    return accepted;
}

static void diagnostic_release(void)
{
    taskENTER_CRITICAL(&diagnostic_lock);
    diagnostic_busy = false;
    taskEXIT_CRITICAL(&diagnostic_lock);
}

static int lxc_ready(int fd, bool writing, int64_t deadline)
{
    int64_t remaining = deadline - esp_timer_get_time();
    if (remaining <= 0) return 0;
    struct timeval timeout = {.tv_sec = remaining / 1000000, .tv_usec = remaining % 1000000};
    fd_set set;
    FD_ZERO(&set);
    FD_SET(fd, &set);
    return select(fd + 1, writing ? NULL : &set, writing ? &set : NULL, NULL, &timeout);
}

static void lxc_probe_task(void *arg)
{
    (void)arg;
    const int64_t started = esp_timer_get_time();
    const int64_t deadline = started + 2500000;
    const char *result = "socket_error";
    int fd = -1;
    char local[INET_ADDRSTRLEN] = "";
    char nonce[65] = {0};
    char request[192], expected[256], received[512];
    struct sockaddr_in endpoint = {.sin_family = AF_INET, .sin_port = htons(18080)};
    if (inet_pton(AF_INET, CONFIG_WASMHOME_LXC_PROBE_IPV4, &endpoint.sin_addr) != 1) {
        result = "disabled_or_invalid_config";
        goto done;
    }
    for (int i = 0; i < 8; ++i) snprintf(nonce + i * 8, 9, "%08" PRIx32, esp_random());
    fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd < 0 || fcntl(fd, F_SETFL, O_NONBLOCK) < 0) goto done;
    int connected = connect(fd, (struct sockaddr *)&endpoint, sizeof(endpoint));
    if (connected < 0 && errno != EINPROGRESS) goto done;
    if (connected < 0) {
        int ready = lxc_ready(fd, true, deadline);
        if (ready <= 0) { result = ready == 0 ? "timeout" : "socket_error"; goto done; }
        int error = 0;
        socklen_t length = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &length) < 0 || error) goto done;
    }
    struct sockaddr_in own;
    socklen_t own_length = sizeof(own);
    if (getsockname(fd, (struct sockaddr *)&own, &own_length) < 0 ||
        !inet_ntop(AF_INET, &own.sin_addr, local, sizeof(local))) goto done;
    int request_size = snprintf(request, sizeof(request),
        "GET /wasmhome-lxc/%s HTTP/1.0\r\nHost: %s\r\n\r\n", nonce, CONFIG_WASMHOME_LXC_PROBE_IPV4);
    if (request_size < 0 || request_size >= sizeof(request)) { result = "invalid_request"; goto done; }
    for (int sent = 0; sent < request_size;) {
        int ready = lxc_ready(fd, true, deadline);
        if (ready <= 0) { result = ready == 0 ? "timeout" : "socket_error"; goto done; }
        int count = send(fd, request + sent, request_size - sent, 0);
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (count <= 0) goto done;
        sent += count;
    }
    size_t used = 0;
    for (;;) {
        int ready = lxc_ready(fd, false, deadline);
        if (ready <= 0) { result = ready == 0 ? "timeout" : "socket_error"; goto done; }
        int count = recv(fd, received + used, sizeof(received) - used, 0);
        if (count < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (count < 0) goto done;
        if (count == 0) break;
        used += count;
        if (used == sizeof(received)) { result = "oversized_response"; goto done; }
    }
    char body[96];
    int body_size = snprintf(body, sizeof(body), "WH_LXC_OK %s %s\n", nonce, local);
    int expected_size = snprintf(expected, sizeof(expected),
        "HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s",
        body_size, body);
    result = expected_size > 0 && expected_size < sizeof(expected) &&
             used == (size_t)expected_size && !memcmp(received, expected, used) ? "ok" : "response_mismatch";
done:
    if (fd >= 0) close(fd);
    printf("WASMHOME_LXC,{\"result\":\"%s\",\"server_ipv4\":\"%s\",\"server_port\":18080,"
           "\"local_ipv4\":\"%s\",\"nonce\":\"%s\",\"elapsed_ms\":%" PRId64 "}\n",
           result, CONFIG_WASMHOME_LXC_PROBE_IPV4, local, nonce, (esp_timer_get_time() - started) / 1000);
    diagnostic_release();
    vTaskDelete(NULL);
}

static void lxc_probe_start(void)
{
    if (!diagnostic_claim()) { wh_platform_event("lxc_probe_busy", ESP_ERR_INVALID_STATE); return; }
    if (xTaskCreate(lxc_probe_task, "wh_lxc", 4096, NULL, 3, NULL) != pdPASS) {
        diagnostic_release();
        wh_platform_event("lxc_probe_create_failed", ESP_ERR_NO_MEM);
    }
}

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
    bool command_overflow = false;
    for (;;) {
        uint8_t incoming[16];
        int count = uart_read_bytes(CONFIG_ESP_CONSOLE_UART_NUM, incoming, sizeof(incoming), 0);
        for (int i = 0; i < count; ++i) {
            if (incoming[i] == '\n') {
                command[command_length] = 0;
                if (!command_overflow && !strcmp(command, "WH_WDT")) {
                    if (!diagnostic_claim()) {
                        wh_platform_event("selftest_watchdog_busy", ESP_ERR_INVALID_STATE);
                    } else {
                        wh_platform_event("selftest_watchdog_triggered", ESP_OK);
                        /* Deliberately miss this subscribed task's feed deadline.
                         * This command exists only in the diagnostic firmware. */
                        vTaskDelay(pdMS_TO_TICKS(10000));
                    }
                }
                if (!command_overflow && !strcmp(command, "WH_LXC")) lxc_probe_start();
                command_length = 0;
                command_overflow = false;
            } else if (incoming[i] != '\r') {
                if (command_length < sizeof(command) - 1) command[command_length++] = incoming[i];
                else command_overflow = true;
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

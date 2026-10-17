#include "platform.h"
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_heap_caps.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#define NETWORK_READY BIT0
#define UART_HEARTBEAT_TASK_STACK_BYTES 3072
#define UART_HEARTBEAT_TASK_PRIORITY 1
#define UART_HEARTBEAT_RECORD "WASMHOME_UART_HEARTBEAT,v1\n"
static uint64_t boot_id;
static EventGroupHandle_t network;
static SemaphoreHandle_t transport_lock;
static struct sockaddr_in observer;
static int udp_socket = -1;
static uint64_t tx_sequence[256];
static wh_request_handler_t request_handler;
static uint32_t request_handler_in_flight;
static bool started;
#if defined(WASMHOME_SCENARIO_NATIVE)
static const char *scenario = "native";
#elif defined(WASMHOME_SCENARIO_MODULE_INTERP)
static const char *scenario = "module-interp";
#elif defined(WASMHOME_SCENARIO_MODULE_AOT)
static const char *scenario = "module-aot";
#elif defined(WASMHOME_SCENARIO_INTERP)
static const char *scenario = "interp";
#else
static const char *scenario = "aot";
#endif

uint64_t wh_platform_boot_id(void) { return boot_id; }

void wh_platform_event(const char *event, esp_err_t status)
{
    /* Names are internal literals. Never pass credentials or remote text. */
    printf("WASMHOME_EVENT,{\"event\":\"%s\",\"scenario\":\"%s\",\"boot_id\":\"%016" PRIx64
           "\",\"monotonic_us\":%" PRId64 ",\"status\":%d,\"heap_internal\":%u}\n",
           event, scenario, boot_id, esp_timer_get_time(), (int)status,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
}

static void uart_heartbeat_task(void *argument)
{
    (void)argument;
    const TickType_t interval = pdMS_TO_TICKS(CONFIG_WASMHOME_UART_HEARTBEAT_INTERVAL_MS);
    TickType_t cycle = xTaskGetTickCount();
    for (;;) {
        vTaskDelayUntil(&cycle, interval);
        (void)fputs(UART_HEARTBEAT_RECORD, stdout);
        (void)fflush(stdout);
    }
}

esp_err_t wh_gpio_apply(uint8_t requested, uint8_t *observed)
{
    if (!started || requested > 1 || !observed) return ESP_ERR_INVALID_ARG;
    esp_err_t err = gpio_set_level(CONFIG_WASMHOME_CONTROL_GPIO, requested);
    if (err != ESP_OK) return err;
    esp_rom_delay_us(5);
    *observed = (uint8_t)gpio_get_level(CONFIG_WASMHOME_LOOPBACK_GPIO);
    return ESP_OK;
}

esp_err_t wh_observation_set_handler(wh_request_handler_t handler)
{
    if (!transport_lock) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(transport_lock, portMAX_DELAY);
    request_handler = handler;
    xSemaphoreGive(transport_lock);
    if (!handler) {
        for (;;) {
            xSemaphoreTake(transport_lock, portMAX_DELAY);
            const uint32_t in_flight = request_handler_in_flight;
            xSemaphoreGive(transport_lock);
            if (in_flight == 0) break;
            vTaskDelay(1);
        }
    }
    return ESP_OK;
}

esp_err_t wh_observation_send(uint8_t type, const uint8_t attempt[16],
                              const uint8_t *body, size_t body_size)
{
    if (type != WH_TELEMETRY && type != WH_CONTROL_RESULT && type != WH_UPDATE_ACCEPTANCE)
        return ESP_ERR_INVALID_ARG;
    if (!transport_lock) return ESP_ERR_INVALID_STATE;
    uint8_t data[68];
    xSemaphoreTake(transport_lock, portMAX_DELAY);
    esp_err_t err = ESP_ERR_INVALID_STATE;
    if (udp_socket >= 0 && (xEventGroupGetBits(network) & NETWORK_READY) && tx_sequence[type] != UINT64_MAX) {
        uint64_t next = tx_sequence[type] + 1;
        size_t n = wh_wire_encode(data, sizeof(data), type, attempt, next,
                                  boot_id, esp_timer_get_time(), body, body_size);
        if (!n) err = ESP_ERR_INVALID_ARG;
        else {
            tx_sequence[type] = next;
            int result = sendto(udp_socket, data, n, 0, (struct sockaddr *)&observer, sizeof(observer));
            err = result == (int)n ? ESP_OK : ESP_FAIL;
        }
    }
    xSemaphoreGive(transport_lock);
    if (err == ESP_FAIL) wh_platform_event("udp_send_failed", err);
    return err;
}

static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *disconnected = data;
        printf("WASMHOME_WIFI,{\"reason\":%u}\n", disconnected->reason);
        xEventGroupClearBits(network, NETWORK_READY);
        wh_platform_event("wifi_disconnected", ESP_FAIL);
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(network, NETWORK_READY);
        wh_platform_event("wifi_ip_acquired", ESP_OK);
        ip_event_got_ip_t *ip = data;
        printf("WASMHOME_NETWORK,{\"ip\":\"" IPSTR "\",\"gateway\":\"" IPSTR "\"}\n",
               IP2STR(&ip->ip_info.ip), IP2STR(&ip->ip_info.gw));
    } else if (base == IP_EVENT && id == IP_EVENT_STA_LOST_IP) xEventGroupClearBits(network, NETWORK_READY);
}

static void udp_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    uint8_t data[WH_MAX_DATAGRAM + 1];
    for (;;) {
        ESP_ERROR_CHECK(esp_task_wdt_reset());
        if (!(xEventGroupGetBits(network) & NETWORK_READY)) {
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        int fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
        struct timeval timeout = {.tv_sec = 0, .tv_usec = 100000};
        struct sockaddr_in local = {.sin_family = AF_INET,
            .sin_port = htons(CONFIG_WASMHOME_UDP_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
        if (fd < 0 || setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) ||
            setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) ||
            bind(fd, (struct sockaddr *)&local, sizeof(local))) {
            if (fd >= 0) close(fd);
            wh_platform_event("udp_open_failed", ESP_FAIL);
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }
        xSemaphoreTake(transport_lock, portMAX_DELAY);
        udp_socket = fd;
        xSemaphoreGive(transport_lock);
        wh_platform_event("udp_bound", ESP_OK);
        while (xEventGroupGetBits(network) & NETWORK_READY) {
            struct sockaddr_in peer;
            socklen_t peer_size = sizeof(peer);
            int n = recvfrom(fd, data, sizeof(data), 0, (struct sockaddr *)&peer, &peer_size);
            ESP_ERROR_CHECK(esp_task_wdt_reset());
            if (n < 0) {
                if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) continue;
                wh_platform_event("udp_receive_failed", ESP_FAIL);
                break;
            }
            if (peer.sin_addr.s_addr != observer.sin_addr.s_addr || peer.sin_port != observer.sin_port ||
                !wh_wire_valid(data, (size_t)n) ||
                (data[5] != WH_CONTROL_REQUEST && data[5] != WH_UPDATE_TRIGGER)) {
                wh_platform_event("udp_discarded", ESP_ERR_INVALID_ARG);
                continue;
            }
            if (data[5] == WH_CONTROL_REQUEST && wh_read_be(data + 32, 8) != boot_id) {
                wh_platform_event("control_wrong_boot", ESP_ERR_INVALID_STATE);
                continue;
            }
            xSemaphoreTake(transport_lock, portMAX_DELAY);
            wh_request_handler_t handler = request_handler;
            if (handler) request_handler_in_flight++;
            xSemaphoreGive(transport_lock);
            if (handler) {
                handler(data, (size_t)n);
                xSemaphoreTake(transport_lock, portMAX_DELAY);
                if (request_handler_in_flight) request_handler_in_flight--;
                xSemaphoreGive(transport_lock);
            } else {
                wh_platform_event("service_not_installed", ESP_ERR_NOT_SUPPORTED);
            }
        }
        xSemaphoreTake(transport_lock, portMAX_DELAY);
        udp_socket = -1;
        close(fd);
        xSemaphoreGive(transport_lock);
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

esp_err_t wh_platform_start(void)
{
    if (started) return ESP_ERR_INVALID_STATE;
    started = true;
    do { esp_fill_random(&boot_id, sizeof(boot_id)); } while (!boot_id);
    wh_platform_event("boot_observed", (esp_err_t)esp_reset_reason());
    if (pdMS_TO_TICKS(CONFIG_WASMHOME_UART_HEARTBEAT_INTERVAL_MS) == 0)
        return ESP_ERR_INVALID_ARG;
    if (xTaskCreate(uart_heartbeat_task, "wh_uart_heartbeat",
                    UART_HEARTBEAT_TASK_STACK_BYTES, NULL,
                    UART_HEARTBEAT_TASK_PRIORITY, NULL) != pdPASS)
        return ESP_ERR_NO_MEM;
    network = xEventGroupCreate();
    transport_lock = xSemaphoreCreateMutex();
    if (!network || !transport_lock) return ESP_ERR_NO_MEM;
    gpio_config_t output = {.pin_bit_mask = 1ULL << CONFIG_WASMHOME_CONTROL_GPIO, .mode = GPIO_MODE_OUTPUT};
    gpio_config_t input = {.pin_bit_mask = 1ULL << CONFIG_WASMHOME_LOOPBACK_GPIO,
        .mode = GPIO_MODE_INPUT, .pull_down_en = GPIO_PULLDOWN_ENABLE};
    ESP_ERROR_CHECK(gpio_config(&output));
    ESP_ERROR_CHECK(gpio_set_level(CONFIG_WASMHOME_CONTROL_GPIO, 0));
    ESP_ERROR_CHECK(gpio_config(&input));
    ESP_ERROR_CHECK(wh_sensor_start());
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    if (!esp_netif_create_default_wifi_sta()) return ESP_ERR_NO_MEM;
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL));
    wifi_config_t config = {0};
    size_t ssid_size = strlen(CONFIG_WASMHOME_WIFI_SSID);
    size_t password_size = strlen(CONFIG_WASMHOME_WIFI_PASSWORD);
    if (!ssid_size || ssid_size > sizeof(config.sta.ssid) || password_size > sizeof(config.sta.password))
        return ESP_ERR_INVALID_ARG;
    memcpy(config.sta.ssid, CONFIG_WASMHOME_WIFI_SSID, ssid_size);
    memcpy(config.sta.password, CONFIG_WASMHOME_WIFI_PASSWORD, password_size);
    config.sta.threshold.authmode = password_size ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    observer.sin_family = AF_INET;
    observer.sin_port = htons(CONFIG_WASMHOME_UDP_PORT);
    if (!strlen(CONFIG_WASMHOME_OBSERVER_IPV4)) wh_platform_event("udp_disabled_no_observer", ESP_ERR_INVALID_STATE);
    else {
        if (inet_pton(AF_INET, CONFIG_WASMHOME_OBSERVER_IPV4, &observer.sin_addr) != 1) return ESP_ERR_INVALID_ARG;
        if (xTaskCreate(udp_task, "wh_udp", 6144, NULL, 5, NULL) != pdPASS) return ESP_ERR_NO_MEM;
    }
    wh_platform_event("platform_initialized", ESP_OK);
    return ESP_OK;
}

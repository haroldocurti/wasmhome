#include "module_slot_store.h"

#include "esp_partition.h"
#include "mbedtls/constant_time.h"
#include "mbedtls/sha256.h"
#include "sdkconfig.h"

#include <string.h>

#define WH_MODULE_SLOT_ERASE_BYTES 0x1000u
#define WH_MODULE_SLOT_READBACK_CHUNK 1024u

typedef struct {
    const esp_partition_t *partition;
    uint8_t scenario;
    uint32_t slot_id;
    uint32_t expected_size;
    uint32_t bytes_written;
    bool writing;
    bool erased;
    bool erase_attempted;
} slot_writer_t;

static slot_writer_t writer;

static const char *
slot_label(uint8_t scenario, uint32_t slot_id)
{
    if (slot_id >= WH_MODULE_SLOT_COUNT) return NULL;
    if (scenario == 2)
        return slot_id == WH_MODULE_SLOT_ID_A ? "interp_slot_a" : "interp_slot_b";
    if (scenario == 3)
        return slot_id == WH_MODULE_SLOT_ID_A ? "aot_slot_a" : "aot_slot_b";
    return NULL;
}

static const esp_partition_t *
find_slot(uint8_t scenario, uint32_t slot_id)
{
    const char *label = slot_label(scenario, slot_id);
    if (!label) return NULL;
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                    ESP_PARTITION_SUBTYPE_ANY, label);
}

static bool
size_fits(const esp_partition_t *partition, uint32_t size)
{
    return partition && size > 0 &&
           size <= CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES &&
           size <= partition->size;
}

esp_err_t
wh_module_slot_store_init(void)
{
    static const char *const labels[] = {
        "interp_slot_a", "interp_slot_b", "aot_slot_a", "aot_slot_b",
    };
    for (size_t i = 0; i < sizeof(labels) / sizeof(labels[0]); ++i) {
        const esp_partition_t *partition = esp_partition_find_first(
            ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, labels[i]);
        if (!partition || partition->size < CONFIG_WASMHOME_MODULE_MAX_ARTIFACT_BYTES ||
            partition->address % WH_MODULE_SLOT_ERASE_BYTES != 0 ||
            partition->size % WH_MODULE_SLOT_ERASE_BYTES != 0)
            return ESP_ERR_NOT_FOUND;
    }
    return ESP_OK;
}

esp_err_t
wh_module_slot_store_begin(uint8_t scenario, uint32_t slot_id,
                           uint32_t expected_size)
{
    if (writer.writing) return ESP_ERR_INVALID_STATE;
    const esp_partition_t *partition = find_slot(scenario, slot_id);
    if (!size_fits(partition, expected_size) ||
        partition->size % WH_MODULE_SLOT_ERASE_BYTES != 0)
        return ESP_ERR_INVALID_ARG;

    writer = (slot_writer_t){
        .partition = partition,
        .scenario = scenario,
        .slot_id = slot_id,
        .expected_size = expected_size,
        .writing = true,
    };
    return ESP_OK;
}

esp_err_t
wh_module_slot_store_write(const uint8_t *data, size_t size)
{
    if (!writer.writing || !writer.partition || (!data && size) || size == 0 ||
        writer.bytes_written > writer.expected_size ||
        size > writer.expected_size - writer.bytes_written)
        return ESP_ERR_INVALID_ARG;
    if (!writer.erased) {
        writer.erase_attempted = true;
        const esp_err_t erase_status = esp_partition_erase_range(
            writer.partition, 0, writer.partition->size);
        if (erase_status != ESP_OK) return erase_status;
        writer.erased = true;
    }
    const esp_err_t result = esp_partition_write(
        writer.partition, writer.bytes_written, data, size);
    if (result == ESP_OK) writer.bytes_written += (uint32_t)size;
    return result;
}

esp_err_t
wh_module_slot_store_finish(uint8_t scenario, uint32_t slot_id,
                            uint32_t expected_size,
                            const uint8_t expected_sha256[32])
{
    if (!writer.writing || !expected_sha256 || !writer.partition ||
        scenario != writer.scenario || slot_id != writer.slot_id ||
        expected_size != writer.expected_size ||
        writer.bytes_written != writer.expected_size)
        return ESP_ERR_INVALID_STATE;

    uint8_t buffer[WH_MODULE_SLOT_READBACK_CHUNK];
    uint8_t digest[32] = {0};
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    int status = mbedtls_sha256_starts(&sha, 0);
    uint32_t offset = 0;
    while (status == 0 && offset < expected_size) {
        size_t chunk = expected_size - offset;
        if (chunk > sizeof(buffer)) chunk = sizeof(buffer);
        const esp_err_t read_status = esp_partition_read(
            writer.partition, offset, buffer, chunk);
        if (read_status != ESP_OK) {
            status = -1;
            break;
        }
        status = mbedtls_sha256_update(&sha, buffer, chunk);
        offset += (uint32_t)chunk;
    }
    if (status == 0) status = mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);
    memset(buffer, 0, sizeof(buffer));

    if (status != 0 || mbedtls_ct_memcmp(digest, expected_sha256,
                                         sizeof(digest)) != 0) {
        memset(digest, 0, sizeof(digest));
        return ESP_ERR_INVALID_CRC;
    }
    memset(digest, 0, sizeof(digest));
    writer = (slot_writer_t){0};
    return ESP_OK;
}

esp_err_t
wh_module_slot_store_abort(void)
{
    esp_err_t err = ESP_OK;
    /* The caller reserves only the inactive candidate. Erase the whole slot
       after any erase attempt, including a failed first erase, then read back
       every byte so a failed invalidation cannot be mistaken for success. */
    if (writer.writing && writer.erase_attempted && writer.partition) {
        err = esp_partition_erase_range(writer.partition, 0,
                                        writer.partition->size);
        uint8_t verify[WH_MODULE_SLOT_READBACK_CHUNK];
        for (uint32_t offset = 0; err == ESP_OK &&
             offset < writer.partition->size;) {
            size_t amount = writer.partition->size - offset;
            if (amount > sizeof(verify)) amount = sizeof(verify);
            err = esp_partition_read(writer.partition, offset, verify, amount);
            for (size_t i = 0; err == ESP_OK && i < amount; ++i)
                if (verify[i] != 0xff) err = ESP_ERR_INVALID_CRC;
            offset += (uint32_t)amount;
        }
        memset(verify, 0, sizeof(verify));
    }
    writer = (slot_writer_t){0};
    return err;
}

esp_err_t
wh_module_slot_store_read(uint8_t scenario, uint32_t slot_id, uint32_t size,
                          uint8_t *destination, size_t destination_size)
{
    const esp_partition_t *partition = find_slot(scenario, slot_id);
    if (!size_fits(partition, size) || !destination || destination_size < size)
        return ESP_ERR_INVALID_ARG;
    return esp_partition_read(partition, 0, destination, size);
}

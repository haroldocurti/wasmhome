#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define WH_SIGNED_UPDATE_MAX_ENVELOPE_BYTES 4096u
#define WH_SIGNED_UPDATE_MAX_CAMPAIGN_BYTES 64u
#define WH_SIGNED_UPDATE_MAX_COMPONENT_BYTES 32u
#define WH_SIGNED_UPDATE_MAX_RELEASE_BYTES 32u
#define WH_SIGNED_UPDATE_MAX_TOOLCHAIN_BYTES 128u
#define WH_SIGNED_UPDATE_NO_ACTIVE_SLOT UINT32_MAX

typedef enum {
    WH_SIGNED_UPDATE_FAILURE_NONE = 0,
    WH_SIGNED_UPDATE_FAILURE_ARGUMENT,
    WH_SIGNED_UPDATE_FAILURE_TRANSPORT,
    WH_SIGNED_UPDATE_FAILURE_HTTP,
    WH_SIGNED_UPDATE_FAILURE_ENVELOPE,
    WH_SIGNED_UPDATE_FAILURE_SIGNATURE,
    WH_SIGNED_UPDATE_FAILURE_POLICY,
    WH_SIGNED_UPDATE_FAILURE_PERSISTENCE,
    WH_SIGNED_UPDATE_FAILURE_PAYLOAD_SIZE,
    WH_SIGNED_UPDATE_FAILURE_PAYLOAD_HASH,
    WH_SIGNED_UPDATE_FAILURE_SINK,
} wh_signed_update_failure_t;

typedef struct {
    uint8_t scenario;
    uint8_t artifact_kind;
    char campaign_id[WH_SIGNED_UPDATE_MAX_CAMPAIGN_BYTES];
    char component[WH_SIGNED_UPDATE_MAX_COMPONENT_BYTES];
    char release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    char target[16];
    char toolchain_id[WH_SIGNED_UPDATE_MAX_TOOLCHAIN_BYTES];
    uint64_t sequence;
    uint32_t payload_size;
    uint32_t base_api_version;
    uint8_t payload_sha256[32];
    uint8_t envelope_sha256[32];
    uint8_t manifest_sha256[32];
    uint8_t key_id[8];
} wh_signed_update_manifest_t;

typedef enum {
    WH_SIGNED_UPDATE_PHASE_ACCEPTED = 0,
    WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOAD_STARTED,
    WH_SIGNED_UPDATE_PHASE_MANIFEST_DOWNLOADED,
    WH_SIGNED_UPDATE_PHASE_SIGNATURE_VERIFIED,
    WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_STARTED,
    WH_SIGNED_UPDATE_PHASE_PAYLOAD_DOWNLOAD_PROGRESS,
    WH_SIGNED_UPDATE_PHASE_PAYLOAD_VERIFIED,
    WH_SIGNED_UPDATE_PHASE_STAGED,
    WH_SIGNED_UPDATE_PHASE_ACTIVATING,
    WH_SIGNED_UPDATE_PHASE_BOOT_PENDING,
    WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_PASSED,
    WH_SIGNED_UPDATE_PHASE_HEALTH_CHECK_FAILED,
    WH_SIGNED_UPDATE_PHASE_ROLLBACK_DETECTED,
    WH_SIGNED_UPDATE_PHASE_ACTIVATION_INTERRUPTED,
    WH_SIGNED_UPDATE_PHASE_FLOOR_COMMITTED,
    WH_SIGNED_UPDATE_PHASE_REBOOT_REQUESTED,
    WH_SIGNED_UPDATE_PHASE_CANDIDATE_LOADED,
    WH_SIGNED_UPDATE_PHASE_CANDIDATE_INSTANTIATED,
    WH_SIGNED_UPDATE_PHASE_CANDIDATE_HEALTH_CHECK,
    WH_SIGNED_UPDATE_PHASE_CONTROL_ACTIVATED,
    WH_SIGNED_UPDATE_PHASE_PREVIOUS_CONTROL_RELEASED,
    WH_SIGNED_UPDATE_PHASE_UPDATE_FAILED,
} wh_signed_update_phase_t;

typedef enum {
    WH_SIGNED_UPDATE_PENDING_NONE = 0,
    WH_SIGNED_UPDATE_PENDING_DOWNLOADING = 1,
    WH_SIGNED_UPDATE_PENDING_STAGED = 2,
    WH_SIGNED_UPDATE_PENDING_ACTIVATING = 3,
} wh_signed_update_pending_phase_t;

typedef struct {
    wh_signed_update_phase_t phase;
    wh_signed_update_failure_t failure;
    const uint8_t *attempt_id; /* 16 bytes, NULL only for boot reconciliation. */
    uint64_t origin_boot_id; /* Boot that accepted the trigger. */
    uint64_t accepted_floor; /* Read-back floor after durable commit, otherwise 0. */
    uint64_t ledger_readback_sequence; /* Read-back active sequence, otherwise 0. */
    uint8_t scenario;
    uint8_t component_id; /* Wire component ID; currently CONTROL (1). */
    uint16_t target_version;
    uint64_t sequence;
    const char *release_version; /* Signed manifest label (for example, "v2"). */
    uint32_t payload_bytes_expected;
    uint32_t payload_bytes_received;
    uint32_t current_task_stack_high_water_bytes;
    uint64_t elapsed_us;
    const char *elapsed_scope; /* "attempt" before reboot, "boot" after reboot. */
    const uint8_t *payload_sha256; /* 32 bytes when verified, otherwise NULL. */
    const uint8_t *running_sha256; /* 32 bytes after native-image readback, otherwise NULL. */
    const uint8_t *manifest_sha256; /* 32 bytes after COSE parsing, otherwise NULL. */
    uint64_t telemetry_generation; /* Optional module-swap correlation. */
    const uint8_t *telemetry_sha256; /* Optional 32-byte module identity. */
    uint64_t telemetry_runtime_busy_drops;
    bool health_reconciled; /* true only when a prior successful gate is being reconciled */
    uint64_t control_generation; /* Optional active-control snapshot. */
    uint8_t control_module_version; /* 0 when no control snapshot applies. */
    const uint8_t *control_sha256; /* Optional 32-byte control artifact identity. */
} wh_signed_update_event_t;

typedef void (*wh_signed_update_event_fn)(void *context,
                                          wh_signed_update_event_t *event);

typedef struct {
    void *context;
    /* Begin a candidate sink only after signature, identity, sequence and
       declared-size policy have all passed. Return a stable slot identifier. */
    esp_err_t (*begin)(void *context, const wh_signed_update_manifest_t *manifest,
                       uint32_t *slot_id);
    esp_err_t (*write)(void *context, const uint8_t *data, size_t size);
    /* Called only after exact payload length and SHA-256 have been verified.
       Must make the candidate durable and return only after readback succeeds. */
    esp_err_t (*finish)(void *context, const wh_signed_update_manifest_t *manifest,
                        uint32_t *slot_id);
    void (*abort)(void *context);
} wh_signed_update_sink_t;

typedef struct {
    bool pending;
    uint8_t scenario;
    char component[WH_SIGNED_UPDATE_MAX_COMPONENT_BYTES];
    uint64_t accepted_floor;
    uint64_t active_sequence;
    uint64_t pending_sequence;
    uint32_t active_slot;
    uint32_t pending_slot;
    uint32_t active_payload_size;
    uint16_t active_target_version;
    uint16_t pending_target_version;
    uint32_t pending_payload_size;
    uint8_t pending_attempt_id[16];
    uint64_t origin_boot_id;
    char pending_release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint8_t active_payload_sha256[32];
    uint8_t active_manifest_sha256[32];
    char active_release_version[WH_SIGNED_UPDATE_MAX_RELEASE_BYTES];
    uint8_t active_attempt_id[16];
    uint64_t active_origin_boot_id;
    uint8_t pending_payload_sha256[32];
    uint8_t pending_manifest_sha256[32];
    uint8_t pending_phase;
} wh_signed_update_state_t;

const char *wh_signed_update_phase_name(wh_signed_update_phase_t phase);
const char *wh_signed_update_failure_name(wh_signed_update_failure_t failure);
void wh_signed_update_emit_event(const wh_signed_update_event_t *event);

/* Release labels map only to the immutable v1/v2 paths in the signed #15
   package. No URL or path is accepted from the UDP trigger. */
esp_err_t wh_signed_update_download(uint8_t scenario, const char *release_version,
                                    uint64_t expected_sequence,
                                    const uint8_t attempt_id[16],
                                    const wh_signed_update_sink_t *sink,
                                    wh_signed_update_event_fn event_fn,
                                    void *event_context,
                                    wh_signed_update_manifest_t *manifest_out,
                                    wh_signed_update_failure_t *failure_out);

/* Stage writes pending before payload I/O. Activation adapters record the
   chosen persistent slot before changing the in-memory selection. commit()
   changes active_slot and accepted_floor together, then verifies readback. */
esp_err_t wh_signed_update_mark_activating(const wh_signed_update_manifest_t *manifest,
                                           uint32_t slot_id);
esp_err_t wh_signed_update_mark_staged(const wh_signed_update_manifest_t *manifest,
                                       uint32_t slot_id);
esp_err_t wh_signed_update_commit(const wh_signed_update_manifest_t *manifest,
                                  uint32_t slot_id);
esp_err_t wh_signed_update_abort(const wh_signed_update_manifest_t *manifest);
esp_err_t wh_signed_update_get_state(uint8_t scenario, const char *component,
                                     const char *campaign_id,
                                     wh_signed_update_state_t *state_out);

/* Native OTA calls this on each boot after NVS initialization. If the image is
   still pending verification, it leaves rollback armed. If the old slot runs,
   it clears the abandoned pending record and records the rollback. */
esp_err_t wh_signed_update_reconcile_native_boot(void);
esp_err_t wh_signed_update_mark_native_healthy(void);

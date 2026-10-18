if(NOT DEFINED EXPERIMENT_SCENARIO)
    if(DEFINED ENV{WASMHOME_EXPERIMENT_SCENARIO})
        set(EXPERIMENT_SCENARIO "$ENV{WASMHOME_EXPERIMENT_SCENARIO}")
    else()
        set(EXPERIMENT_SCENARIO "native")
    endif()
endif()

set(SCENARIO_SOURCE "main.c" "platform.c" "sensor.c" "observation_wire.c" "platform_selftest.c" "observer_selftest.c")
set(SCENARIO_REQUIRES "esp_timer" "esp_wifi" "esp_netif" "esp_event" "nvs_flash" "driver" "esp_system" "onewire_bus")
set(UPDATE_EMBED_TXTFILES)
set(UPDATE_EMBED_FILES)

if(CONFIG_WASMHOME_SIGNED_UPDATE)
    if(NOT CONFIG_WASMHOME_UPDATE_CA_CERT_PATH OR
       NOT EXISTS "${CONFIG_WASMHOME_UPDATE_CA_CERT_PATH}")
        message(FATAL_ERROR "WASMHOME_SIGNED_UPDATE requires a local trusted CA PEM file")
    endif()
    if(NOT CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH OR
       NOT EXISTS "${CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH}")
        message(FATAL_ERROR "WASMHOME_SIGNED_UPDATE requires a local pinned signing public DER file")
    endif()
    set(UPDATE_CA_BUNDLE "${CMAKE_CURRENT_BINARY_DIR}/wasmhome-update-ca.pem")
    set(UPDATE_SIGNING_KEY "${CMAKE_CURRENT_BINARY_DIR}/wasmhome-update-signing-key.der")
    configure_file("${CONFIG_WASMHOME_UPDATE_CA_CERT_PATH}" "${UPDATE_CA_BUNDLE}" COPYONLY)
    configure_file("${CONFIG_WASMHOME_UPDATE_SIGNING_PUBLIC_KEY_PATH}" "${UPDATE_SIGNING_KEY}" COPYONLY)
    list(APPEND UPDATE_EMBED_TXTFILES "${UPDATE_CA_BUNDLE}")
    list(APPEND UPDATE_EMBED_FILES "${UPDATE_SIGNING_KEY}")
    list(APPEND SCENARIO_SOURCE "signed_update.c")
endif()

if(CONFIG_WASMHOME_NATIVE_SERVICES)
    if(NOT EXPERIMENT_SCENARIO STREQUAL "native")
        message(FATAL_ERROR "WASMHOME_NATIVE_SERVICES is available only in EXPERIMENT_SCENARIO=native")
    endif()
    if(CONFIG_WASMHOME_PLATFORM_SELFTEST OR CONFIG_WASMHOME_OBSERVER_SELFTEST OR CONFIG_WASMHOME_RUN_BENCHMARK)
        message(FATAL_ERROR "WASMHOME_NATIVE_SERVICES cannot be combined with diagnostics or benchmark")
    endif()
endif()

if((EXPERIMENT_SCENARIO STREQUAL "interp" OR EXPERIMENT_SCENARIO STREQUAL "aot") AND NOT CMAKE_BUILD_EARLY_EXPANSION)
    execute_process(
        COMMAND "${PYTHON}" "${CMAKE_SOURCE_DIR}/tools/validate_build_config.py" --artifact-integrity
        RESULT_VARIABLE ARTIFACT_VALIDATION_RESULT
        OUTPUT_VARIABLE ARTIFACT_VALIDATION_OUTPUT
        ERROR_VARIABLE ARTIFACT_VALIDATION_ERROR
    )
    if(NOT ARTIFACT_VALIDATION_RESULT EQUAL 0)
        message(FATAL_ERROR "E1 artifacts are missing/stale/invalid: ${ARTIFACT_VALIDATION_ERROR}")
    endif()
    # Trigger reconfiguration after a generation failure or payload changes.
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${CMAKE_SOURCE_DIR}/artifacts/build-manifest.json"
        "${CMAKE_SOURCE_DIR}/artifacts/benchmark.wasm"
        "${CMAKE_SOURCE_DIR}/artifacts/benchmark.aot"
        "${CMAKE_SOURCE_DIR}/artifacts/benchmark.wasm.s"
        "${CMAKE_SOURCE_DIR}/artifacts/benchmark_wasm_embedded.c"
        "${CMAKE_SOURCE_DIR}/artifacts/benchmark_aot_embedded.c"
        "${CMAKE_SOURCE_DIR}/workload/benchmark.c"
        "${CMAKE_SOURCE_DIR}/workload/benchmark.h"
        "${CMAKE_SOURCE_DIR}/tools/validate_build_config.py"
        "${CMAKE_SOURCE_DIR}/tools/embed_artifact.py"
        "${CMAKE_SOURCE_DIR}/runtime-port/lock.json"
        "${CMAKE_SOURCE_DIR}/runtime-port/idf_component.yml"
        "${CMAKE_SOURCE_DIR}/runtime-port/esp-idf.patch"
        "${CMAKE_SOURCE_DIR}/tools/runtime_package.py"
        "${CMAKE_SOURCE_DIR}/tools/evidence_json.py"
        "${CMAKE_SOURCE_DIR}/tools/toolchain_provenance.py"
        "${CMAKE_SOURCE_DIR}/tools/generate_artifacts.py"
        "${CMAKE_SOURCE_DIR}/tools/build-artifacts.ps1"
        "${CMAKE_SOURCE_DIR}/tools/record_wamrc_build.py"
        "${CMAKE_SOURCE_DIR}/build-runtime-port/component/wasm-micro-runtime/.wasmhome-runtime.json"
    )
endif()

if(EXPERIMENT_SCENARIO STREQUAL "native")
    list(APPEND SCENARIO_SOURCE "../workload/benchmark.c")
    set(SCENARIO_DEFINE WASMHOME_SCENARIO_NATIVE=1)
elseif(EXPERIMENT_SCENARIO STREQUAL "interp")
    if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark.wasm")
        message(FATAL_ERROR "Run tools/build-artifacts.ps1 before the interpreted build")
    endif()
    if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark_wasm_embedded.c")
        message(FATAL_ERROR "Embedded Wasm source is missing; rerun tools/build-artifacts.ps1")
    endif()
    list(APPEND SCENARIO_REQUIRES "wasm-micro-runtime")
    set(SCENARIO_DEFINE WASMHOME_SCENARIO_INTERP=1)
    list(APPEND SCENARIO_SOURCE "../artifacts/benchmark_wasm_embedded.c")
elseif(EXPERIMENT_SCENARIO STREQUAL "aot")
    if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark.aot")
        message(FATAL_ERROR "Run tools/build-artifacts.ps1 before the AOT build")
    endif()
    if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark_aot_embedded.c")
        message(FATAL_ERROR "Embedded AOT source is missing; rerun tools/build-artifacts.ps1")
    endif()
    list(APPEND SCENARIO_REQUIRES "wasm-micro-runtime")
    set(SCENARIO_DEFINE WASMHOME_SCENARIO_AOT=1)
    list(APPEND SCENARIO_SOURCE "../artifacts/benchmark_aot_embedded.c")
elseif(EXPERIMENT_SCENARIO STREQUAL "module-interp" OR EXPERIMENT_SCENARIO STREQUAL "module-aot")
    if(NOT CMAKE_BUILD_EARLY_EXPANSION AND NOT CONFIG_WASMHOME_MODULE_SERVICES)
        message(FATAL_ERROR "The module-* scenarios require CONFIG_WASMHOME_MODULE_SERVICES=y")
    endif()
    if(EXPERIMENT_SCENARIO STREQUAL "module-interp")
        if(NOT CMAKE_BUILD_EARLY_EXPANSION AND
           (NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/service_telemetry_wasm_embedded.c" OR
            NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/service_control_v1_wasm_embedded.c" OR
            NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/service_control_v2_wasm_embedded.c"))
            message(FATAL_ERROR "Service Wasm images missing; run tools/generate_service_modules.py")
        endif()
        set(MODULE_ARTIFACT_SUFFIX "wasm")
        set(SCENARIO_DEFINE WASMHOME_SCENARIO_MODULE_INTERP=1)
    else()
        if(NOT CMAKE_BUILD_EARLY_EXPANSION AND
           (NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/service_telemetry_aot_embedded.c" OR
            NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/service_control_v1_aot_embedded.c" OR
            NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/service_control_v2_aot_embedded.c"))
            message(FATAL_ERROR "Service AOT images missing; run tools/generate_service_modules.py")
        endif()
        set(MODULE_ARTIFACT_SUFFIX "aot")
        set(SCENARIO_DEFINE WASMHOME_SCENARIO_MODULE_AOT=1)
    endif()
    if(NOT CMAKE_BUILD_EARLY_EXPANSION)
        execute_process(
            COMMAND "${PYTHON}" "${CMAKE_SOURCE_DIR}/tools/generate_service_modules.py" --verify
            RESULT_VARIABLE SERVICE_MODULE_VALIDATION_RESULT
            OUTPUT_VARIABLE SERVICE_MODULE_VALIDATION_OUTPUT
            ERROR_VARIABLE SERVICE_MODULE_VALIDATION_ERROR)
        if(NOT SERVICE_MODULE_VALIDATION_RESULT EQUAL 0)
            message(FATAL_ERROR "Issue #12 service module integrity check failed: ${SERVICE_MODULE_VALIDATION_ERROR}")
        endif()
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
            "${CMAKE_SOURCE_DIR}/artifacts/service-modules-manifest.json"
            "${CMAKE_SOURCE_DIR}/workload/service_telemetry.c"
            "${CMAKE_SOURCE_DIR}/workload/service_control.c"
            "${CMAKE_SOURCE_DIR}/tools/generate_service_modules.py"
            "${CMAKE_SOURCE_DIR}/tools/toolchain_provenance.py")
    endif()
    list(APPEND SCENARIO_REQUIRES "wasm-micro-runtime" "pthread" "mbedtls" "esp_partition")
    list(APPEND SCENARIO_SOURCE
        "module_abi_v1.c" "module_manager.c" "module_slot_store.c" "module_update.c"
        "../artifacts/service_telemetry_${MODULE_ARTIFACT_SUFFIX}_embedded.c"
        "../artifacts/service_control_v1_${MODULE_ARTIFACT_SUFFIX}_embedded.c"
        "../artifacts/service_control_v2_${MODULE_ARTIFACT_SUFFIX}_embedded.c")
    if(CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
        if(NOT CMAKE_BUILD_EARLY_EXPANSION)
            foreach(RESILIENCE_OUTPUT IN ITEMS
                    resilience_control_traps_${MODULE_ARTIFACT_SUFFIX}
                    resilience_control_traps_${MODULE_ARTIFACT_SUFFIX}_embedded.c)
                if(NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/${RESILIENCE_OUTPUT}")
                    message(FATAL_ERROR
                        "Issue #22 fixture ${RESILIENCE_OUTPUT} is missing; run tools/generate_resilience_wasm.py")
                endif()
            endforeach()
            execute_process(
                COMMAND "${PYTHON}" "${CMAKE_SOURCE_DIR}/tools/generate_resilience_wasm.py" --verify
                RESULT_VARIABLE RESILIENCE_FIXTURE_VALIDATION_RESULT
                OUTPUT_VARIABLE RESILIENCE_FIXTURE_VALIDATION_OUTPUT
                ERROR_VARIABLE RESILIENCE_FIXTURE_VALIDATION_ERROR)
            if(NOT RESILIENCE_FIXTURE_VALIDATION_RESULT EQUAL 0)
                message(FATAL_ERROR
                    "Issue #22 fixture integrity check failed: ${RESILIENCE_FIXTURE_VALIDATION_ERROR}")
            endif()
            set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
                "${CMAKE_SOURCE_DIR}/workload/resilience_control_traps.wat"
                "${CMAKE_SOURCE_DIR}/tools/generate_resilience_wasm.py"
                "${CMAKE_SOURCE_DIR}/artifacts/resilience-wasm-fixture.json"
                "${CMAKE_SOURCE_DIR}/artifacts/resilience_control_traps_${MODULE_ARTIFACT_SUFFIX}"
                "${CMAKE_SOURCE_DIR}/artifacts/resilience_control_traps_${MODULE_ARTIFACT_SUFFIX}_embedded.c")
        endif()
        list(APPEND SCENARIO_SOURCE "module_resilience_test.c" "resilience_test_protocol.c"
            "../artifacts/resilience_control_traps_${MODULE_ARTIFACT_SUFFIX}_embedded.c")
    endif()
else()
    message(FATAL_ERROR "EXPERIMENT_SCENARIO must be native, interp, aot, module-interp or module-aot")
endif()

if(CONFIG_WASMHOME_NATIVE_SERVICES)
    list(APPEND SCENARIO_SOURCE "native_services.c")
endif()
if(CONFIG_WASMHOME_NATIVE_OTA)
    list(APPEND SCENARIO_SOURCE "native_ota.c")
endif()
if(CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
    if(NOT EXPERIMENT_SCENARIO STREQUAL "native")
        message(FATAL_ERROR "WASMHOME_RESILIENCE_TEST_MODE requires EXPERIMENT_SCENARIO=native")
    endif()
    list(APPEND SCENARIO_SOURCE "resilience_test.c" "resilience_test_protocol.c")
endif()

# Bind the HIL bootstrap configuration to the app image itself. A sidecar
# manifest alone can claim an endpoint/CA that was never compiled into the
# firmware, so every opt-in #22 profile embeds a small build marker derived
# from this build's sdkconfig and CA bytes.
if(CONFIG_WASMHOME_RESILIENCE_TEST_MODE OR CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
    if(NOT CONFIG_WASMHOME_SIGNED_UPDATE OR NOT CONFIG_WASMHOME_UPDATE_CA_CERT_PATH OR
       NOT EXISTS "${CONFIG_WASMHOME_UPDATE_CA_CERT_PATH}")
        message(FATAL_ERROR "Issue #22 profile provenance requires the signed-update CA bundle")
    endif()
    if(NOT CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_IPV4 OR
       NOT CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_NAME OR
       NOT CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_PORT EQUAL 18445)
        message(FATAL_ERROR "Issue #22 profile must compile its user-mode HTTPS endpoint on port 18445")
    endif()
    if(NOT "${CONFIG_WASMHOME_UPDATE_SERVER_IPV4}" STREQUAL "${CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_IPV4}" OR
       NOT CONFIG_WASMHOME_UPDATE_SERVER_PORT EQUAL CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_PORT OR
       NOT "${CONFIG_WASMHOME_UPDATE_SERVER_NAME}" STREQUAL "${CONFIG_WASMHOME_RESILIENCE_FIXTURE_SERVER_NAME}")
        message(FATAL_ERROR "Issue #22 signed provisioning and resilience fixture endpoints must match on port 18445")
    endif()
    if(EXPERIMENT_SCENARIO STREQUAL "native" AND CONFIG_WASMHOME_RESILIENCE_TEST_MODE)
        set(ISSUE22_PROFILE_NAME "native")
        set(ISSUE22_TIMEOUT_MS "${CONFIG_WASMHOME_RESILIENCE_FUNCTIONAL_TIMEOUT_MS}")
    elseif(EXPERIMENT_SCENARIO STREQUAL "module-interp" AND CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
        set(ISSUE22_PROFILE_NAME "interp")
        set(ISSUE22_TIMEOUT_MS "${CONFIG_WASMHOME_MODULE_RESILIENCE_FUNCTIONAL_TIMEOUT_MS}")
    elseif(EXPERIMENT_SCENARIO STREQUAL "module-aot" AND CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
        set(ISSUE22_PROFILE_NAME "aot")
        set(ISSUE22_TIMEOUT_MS "${CONFIG_WASMHOME_MODULE_RESILIENCE_FUNCTIONAL_TIMEOUT_MS}")
    else()
        message(FATAL_ERROR "Issue #22 test mode and EXPERIMENT_SCENARIO do not identify one supported profile")
    endif()
    if(ISSUE22_PROFILE_NAME STREQUAL "native" AND NOT CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE)
        message(FATAL_ERROR "Issue #22 native profile requires a rollback-enabled bootloader from this build")
    endif()
    if(CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE)
        set(ISSUE22_APP_ROLLBACK_ENABLED 1)
    else()
        set(ISSUE22_APP_ROLLBACK_ENABLED 0)
    endif()
    file(SHA256 "${SDKCONFIG}" ISSUE22_SDKCONFIG_SHA256)
    file(SHA256 "${CONFIG_WASMHOME_UPDATE_CA_CERT_PATH}" ISSUE22_CA_SHA256)
    if(NOT CMAKE_BUILD_EARLY_EXPANSION)
        execute_process(
            COMMAND "${PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/issue22_source_identity.py"
                --root "${CMAKE_SOURCE_DIR}" --cmake
            RESULT_VARIABLE ISSUE22_SOURCE_RESULT
            OUTPUT_VARIABLE ISSUE22_SOURCE_IDENTITY
            ERROR_VARIABLE ISSUE22_SOURCE_ERROR
            OUTPUT_STRIP_TRAILING_WHITESPACE)
        if(NOT ISSUE22_SOURCE_RESULT EQUAL 0)
            message(FATAL_ERROR "${ISSUE22_SOURCE_ERROR}")
        endif()
        list(GET ISSUE22_SOURCE_IDENTITY 0 ISSUE22_SOURCE_COMMIT)
        list(GET ISSUE22_SOURCE_IDENTITY 1 ISSUE22_SOURCE_TREE)
    endif()
    set(ISSUE22_PROVENANCE_FILE "${CMAKE_CURRENT_BINARY_DIR}/issue22-build-provenance.txt")
    configure_file("${CMAKE_CURRENT_LIST_DIR}/issue22-build-provenance.txt.in"
        "${ISSUE22_PROVENANCE_FILE}" @ONLY)
    list(APPEND UPDATE_EMBED_TXTFILES "${ISSUE22_PROVENANCE_FILE}")
    list(APPEND SCENARIO_SOURCE "issue22_build_provenance.c")
endif()

if(CONFIG_WASMHOME_RUN_BENCHMARK)
    list(APPEND SCENARIO_REQUIRES "pthread")
endif()

if(CONFIG_WASMHOME_MODULE_SERVICES)
    if(NOT EXPERIMENT_SCENARIO STREQUAL "module-interp" AND NOT EXPERIMENT_SCENARIO STREQUAL "module-aot")
        message(FATAL_ERROR "WASMHOME_MODULE_SERVICES requires a dedicated module-interp or module-aot scenario")
    endif()
endif()

idf_component_register(
    SRCS ${SCENARIO_SOURCE}
    INCLUDE_DIRS "." "../workload"
    REQUIRES ${SCENARIO_REQUIRES}
    PRIV_REQUIRES "app_update" "esp_app_format" "esp_partition"
        "esp_http_client" "mbedtls" "lwip"
    EMBED_TXTFILES ${UPDATE_EMBED_TXTFILES}
    EMBED_FILES ${UPDATE_EMBED_FILES}
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE ${SCENARIO_DEFINE})

if((CONFIG_WASMHOME_RESILIENCE_TEST_MODE OR CONFIG_WASMHOME_MODULE_RESILIENCE_TEST_MODE)
        AND NOT CMAKE_BUILD_EARLY_EXPANSION)
    # This runs on every build, including incremental builds that do not rerun
    # configuration. A stale marker must never survive source edits or a new HEAD.
    add_custom_target(issue22_source_identity_check
        COMMAND "${PYTHON}" -B "${CMAKE_SOURCE_DIR}/tools/issue22_source_identity.py"
            --root "${CMAKE_SOURCE_DIR}"
            --expected-commit "${ISSUE22_SOURCE_COMMIT}"
            --expected-tree "${ISSUE22_SOURCE_TREE}"
        VERBATIM)
    add_dependencies(${COMPONENT_LIB} issue22_source_identity_check)
endif()

if(CONFIG_WASMHOME_MODULE_SERVICES)
    idf_component_get_property(WAMR_MODULE_COMPONENT_DIR wasm-micro-runtime COMPONENT_DIR)
    idf_component_get_property(WAMR_MODULE_COMPONENT_LIB wasm-micro-runtime COMPONENT_LIB)
    if(NOT WAMR_MODULE_COMPONENT_DIR)
        message(FATAL_ERROR "Unable to locate pinned WAMR component for start-function metadata validation")
    endif()
    if(NOT WAMR_MODULE_COMPONENT_LIB)
        message(FATAL_ERROR "Unable to locate pinned WAMR component library for allocator ABI configuration")
    endif()
    # The allocator callbacks in module_manager.c take mem_alloc_usage_t, but
    # WAMR selects that ABI in wasm_memory.c at compile time. A definition on
    # this component alone leaves the runtime with the incompatible one-arg
    # allocator signature.
    target_compile_definitions(${WAMR_MODULE_COMPONENT_LIB} PUBLIC
        WASM_MEM_ALLOC_WITH_USAGE=1)
    target_compile_options(${WAMR_MODULE_COMPONENT_LIB} PRIVATE
        "$<$<COMPILE_LANGUAGE:C>:-include>"
        "$<$<COMPILE_LANGUAGE:C>:${CMAKE_CURRENT_LIST_DIR}/wamr_allocator_abi_guard.h>")
    get_target_property(WAMR_MODULE_COMPILE_DEFINITIONS
        ${WAMR_MODULE_COMPONENT_LIB} COMPILE_DEFINITIONS)
    if(NOT WAMR_MODULE_COMPILE_DEFINITIONS)
        message(FATAL_ERROR "WAMR allocator ABI definition was not applied to the runtime target")
    endif()
    set(WAMR_ALLOCATOR_ABI_DEFINITION_COUNT 0)
    foreach(WAMR_MODULE_DEFINITION IN LISTS WAMR_MODULE_COMPILE_DEFINITIONS)
        if(WAMR_MODULE_DEFINITION MATCHES "^WASM_MEM_ALLOC_WITH_USAGE(=|$)")
            if(NOT WAMR_MODULE_DEFINITION STREQUAL "WASM_MEM_ALLOC_WITH_USAGE=1")
                message(FATAL_ERROR "WAMR allocator ABI definition conflicts with the required value: ${WAMR_MODULE_DEFINITION}")
            endif()
            math(EXPR WAMR_ALLOCATOR_ABI_DEFINITION_COUNT
                "${WAMR_ALLOCATOR_ABI_DEFINITION_COUNT} + 1")
        endif()
    endforeach()
    if(NOT WAMR_ALLOCATOR_ABI_DEFINITION_COUNT EQUAL 1)
        message(FATAL_ERROR "WAMR runtime must have exactly one WASM_MEM_ALLOC_WITH_USAGE=1 definition")
    endif()
    target_include_directories(${COMPONENT_LIB} PRIVATE
        "${WAMR_MODULE_COMPONENT_DIR}/core/iwasm/common"
        "${WAMR_MODULE_COMPONENT_DIR}/core/iwasm/interpreter"
        "${WAMR_MODULE_COMPONENT_DIR}/core/iwasm/aot")
    # module_manager.c inspects WAMR's private WASMModule metadata to reject
    # start functions before instantiation. Keep that struct layout aligned
    # with the pinned runtime, which enables bulk memory in this port.
    target_compile_definitions(${COMPONENT_LIB} PRIVATE
        WASM_ENABLE_BULK_MEMORY=1)
endif()

if(EXPERIMENT_SCENARIO STREQUAL "native")
    # ESP-IDF performance defaults to -O2. The comparison protocol fixes -O3
    # for the workload; apply it after target flags on this translation unit.
    set_source_files_properties("../workload/benchmark.c" PROPERTIES COMPILE_OPTIONS "-O3")
endif()

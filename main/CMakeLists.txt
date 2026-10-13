if(NOT DEFINED EXPERIMENT_SCENARIO)
    if(DEFINED ENV{WASMHOME_EXPERIMENT_SCENARIO})
        set(EXPERIMENT_SCENARIO "$ENV{WASMHOME_EXPERIMENT_SCENARIO}")
    else()
        set(EXPERIMENT_SCENARIO "native")
    endif()
endif()

set(SCENARIO_SOURCE "main.c" "platform.c" "sensor.c" "observation_wire.c" "platform_selftest.c" "observer_selftest.c")
set(SCENARIO_REQUIRES "esp_timer" "esp_wifi" "esp_netif" "esp_event" "nvs_flash" "driver" "esp_system" "onewire_bus")

if(CONFIG_WASMHOME_NATIVE_SERVICES)
    if(NOT EXPERIMENT_SCENARIO STREQUAL "native")
        message(FATAL_ERROR "WASMHOME_NATIVE_SERVICES is available only in EXPERIMENT_SCENARIO=native")
    endif()
    if(CONFIG_WASMHOME_PLATFORM_SELFTEST OR CONFIG_WASMHOME_OBSERVER_SELFTEST OR CONFIG_WASMHOME_RUN_BENCHMARK)
        message(FATAL_ERROR "WASMHOME_NATIVE_SERVICES cannot be combined with diagnostics or benchmark")
    endif()
endif()

if(NOT EXPERIMENT_SCENARIO STREQUAL "native" AND NOT CMAKE_BUILD_EARLY_EXPANSION)
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
else()
    message(FATAL_ERROR "EXPERIMENT_SCENARIO must be native, interp or aot")
endif()

if(CONFIG_WASMHOME_NATIVE_SERVICES)
    list(APPEND SCENARIO_SOURCE "native_services.c")
endif()

idf_component_register(
    SRCS ${SCENARIO_SOURCE}
    INCLUDE_DIRS "." "../workload"
    REQUIRES ${SCENARIO_REQUIRES}
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE ${SCENARIO_DEFINE})

if(EXPERIMENT_SCENARIO STREQUAL "native")
    # ESP-IDF performance defaults to -O2. The comparison protocol fixes -O3
    # for the workload; apply it after target flags on this translation unit.
    set_source_files_properties("../workload/benchmark.c" PROPERTIES COMPILE_OPTIONS "-O3")
endif()

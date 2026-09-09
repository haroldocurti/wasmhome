if(NOT DEFINED EXPERIMENT_SCENARIO)
    set(EXPERIMENT_SCENARIO "native")
endif()

set(SCENARIO_SOURCE "main.c")
set(SCENARIO_REQUIRES "esp_timer")

if(EXPERIMENT_SCENARIO STREQUAL "native")
    list(APPEND SCENARIO_SOURCE "../workload/benchmark.c")
    set(SCENARIO_DEFINE WASMHOME_SCENARIO_NATIVE=1)
elseif(EXPERIMENT_SCENARIO STREQUAL "interp")
    if(NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark.wasm")
        message(FATAL_ERROR "Run tools/build-artifacts.ps1 before the interpreted build")
    endif()
    if(NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark_wasm_embedded.c")
        message(FATAL_ERROR "Embedded Wasm source is missing; rerun tools/build-artifacts.ps1")
    endif()
    list(APPEND SCENARIO_REQUIRES "wasm-micro-runtime")
    set(SCENARIO_DEFINE WASMHOME_SCENARIO_INTERP=1)
    list(APPEND SCENARIO_SOURCE "../artifacts/benchmark_wasm_embedded.c")
elseif(EXPERIMENT_SCENARIO STREQUAL "aot")
    if(NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark.aot")
        message(FATAL_ERROR "Run tools/build-artifacts.ps1 before the AOT build")
    endif()
    if(NOT EXISTS "${CMAKE_SOURCE_DIR}/artifacts/benchmark_aot_embedded.c")
        message(FATAL_ERROR "Embedded AOT source is missing; rerun tools/build-artifacts.ps1")
    endif()
    list(APPEND SCENARIO_REQUIRES "wasm-micro-runtime")
    set(SCENARIO_DEFINE WASMHOME_SCENARIO_AOT=1)
    list(APPEND SCENARIO_SOURCE "../artifacts/benchmark_aot_embedded.c")
else()
    message(FATAL_ERROR "EXPERIMENT_SCENARIO must be native, interp or aot")
endif()

idf_component_register(
    SRCS ${SCENARIO_SOURCE}
    INCLUDE_DIRS "." "../workload"
    REQUIRES ${SCENARIO_REQUIRES}
)

target_compile_definitions(${COMPONENT_LIB} PRIVATE ${SCENARIO_DEFINE})

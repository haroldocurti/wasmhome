cmake_minimum_required(VERSION 3.16)

# Campaign trust/resilience requirements. The ESP-IDF WAMR Kconfig wrapper
# does not expose these switches; set them before components are evaluated.
set(WAMR_BUILD_AOT_VALIDATOR 1 CACHE BOOL "Validate loaded WAMR AOT modules" FORCE)
set(WAMR_CONFIGURABLE_BOUNDS_CHECKS 0 CACHE BOOL "Keep WAMR bounds checks mandatory" FORCE)
add_compile_definitions(WASM_CONFIGURABLE_BOUNDS_CHECKS=0)

# Independent candidate source is acquired and verified before configuration.
# Keep its name local; it is not the Espressif registry component.
if(NOT DEFINED EXPERIMENT_SCENARIO)
    set(EXPERIMENT_SCENARIO "native")
endif()
# ESP-IDF 5.2 resolves component requirements in a separate CMake context,
# which does not inherit project cache variables. Pass the selected scenario
# through the process environment so the component graph matches the build.
set(ENV{WASMHOME_EXPERIMENT_SCENARIO} "${EXPERIMENT_SCENARIO}")
if(EXPERIMENT_SCENARIO STREQUAL "native")
    # Native A does not require acquiring or linking the candidate runtime.
else()
    set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/build-runtime-port/component/wasm-micro-runtime")
    if(NOT EXISTS "${EXTRA_COMPONENT_DIRS}/.wasmhome-runtime.json")
        message(FATAL_ERROR "Run python tools/runtime_package.py --acquire before B/C configuration")
    endif()
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wasmhome_bench)

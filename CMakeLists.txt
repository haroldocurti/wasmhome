cmake_minimum_required(VERSION 3.16)

# Campaign trust/resilience requirements. The ESP-IDF WAMR Kconfig wrapper
# does not expose these switches; set them before components are evaluated.
set(WAMR_BUILD_AOT_VALIDATOR 1 CACHE BOOL "Validate loaded WAMR AOT modules" FORCE)
set(WAMR_CONFIGURABLE_BOUNDS_CHECKS 0 CACHE BOOL "Keep WAMR bounds checks mandatory" FORCE)
add_compile_definitions(WASM_CONFIGURABLE_BOUNDS_CHECKS=0)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wasmhome_bench)

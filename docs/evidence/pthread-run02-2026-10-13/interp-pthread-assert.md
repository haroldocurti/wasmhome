# Interp run02 pthread assertion diagnosis

The provided backtrace was resolved with xtensa-esp32s3-elf-addr2line 2.41 against the exact interp ELF, SHA-256 f511d5d16017b2c1512ab4d9406fb3509adc5b53f0313257f12f4175ea8002be. Tool SHA-256: b795f68a75af46b0505df93fc515523ce2bce2b6da3e052f966e83983a3528f4. Source pin: a91ff5efabbdd57a5bd742f5682bab1c3124e018 / c0a09c694cc8e751b9d5f8122d07004c5da420ef, clean.

| PC | Symbol | Source location |
|---|---|---|
| 0x40375de2 | panic_abort | esp_system/panic.c:482 |
| 0x4037e0e9 | esp_system_abort | esp_system/port/esp_system_chip.c:93 |
| 0x403859ad | __assert_func | newlib/assert.c:81 |
| 0x4200295b | pthread_self | pthread/pthread.c:481 |
| 0x4200daa3 | os_self_thread | wasm-micro-runtime/core/shared/platform/esp-idf/espidf_thread.c:40 |
| 0x4200e313 | wasm_exec_env_set_thread_info | wasm-micro-runtime/core/iwasm/common/wasm_exec_env.c:286 |
| 0x4200d510 | wasm_call_function | wasm-micro-runtime/core/iwasm/interpreter/wasm_runtime.c:3680 |
| 0x4200b887 | wasm_runtime_call_wasm | wasm-micro-runtime/core/iwasm/common/wasm_runtime_common.c:2666 |
| 0x4200a141 | runner_call | main/main.c:203 |
| 0x4200a45c | app_main | main/main.c:276 |
| 0x42091e7b | main_task | esp-idf/components/freertos/app_startup.c:208 |

IDF pthread_self looks up the current FreeRTOS task in its pthread descriptor list and asserts if no descriptor exists. IDF adds entries through pthread_create(). The stack shows WAMR calling its ESP-IDF port os_self_thread() from the IDF main_task launched through app_main; that task was not registered through pthread_create(). This is an integration contract mismatch, not a Wasm workload result. WAMR AOT aot_call_function() also calls wasm_exec_env_set_thread_info() (aot_runtime.c:2614), so the same issue is expected to affect AOT until the fix covers all WAMR execution paths.

The ELF and sources were read-only. No serial, UDP, reset, flash, guest modification, or device action was performed for this diagnosis.

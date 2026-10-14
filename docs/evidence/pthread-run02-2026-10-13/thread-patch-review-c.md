# Independent offline review: E1 POSIX worker patch

Base commit/tree: `a91ff5efabbdd57a5bd742f5682bab1c3124e018` / `c0a09c694cc8e751b9d5f8122d07004c5da420ef`.

Reviewed exact changed-file digests:

- `main/main.c`: `169166702d111bd30304b9c73917b61c1e4374d834803bff945a725df230ac4a`
- `main/CMakeLists.txt`: `ee850c89ec6db9ac3b65f0eab665ac118e93ed7eb8e1a5ab1daf90b6c3bd6692`
- `tests/test_e1_runner.py`: `594b63a782bc9413d0bb18b7877c02b2b642dfec99dc3d274632ead3c5f4fdeb`
- combined diff: `cc04e8da0d12beb3cfa9ea8341e712eb9539ad887aa41226486f4225cf18c476`

## Review findings

The patch moves benchmark work into a detached POSIX worker and leaves the existing WAMR thread assertion intact. This addresses the run02 interpreter failure at `pthread_self`/`wasm_exec_env_set_thread_info` by running the runtime on a pthread-created task rather than the FreeRTOS `main_task`. Platform/service initialization remains in `app_main`; WAMR initialization, load/calls, boot-id read, measurements, cleanup and destruction run in the worker. The worker is detached so returning from `app_main` cannot wait on it or make it a child of `main_task`.

ESP-IDF 5.2.6's `pthread_attr_setstacksize` parameter is bytes, checks `PTHREAD_STACK_MIN` (768 bytes in the pinned IDF), and the stack-size attribute overrides the configured pthread default (3072 bytes). The configured 8192-byte host stack is therefore valid; it is distinct from the 8192-byte WAMR/WebAssembly execution stack. `pthread_create` copies the attributes before the code destroys the attribute object. This stack is now resident when the first benchmark memory event is emitted, so memory observations include worker-task allocations and are not directly comparable to pre-worker values taken in `app_main`.

`main/CMakeLists.txt` adds the ESP-IDF `pthread` dependency only when `CONFIG_WASMHOME_RUN_BENCHMARK` is enabled. The host tests cover the benchmark-disabled path in CMake, the interpreter and native worker routes, the detached/stack attributes, 35 native calls/67 memory events, and failure to create the worker without benchmark activity. Review did not find an assertion bypass or a failure-path resource leak introduced by the split.

## Validation and scope

Command: `python -m unittest tests.test_e1_runner -v` in the shared checkout. Result: 11 tests passed.

This is a source/test review only. No ESP-IDF rebuild or physical device execution was performed here; each E1 mode still requires the authorized rebuilt artifact receipts and subsequent device validation. The measured heap baseline changes because the pthread stack is allocated before benchmark instrumentation starts.

**Review outcome:** no blocker found in the submitted patch.

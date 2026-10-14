# Independent review: E1 POSIX benchmark worker

**Verdict:** no static blocker to publishing the patch and building the three E1 scenarios. The actual IDF builds at the new source pin remain required before a physical run or a claim of runtime success. No device I/O was performed for this review.

## Reviewed inputs

- Base commit/tree: `a91ff5efabbdd57a5bd742f5682bab1c3124e018` / `c0a09c694cc8e751b9d5f8122d07004c5da420ef`.
- `main/main.c` SHA-256: `169166702d111bd30304b9c73917b61c1e4374d834803bff945a725df230ac4a`.
- `main/CMakeLists.txt` SHA-256: `ee850c89ec6db9ac3b65f0eab665ac118e93ed7eb8e1a5ab1daf90b6c3bd6692`.
- `tests/test_e1_runner.py` SHA-256: `594b63a782bc9413d0bb18b7877c02b2b642dfec99dc3d274632ead3c5f4fdeb`.
- Diff SHA-256: `cc04e8da0d12beb3cfa9ea8341e712eb9539ad887aa41226486f4225cf18c476`.

## Findings

The pinned ESP-IDF 5.2.6 source inspected in CT107 is commit `9ef24e3e2a2c96e720d83c574a3f8699177573da`. `pthread_attr_setstacksize` stores a byte count and accepts values at or above `PTHREAD_STACK_MIN`; the Kconfig default minimum is 768 bytes. The `pthread_create` implementation replaces its default stack size with `attr->stacksize`, passes that byte size to FreeRTOS (rounding to `StackType_t` units), applies the requested detached state, registers the pthread descriptor before starting the task, and the IDF wrapper calls `pthread_exit` when the entry function returns. Thus the explicit 8192-byte host stack is accepted and its current-task descriptor exists before WAMR calls `pthread_self`. The earlier `app_main` task lacked that descriptor, matching the run02 assertion trace.

The previous three E1 sdkconfigs each had `CONFIG_PTHREAD_STACK_MIN=768`, `CONFIG_PTHREAD_TASK_STACK_SIZE_DEFAULT=3072`, and default priority 5. The explicit 8192-byte attribute therefore overrides the 3072-byte default consistently. `main.c` does not call `esp_pthread_set_cfg`; IDF's ordinary default priority/core behavior applies. CMake adds the `pthread` dependency only when `CONFIG_WASMHOME_RUN_BENCHMARK` is enabled, matching the conditional include and keeping non-benchmark service builds independent.

The 8192-byte host pthread stack is separate from WAMR's 8192-byte stack size passed to `wasm_runtime_instantiate` and `wasm_runtime_create_exec_env`. The worker is detached, receives no pointer to `app_main` stack data, runs the whole benchmark, and returns through IDF's pthread wrapper. `app_main` may return safely after successful creation. On setup or creation failure, the benchmark does not run, so missing CSV/call evidence remains detectable by the existing gates. Attribute destruction happens after `pthread_create` returns, which is valid; IDF's implementation resets the attribute object and owns no resource there.

All three benchmark scenarios now use this same pthread path. The worker's task stack is allocated before `before_runtime` is recorded, so its allocation is included in the free-heap measurements for each scenario; the WAMR stack remains a separate runtime allocation. This changes the scheduling and heap baseline relative to the older benchmark executed directly on `app_main`. New native/interp/AOT results should be compared with one another at the new pin, not treated as directly comparable to the old run.

## Validation and remaining check

The patch's host harness models the pthread API synchronously; it checks attribute setup, detached state, workload calls, and failure handling, but it cannot establish actual FreeRTOS task stack margin or detached-task behavior on hardware. The change is structurally consistent with the pinned IDF implementation, so an IDF build is not a blocker to publishing the reviewed source pin. Build all three E1 scenarios at that pin before hardware; then require the existing runtime gates to show 5 warmups + 30 measured calls, all CSV checksums/statuses, release, and no reset/stack-overflow assertion. The current code emits no host-stack high-water measurement, so runtime evidence only shows that the selected stack did not fail during the trial, not its remaining margin.

`git diff --check` was clean during review. The author reports 11/11 focused tests and 91 suite cases with 5 skips; this review did not rerun that full suite or access the device.

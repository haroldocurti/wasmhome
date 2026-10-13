# R5 — capacidade heap estável e snapshot livre coerente

Data: 13/10/2026. Correção restrita a `main/main.c` e `tests/test_e1_runner.py`, após revisões independentes apontarem que `multi_heap_info_t.total_allocated_bytes` não inclui o overhead variável por bloco do TLSF. O estado e os logs r4 permanecem preservados em `/tmp/wasmhome-native-fixes-r4-20261013/`.

## Alteração

Para cada capacidade habilitada, `memory_event()` agora coleta `total_bytes` com `heap_caps_get_total_size(cap)` e faz uma chamada `heap_caps_get_info(&info, cap)` para `free_bytes` e `largest_free_block_bytes`. Assim, a capacidade declarada não depende de `total_free_bytes + total_allocated_bytes`, enquanto livre e maior bloco continuam vindos do mesmo snapshot do IDF. O ramo PSRAM segue o mesmo padrão; o ramo sem PSRAM continua emitindo `null/not_applicable`. A linha JSON continua sendo emitida por uma única chamada `printf`.

Confirmei em fonte oficial ESP-IDF v5.2.6: `multi_heap_get_info_impl` subtrai `allocated_blocks * tlsf_alloc_overhead()` de `total_allocated_bytes`; `heap_caps_get_info` expõe o struct, e `heap_caps_get_total_size` é o getter dedicado de tamanho total. Fontes primárias: [multi_heap.c v5.2.6, cálculo do overhead](https://github.com/espressif/esp-idf/blob/v5.2.6/components/heap/multi_heap.c#L382-L400), [esp_heap_caps.h v5.2.6](https://github.com/espressif/esp-idf/blob/v5.2.6/components/heap/include/esp_heap_caps.h#L170-L238).

O stub agora faz `total_free_bytes + total_allocated_bytes` variar entre snapshots, para representar o overhead, e retorna a capacidade total fixa pelo getter dedicado. O harness confirma uma chamada a cada API necessária por evento/capacidade; verifica que a soma do struct de info realmente muda; testa que `free` e `largest` vêm juntos do mesmo info mutável; e mantém getters antigos separados com valores deliberadamente incompatíveis (`free=800`, `largest=900`) para detectar regressão. As relações `largest <= free <= total`, o ramo PSRAM, emissão por uma chamada e fluxos de liberação continuam cobertos.

## Verificação

- `test_e1_runner.py`: 9 testes aprovados, exit code 0. Log: `/tmp/wasmhome-native-fixes-r5-20261013/test_e1_runner.log`, SHA-256 `ac1e959d4ea5c7355742952a33ce1fc961894a5713d70d01fb0711c67d26e2d4`.
- Suíte completa: 81 testes, 80 aprovados e 1 skip, exit code 0. O único skip requer junction Windows. Log: `/tmp/wasmhome-native-fixes-r5-20261013/unittest-full.log`, SHA-256 `901e878a1897ec7a63dc01c38b23c8660a69046d84908def9ffcaa9a55afce2d`.
- `git diff --check`: passou.

Comando integral:

```sh
PYTHONDONTWRITEBYTECODE=1 \
WASMHOME_WIRE_LIBRARY=/tmp/wasmhome-p1-20261013-r2/libobservation_wire.so \
PATH=/home/haroldocurti/wasmhome-toolchain-e1-2.4.5-xtensa18-20261012/tools/cmake/bin:/home/haroldocurti/.local/toolchains/wasmhome-pwsh-7.6.6-20261013:/usr/bin:/bin \
/usr/bin/python3 -B -m unittest discover -s tests -v
```

## Identidade e limite

- `main/main.c`: SHA-256 `ad031c6ad9e3a7d4af294a4cbf3c14b09c8bcab6e401e131047c0d3f81ec74e3`
- `tests/test_e1_runner.py`: SHA-256 `b95416fa340dfd81cd5043a5145db987a6b81b2075b1a9f20962ad0959e138c7`
- Diff dos dois arquivos contra HEAD, 26.761 bytes: `/tmp/wasmhome-native-fixes-r5-20261013/main-and-runner-vs-HEAD.diff`, SHA-256 `6fc1f48988c9005e760f22c335f918275e1e4a26a3fbd27bf2807d88bf4ceb3d`.

Não foi alterado o validador, domínios, fases, timers, fluxo CSV nem código dos serviços. Não rodei build ESP-IDF, flash, serial ou ensaio físico; o revisor independente deve avaliar este snapshot antes de qualquer integração posterior.

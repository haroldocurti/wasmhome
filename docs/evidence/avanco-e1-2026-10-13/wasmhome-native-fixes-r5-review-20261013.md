# Revisão independente do snapshot r5 — instrumentação de heap E1

Data: 13/10/2026. Checkout revisado: `/home/haroldocurti/Documents/wasmhome`, HEAD `0ba2faab16dc9671f89a663eea8e6d0d1f528997`. O diff focado contra HEAD tem SHA-256 `6fc1f48988c9005e760f22c335f918275e1e4a26a3fbd27bf2807d88bf4ceb3d`. Arquivos verificados: `main/main.c` SHA-256 `ad031c6ad9e3a7d4af294a4cbf3c14b09c8bcab6e401e131047c0d3f81ec74e3`, `tests/test_e1_runner.py` SHA-256 `b95416fa340dfd81cd5043a5145db987a6b81b2075b1a9f20962ad0959e138c7`.

## Revisão

O r5 corrige o blocker do r4. A capacidade (`total_bytes`) agora vem de `heap_caps_get_total_size(cap)`, cuja documentação v5.2.6 define o retorno como a soma do espaço total das regiões com a capacidade pedida. `free_bytes` e `largest_free_block_bytes` vêm de uma só chamada `heap_caps_get_info()` por capacidade. Assim, a capacidade não é inferida de `total_free_bytes + total_allocated_bytes`, soma que perde o overhead TLSF por bloco. A aplicação e o harness cobrem os ramos interno e PSRAM, além do ramo sem PSRAM.

Confirmei a declaração e a semântica em fonte oficial primária: [ESP-IDF v5.2.6 `esp_heap_caps.h`](https://raw.githubusercontent.com/espressif/esp-idf/v5.2.6/components/heap/include/esp_heap_caps.h), que declara `heap_caps_get_total_size` (linhas 170–181), inclui `multi_heap.h` e declara `heap_caps_get_info` (linhas 225–238). A origem do blocker anterior permanece documentada em [ESP-IDF v5.2.6 `multi_heap.c`](https://raw.githubusercontent.com/espressif/esp-idf/v5.2.6/components/heap/multi_heap.c), linhas 393–399.

O stub r5 varia a soma dos campos do struct, verifica que ela mudou durante a execução e exige capacidade fixa obtida pelo getter dedicado; uma regressão para a fórmula antiga falharia nas assertions do JSON. O harness continua checando as chamadas por evento/capacidade, `largest <= free <= total`, atomicidade da linha, PSRAM, estágios 0–9 e liberação dos recursos.

## Verificação independente

- `test_e1_runner.py`: 9 testes aprovados, sem skips, exit 0. Log `/tmp/wasmhome-native-fixes-r5-review-20261013-focal.log`, SHA-256 `7d5e9f1dac282c7f0287fb070c5760d52869ed5664b465200f354ee077ea6719`.
- Suíte completa com PATH contendo CMake e PowerShell e `WASMHOME_WIRE_LIBRARY` definido: 81 testes, 80 aprovados e 1 skip esperado para junction Windows, exit 0. Log `/tmp/wasmhome-native-fixes-r5-review-20261013-full.log`, SHA-256 `dc4ab7a00baba5e79161e1de3702dc98c90ae37210364f2688f1b30ed40f5e44`.
- `git diff --check -- main/main.c tests/test_e1_runner.py`: passou.

Observação não bloqueadora: o stub mantém `allocated_blocks = 1` e varia `total_allocated_bytes` diretamente; ele detecta a regressão da fórmula, mas não representa literalmente a relação de overhead por contagem de blocos indicada pelo IDF. Para precisão didática adicional, a fixture poderia variar `allocated_blocks` junto com `total_allocated_bytes` de forma coerente com um overhead fixo por bloco. Isso não invalida a correção, cuja semântica foi verificada na fonte oficial.

**Parecer:** aprovado para a revisão dirigida do bloqueio `total_bytes`; sem achados bloqueadores neste escopo. Não rodei build ESP-IDF, flash, serial ou ensaio físico.

# Revisão independente do R5 — métrica de capacidade heap

Data: 2026-10-13
Resultado: **correção aprovada no escopo da revisão e dos testes de host; nenhum bloqueio na semântica de `total_bytes`**. Esta aprovação cobre a correção da métrica heap e a suíte de host; não representa build ESP-IDF, validação em placa, flash, serial ou aprovação independente de alterações fora do escopo.

## Identidade

- HEAD/tree base: `0ba2faab16dc9671f89a663eea8e6d0d1f528997` / `76cd12437c7bf7351880ee7ee03d969248ec5e51`.
- `main/main.c` SHA-256: `ad031c6ad9e3a7d4af294a4cbf3c14b09c8bcab6e401e131047c0d3f81ec74e3`; blob `2080844c6c28cfaccb5d5ddf6665469af87063e0`.
- `tests/test_e1_runner.py` SHA-256: `b95416fa340dfd81cd5043a5145db987a6b81b2075b1a9f20962ad0959e138c7`; blob `8024e2afbac31a02a83bc239a5b1a249d93010d3`.
- Diff focal informado pelo autor: `/tmp/wasmhome-native-fixes-r5-20261013/main-and-runner-vs-HEAD.diff`, SHA-256 `6fc1f48988c9005e760f22c335f918275e1e4a26a3fbd27bf2807d88bf4ceb3d`. Blob IDs finais no cabeçalho do diff conferem com os arquivos atuais.
- Relatório do autor: `/tmp/wasmhome-native-fixes-r5-20261013.md`, SHA-256 `a6eafcb475e2b654f0ccfbdcf758b226e9aae5d7ae27da86666d3033f9cc49b3`.

## Revisão da métrica

R5 define `total_bytes` por `heap_caps_get_total_size(cap)` e lê `free_bytes`/`largest_free_block_bytes` de uma chamada `heap_caps_get_info` por capability. Isso corrige o problema de r4: a capacidade não é mais inferida pela soma de campos de `multi_heap_info_t` cujo valor líquido muda com o overhead TLSF por bloco. O stub r5 agora varia essa soma ao longo dos snapshots e confirma que o JSON continua usando o getter de capacidade fixa. Também verifica chamadas por evento/capability, valores livres/maior bloco do mesmo snapshot, ramo PSRAM, fluxo sem PSRAM e emissão por uma chamada.

A API e sua semântica estão confirmadas em fonte oficial ESP-IDF v5.2.6: [heap_caps_get_total_size e heap_caps_get_info](https://github.com/espressif/esp-idf/blob/v5.2.6/components/heap/include/esp_heap_caps.h#L1354-L1480) documentam respectivamente a soma dos tamanhos das regiões e o agregado dos metadados livres; [multi_heap_get_info_impl](https://github.com/espressif/esp-idf/blob/v5.2.6/components/heap/multi_heap.c#L1678-L1706) calcula allocated bytes subtraindo o overhead por bloco.

Não encontrei novo bloqueio no ajuste dirigido. A chamada de capacidade e a de metadados livres são APIs separadas, mas a primeira consulta os tamanhos registrados das regiões; os workloads e configurações atuais não registram ou removem regiões durante o ensaio. A estabilidade em execução de placa ainda precisa ser confirmada pelo build/runtime gate subsequente.

## Testes independentes

Executados nesta árvore, sem alterar fontes: `test_e1_runner.py` teve 9/9 aprovados; suite completa teve 81 testes, 80 aprovados e 1 skip (junction Windows); `git diff --check` passou.

- Log focal: `/tmp/wasmhome-native-review-r5/targeted-runner.log`, SHA-256 `7d5e9f1dac282c7f0287fb070c5760d52869ed5664b465200f354ee077ea6719`.
- Suite completa: `/tmp/wasmhome-native-review-r5/unittest-full.log`, SHA-256 `2f63fddf2eab09a18430dff00fef7fcfe401a1f6345ba867fbd14e940b5ccc9e`.
- Diferenças de outros arquivos já presentes no checkout não foram alteradas por esta revisão.

## Preservação de proveniência P0

O pacote privado `/home/haroldocurti/.local/share/wasmhome-private/source-801-20261013` agora inclui o bundle 801, metadados com a limitação shallow/bare-clone, history, relatórios e cópias integrais dos manifests R8/R9. Diretório está em modo 700; arquivos em modo 600; `sha256sum -c SHA256SUMS` passou. Os hashes R8 e R9 são `1a8c85d288cd08eb8bc600fc69c6211c5b7137070c2ee6e8fd92d78a3ec926d1` e `a62b25141a78da34e06b16ddcb59f93892c65df469b3af41965b06d719f41ea7`.

A lacuna histórica do manifest final R6 `f31158a3…` continua explicitamente aberta: nenhuma cópia de log original recuperada liga aquele manifest à execução exata. As gerações R8/R9 são evidência nova, não retroativa.

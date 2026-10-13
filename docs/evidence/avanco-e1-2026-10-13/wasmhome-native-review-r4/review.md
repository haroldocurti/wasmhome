# Revisão B do snapshot r4 — memória

Data: 2026-10-13  
Resultado: **não aprovar para build ainda**; há um bloqueio semântico na métrica `total_bytes`.

## Snapshot e verificações

O diff r4 verificado é `/tmp/wasmhome-native-fixes-r4-20261013/working-tree-final-r4-final.diff`, SHA-256 `7ff84b06bd4892a41b47676d53dd5c19873c3ff492dbe316f4756ec0c5a586d4`. Os blob IDs dos arquivos alterados no r4 correspondem ao diff: `main/main.c` = `8048564fcd7275f3a7c5ae8fa088477f4b8ee747`; `tests/test_e1_runner.py` = `a2fe49f8fb3b9d35357d2ac8c72f2e6e5c577044`. HEAD e tree base continuam `0ba2faab16dc9671f89a663eea8e6d0d1f528997` / `76cd12437c7bf7351880ee7ee03d969248ec5e51`. Não alterei o repositório.

O teste compilado específico do runner passou. A suíte completa passou com 81 testes: 80 aprovados e 1 skip para junctions Windows. Logs:

- `memory-helper-test.log`: SHA-256 `1ccfd8dc40f755dd58a3345e3d3c486c2067591a3239b7a223d20e9b98ce6ef0`
- `unittest-full.log`: SHA-256 `6aac9137c9f97d6b6739837b71be8fd0ef9ad3f9b218ee3903cbaede8edd6cdf`

## Bloqueio

Em `main/main.c`, r4 define cada `total_bytes` como `total_free_bytes + total_allocated_bytes` retornados por `heap_caps_get_info`. Isso não é uma capacidade constante no ESP-IDF 5.2.6. A implementação de `multi_heap_get_info_impl` subtrai o overhead TLSF por bloco alocado ao calcular `total_allocated_bytes`; o comentário declara explicitamente que esse overhead não integra o valor. O agregador `heap_caps_get_info` também subtrai o overhead de owner por heap e por bloco. Assim, a soma dos dois campos varia conforme a contagem de blocos alocados/liberados. Isso conflita com a exigência do validador de que `total_bytes` permaneça constante durante o boot e pode rejeitar logs legítimos das fases de carga e liberação.

A fonte primária do IDF usado:

- [multi_heap.c v5.2.6](https://raw.githubusercontent.com/espressif/esp-idf/v5.2.6/components/heap/multi_heap.c), em especial a fórmula e o comentário em `multi_heap_get_info_impl`.
- [heap_caps.c v5.2.6](https://raw.githubusercontent.com/espressif/esp-idf/v5.2.6/components/heap/heap_caps.c), onde o agregador `heap_caps_get_info` subtrai overhead por região/bloco e `heap_caps_get_total_size` soma os tamanhos das regiões registradas.
- [Documentação ESP-IDF 5.2.6 para ESP32-S3](https://docs.espressif.com/projects/esp-idf/en/v5.2.6/esp32s3/api-reference/system/mem_alloc.html), que documenta `heap_caps_get_info` como agregação de `multi_heap_info_t` e define os campos.

O stub novo evita revelar esse caso: para o heap interno ele define `free=1000+n` e `allocated=1048-n`, então a soma permanece sempre 2048; o teste confirma exatamente essa soma. Ele verifica que o helper usa `heap_caps_get_info` em vez dos getters antigos, mas não modela overhead por bloco nem a variação resultante.

## Próximo ajuste recomendado

Manter uma chamada `heap_caps_get_info` por capacidade para coletar `free_bytes` e `largest_free_block_bytes`, e usar `heap_caps_get_total_size(cap)` para o campo de capacidade fixa `total_bytes`. Alternativamente, se `total_bytes` passar a significar capacidade líquida dos dados, mudar explicitamente o contrato/validador para não exigir constância e documentar o overhead variável. A primeira opção preserva a semântica atual de capacidade total. O stub deve incluir overhead variável em pelo menos dois números de blocos e provar que `total_bytes` continua invariável enquanto free/largest variam.

Nenhum build ou atividade no CT107 foi iniciado: o gate de revisão r4 não passou. O roteiro privado de builds permanece apenas preparado em `/tmp/wasmhome-idf-r4-prep/`.

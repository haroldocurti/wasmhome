# R4 — snapshot único da instrumentação de heap E1

Data: 13/10/2026. Correção limitada a `main/main.c` e ao stub/harness em `tests/test_e1_runner.py`, atendendo ao achado de que consultas de total, livre e maior bloco em chamadas separadas podem intercalar alocações concorrentes.

## Implementação

`memory_event()` agora chama uma vez `heap_caps_get_info(&info, capability)` por capacidade e captura o struct antes da formatação do JSON. Em cada snapshot, `total_bytes = total_free_bytes + total_allocated_bytes`, `free_bytes = total_free_bytes` e `largest_free_block_bytes = largest_free_block`. O ramo PSRAM usa o mesmo mecanismo; sem `CONFIG_SPIRAM`, os valores continuam `null/not_applicable`. A emissão permanece uma chamada `printf` por linha.

Confirmei a API no header oficial do ESP-IDF v5.2.6: `esp_heap_caps.h` inclui `multi_heap.h` e declara `heap_caps_get_info(multi_heap_info_t *, uint32_t)`; o struct contém os campos usados. Referências primárias: [esp_heap_caps.h v5.2.6](https://github.com/espressif/esp-idf/blob/v5.2.6/components/heap/include/esp_heap_caps.h#L225-L238) e [multi_heap.h v5.2.6](https://github.com/espressif/esp-idf/blob/v5.2.6/components/heap/include/multi_heap.h#L148-L157).

O stub host fornece snapshots mutáveis coerentes em cada chamada: internal mantém soma 2048 e PSRAM 4096, com free e largest variando por evento. Os getters antigos separados devolvem um exemplo intercalado impossível (`free=800`, depois `largest=900`); o harness falha se total/largest separados forem chamados e exige exatamente uma chamada `heap_caps_get_info` por evento e capacidade habilitada. As assertions também verificam `largest <= free <= total` nos dois ramos. O teste exercita 10 estágios do runner, fases, linhas atômicas, liberação existente e 30 repetições; não muda parser, contrato de medição, timer ou logger.

## Verificação e identidade

Teste focalizado `test_e1_runner.py`: 9 testes aprovados, exit code 0; log SHA-256 `31d811e58c82c5460923e9669e3b0d463a1a468500cd64d13dd5d893cff84c98` em `/tmp/wasmhome-native-fixes-r4-20261013/test_e1_runner-r4-final.log`.

Suíte integral no snapshot R4: 81 testes, 80 aprovados e 1 skip, exit code 0; o único skip é o caso de junction Windows. Log SHA-256 `4cbd8799949108f1a6913815a62278b8b4824ff84c2c9524a9ae62831d102653` em `/tmp/wasmhome-native-fixes-r4-20261013/unittest-full-r4-final.log`. `git diff --check` passou.

Hashes finais dos dois arquivos desta alteração:

- `main/main.c`: `09d456e33ac1723300c684fc3f6ab71a9a7e6b7577993c0d0ff0fa6728d4f16e`
- `tests/test_e1_runner.py`: `34d7332e993a1097028f45de4f168514b8a938b45a167e23a11d1b1d030307c2`

Snapshot completo conjunto do worktree, tracked + untracked contra HEAD, SHA-256 `7ff84b06bd4892a41b47676d53dd5c19873c3ff492dbe316f4756ec0c5a586d4`, 108.874 bytes: `/tmp/wasmhome-native-fixes-r4-20261013/working-tree-final-r4-final.diff`. Ele inclui trabalho paralelo no worktree e não é diff exclusivo desta correção. O r3 original permanece preservado sem alteração em `/tmp/wasmhome-native-fixes-r3-20261013/` (diff r3 SHA-256 `0443a2c36cc6b8f74277369e1fdf1af65f5ede1955761d14e178b5e47eb26f1d`).

Não rodei build ESP-IDF nem acessei placa/serial; o adaptador dos serviços e o snapshot integrado continuam aguardando revisão B dirigida ao r4.

# Revisão independente do snapshot r4 — instrumentação de heap E1

Data: 13/10/2026. Checkout revisado: `/home/haroldocurti/Documents/wasmhome`, HEAD `0ba2faab16dc9671f89a663eea8e6d0d1f528997`. O diff-snapshot fornecido tem SHA-256 `7ff84b06bd4892a41b47676d53dd5c19873c3ff492dbe316f4756ec0c5a586d4`; os arquivos em foco correspondem aos hashes anunciados: `main/main.c` `09d456e33ac1723300c684fc3f6ab71a9a7e6b7577993c0d0ff0fa6728d4f16e`, `tests/test_e1_runner.py` `34d7332e993a1097028f45de4f168514b8a938b45a167e23a11d1b1d030307c2`.

## Achado bloqueador

`main/main.c:65` e `:76` definem `total_bytes` como `total_free_bytes + total_allocated_bytes` do `multi_heap_info_t`. Essa soma não representa capacidade fixa do heap no ESP-IDF 5.2.6: `multi_heap_get_info_impl` calcula `allocated_blocks * tlsf_alloc_overhead()` e subtrai esse overhead de `total_allocated_bytes`; o próprio comentário diz que o overhead por bloco não integra os bytes alocados. A soma pode então mudar quando muda a quantidade de blocos alocados, embora a capacidade do heap permaneça constante. O ramo interno e o ramo PSRAM compartilham o problema.

Fonte primária verificada: [implementação oficial do ESP-IDF 5.2.6, `multi_heap.c`](https://raw.githubusercontent.com/espressif/esp-idf/v5.2.6/components/heap/multi_heap.c), linhas 393–399. O validador atualizado exige capacidade interna constante por boot em `tools/validate_e1_device.py:270–273`, de modo que a divergência pode rejeitar uma sessão válida. A alteração do snapshot também cria fixtures que fixam artificialmente `free + allocated` em 2048/4096, ocultando a semântica real da API.

Correção provável: obter a capacidade fixa com `heap_caps_get_total_size(cap)` e conservar uma única chamada `heap_caps_get_info()` por capacidade para os campos livres/maior bloco; atualizar stubs para variar `total_allocated_bytes` conforme a contagem de blocos e acrescentar teste que confirme o `total_bytes` estável. Não alterei o repositório.

## Verificação executada

- Teste focalizado (`test_e1_runner.py`), com CMake 4.4.4 disponível: 9 aprovados, sem skips, exit 0. Log `/tmp/wasmhome-native-fixes-r4-20261013-review-with-toolchain.log`, SHA-256 `b64fad1a4f4789004c72d221c5fbff305d1d5d28c9bc38de813c6e9cc382ced5`.
- Suíte completa no snapshot compartilhado, com CMake, PowerShell e biblioteca do codec no PATH/ambiente: 81 testes, 80 aprovados e 1 skip de junction Windows, exit 0. Log `/tmp/wasmhome-native-fixes-r4-20261013-review-full-with-toolchain.log`, SHA-256 `08d280da3d158004562360312e8328a494a68248e881cddb7126e5aa282c6fbd`.
- `git diff --check -- main/main.c tests/test_e1_runner.py`: passou.

Uma tentativa inicial da suíte completa sem PowerShell/codec no PATH teve uma falha de preflight por ausência de PowerShell e cinco skips. Reexecutei com o ambiente documentado; somente o resultado green acima é considerado nesta revisão. Testes host aprovados não removem o bloqueador semântico porque o stub força a soma constante que a API real não promete.

**Parecer:** bloquear a aceitação/publicação do r4 até corrigir a origem de `total_bytes` e tornar os testes representativos de `multi_heap_info_t` real. Não houve build ESP-IDF, flash nem acesso à placa nesta revisão.

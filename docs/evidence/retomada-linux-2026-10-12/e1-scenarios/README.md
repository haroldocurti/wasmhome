# Builds E1 A/B/C no CT107 — 12/10/2026

Complemento à [retomada Linux](../README.md), com artefatos, builds e inspeção
Xtensa dos três cenários no mesmo snapshot WasmHome.

## Proveniência

- WasmHome: commit experimental `801f9381da1a8ed0895adc88e8cd03f3a1df6707`,
  árvore `c728d9ed348e7fdefe4dac403a4c42b6916e704a`; árvore limpa no CT107.
  O snapshot deriva do checkout raso em `a7c4f72` e contém as correções desta
  retomada.
- ESP-IDF 5.2.6: commit `9ef24e3e2a2c96e720d83c574a3f8699177573da`, alvo
  `esp32s3`. Runtime `wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.1, upstream
  `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, hash
  `7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`.
- Os cenários usaram diretórios de build e `sdkconfig` distintos, clock de
  240 MHz, benchmark habilitado, selftests desabilitados e workload `-O3`.
  Um job foi usado no CT107. Os hashes das configurações efetivas e dos
  `compile_commands.json` estão em `builds/*/image-hashes.txt`; os valores
  completos de `sdkconfig` não foram exportados.

## Resultados dos builds

Todos os recibos têm estado `build_checked_not_device_validated`. O ELF e o BIN
completos permanecem no CT107; os hashes abaixo e os recibos JSON foram copiados
para este pacote.

| Cenário | Etapas | Configuração validada | SHA-256 do ELF | SHA-256 do BIN |
|---|---:|---|---|---|
| A nativo | 994 | `benchmark_run` presente; sem símbolos do runtime WAMR | `e652811f3ab89a504e922dae328dac0489fcf7e7f8f6df429d21f210dc683c52` | `418636ad0a2a6a61857efeb6c2a6f44cddd89ff25f53dda58e2534406ff0a9dd` |
| B interpretado | 1.048 | intérprete clássico; fast interpreter e AOT desativados | `d15565f136a26cf4e9d213533a40dd7d5eba8ad3f0654ab941ac00c4911c5dc5` | `0abe28dc75b9affbaf89c148ca3b2c32dc7ae5c7c5550cc1b578bc9f0721a6fe` |
| C AOT | 1.050 | AOT ligado, intérprete desligado, validator presente | `6d5f28516ad2e8fea5282643e67e4badef649c7abb031d004f78fb26e619fa1e` | `18f73a8f108baf2fc61eb178b2b2e948ad8155d83c907ed6d9322913e72ce9d3` |

Os recibos `builds/{native,interp,aot}/e1-build.json` registram revisão,
identidade do IDF/runtime, hash do ELF, configuração e comandos de compilação.
Os logs integrais estão em `logs/`. A configuração B confirmou
`WASM_ENABLE_INTERP=1`, `WASM_ENABLE_FAST_INTERP=0` e ausência efetiva de AOT;
C confirmou `WASM_ENABLE_AOT=1`, `WASM_ENABLE_INTERP=0`,
`WASM_ENABLE_AOT_VALIDATOR=1` e `WASM_CONFIGURABLE_BOUNDS_CHECKS=0`. O artefato
AOT usa bounds checks de módulo habilitados em `wamrc`.

## Artefatos e repetição

O manifest de R6 é `artifacts/build-manifest-r6.json`; ele fixa WAMR AOT v5,
Wasm de 174 bytes e AOT de 812 bytes. `artifacts/reproduction-r6-r7.json`
compara os dois builds limpos: Wasm, AOT, assembly e os dois fontes C embutidos
têm hashes idênticos. Os manifests diferem porque incluem caminhos de checkout
distintos.

O R1 falhou porque `wamrc` 2.4.5 rejeita `-O3`; a receita foi corrigida para
`--opt-level=3`. Os logs R1–R7 preservam as tentativas e a repetição. Na
configuração inicial, o passe de descoberta do IDF não recebeu o cenário do
projeto e omitiu `wasm-micro-runtime` de `main.req`; depois da propagação pelo
ambiente, `main.req` incluiu o componente e os builds B/C compilaram.

O primeiro B compilou, mas a validação rejeitou a macro AOT desativada porque o
WAMR omite `-DWASM_ENABLE_AOT=0`; os guards C do WAMR tratam a macro indefinida
como zero. O validador agora aceita ausência somente para opções esperadas
desativadas e exige o define ativo quando a opção está ligada. A nova validação
de B passou. No build A, a checagem inicial também encontrou um único
`python_version_checker.cpython-313.pyc` ignorado na árvore do IDF. O arquivo foi
inspecionado, removido sozinho, e a validação repetida passou com o IDF limpo.
Esses primeiros resultados e as correções estão preservados nos logs.

## Inspeção do AOT em Xtensa

`benchmark.aot` é um contêiner WAMR, não um ELF. Para inspecionar o código, o
mesmo `benchmark.wasm` foi enviado a `wamrc` com os flags do manifest e
`--format=object`. O resultado é ELF 32-bit relocável Xtensa, SHA-256
`5f030fce3499b11a85b47f4166c0148347938af218405f7581fdb2ec44cd7b3f`.

Em [`disassembly.txt`](aot-object/disassembly.txt), `aot_func_internal#0`
carrega `0x7d0` (2.000) como limite em `0x48`, incrementa o contador em `0x73` e
faz `bne` de `0x75` para `0x4b`. O laço do workload está presente no código
Xtensa e não foi substituído por uma constante. O wrapper `aot_func#0` também
contém desvios de limite e caminho de exceção. Em A, o disassembly de
[`benchmark_run`](builds/native/benchmark_run-disassembly.txt) contém
`movi a10, 0x7d0` e a instrução Xtensa `loop` sobre o corpo da função. Em B, o
assembly Wasm contém a instrução `loop`. Assim, a inspeção estática dos três formatos
confirma as 2.000 iterações no workload. Os hashes de objeto, símbolos e
disassembly estão junto aos respectivos arquivos.

## Limites

Isto comprova geração, compilação e inspeção estática no CT107. Não houve flash,
execução serial ou checksum no ESP32-S3. O recibo `wamrc` continua unsigned e
aguarda atestado/rebuild independente. Nenhuma suíte de testes foi executada;
#7 e #9 permanecem abertas.

Os arquivos ELF/BIN completos e os diretórios de build ficam no CT107 em
`/home/wasmhome/.local/toolchains/wasmhome-e1-20261012-linux-artifacts-a7c4f72/run-idf-requirements-r6/`.
`logs/` inclui falhas anteriores sem substituir os registros originais.

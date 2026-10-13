# Retomada Linux — toolchain e CT107 (12/10/2026)

Complemento de execução à [retomada de 11/10](../retomada-linux-2026-10-11/README.md). O checkout local partiu de `main` em `a7c4f72dad00bd8e9c9e116a7ef88635b2588ca3`, árvore `f8e3ad5b4a37391bdc5142b8e1e67397863011a8`. Por falta de autenticação Git na CLI, ele é raso. A execução de 12/10 deixa alterações locais de código e documentação; os builds E1 usaram o snapshot limpo `801f9381da1a8ed0895adc88e8cd03f3a1df6707`, árvore `c728d9ed348e7fdefe4dac403a4c42b6916e704a`.

## Resultado

- O host Omarchy 4.0.4/Arch x86_64 tinha GCC/G++ 16.2.1, glibc 2.44, 27 GiB de RAM e 16 CPUs. CMake 4.4.4 e Ninja 1.13.2 portáteis foram usados sem instalação de pacotes de sistema.
- O pacote WAMR `2.4.5-wasmhome.1` foi verificado pelo hash `7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`.
- LLVM Espressif `344b928e67bfc1d07cb150609c55548e86a9bf19` compilou em Release com X86 e Xtensa, LTO desligado e um job. O workaround explícito `-include cstdint` corrigiu a falha de cabeçalhos no GCC 16; o código-fonte permaneceu limpo.
- O primeiro `wamrc` compilou, mas não executou no CT107: o link no Arch escolheu símbolos de `libm` das versões glibc 2.43/2.44, enquanto o guest usa glibc 2.41. O log de probes R4 preserva essa falha.
- Para R5, foi copiado somente o `libm.so.6` 2.41 do CT107 para a área de build local (SHA-256 `759506ba4ed410f7b926704547db1bd31896ffb4f1a79d6b75884af239bcf21b`) e usado no caminho de busca do link. Nenhum pacote ou arquivo de biblioteca do sistema foi alterado no CT107.
- O recibo R5 registra o binário `wamrc 2.4.5`, SHA-256 `ff1bf482570dbab67521e0bbb0c682fdbd74f5e784f364eb34bfc9de735861fb`, LLVM limpo no pin e 98 archives LLVM ligados. O binário exige no máximo glibc 2.38.
- Os logs contêm 265 linhas de avisos no build LLVM e 4 no build WAMR sob GCC 16; não houve erro fatal.
- O binário e recibo R5 foram copiados para `/home/wasmhome/.local/toolchains/wasmhome-e1-20261012-linux-r5`. `SHA256SUMS` foi conferido no host de transporte e no CT107. No guest, `wamrc --version` retornou `wamrc 2.4.5`; `ldd` resolveu `libm`, `libz`, `libzstd`, `libstdc++`, `libgcc_s` e `libc`, sem bibliotecas ausentes. As consultas listaram os alvos `xtensa`, `x86_64`, `i386`, CPUs Xtensa e features. Os comandos de ajuda imprimem a lista e retornam status 1 por comportamento do `wamrc`.
- R4 permanece no diretório próprio do CT107 como tentativa incompatível; use R5 para consultas posteriores.
- A continuação gerou e repetiu os artefatos Wasm/AOT em duas árvores do CT107. As cinco saídas tiveram hashes idênticos; os manifests retêm caminhos de checkout distintos. A receita usa `--opt-level=3`, que é a opção aceita por `wamrc` 2.4.5.
- Firmware A/B/C compilado e validado no mesmo snapshot WasmHome, em diretórios e configurações isolados, com ESP-IDF 5.2.6/ESP32-S3. A inspeção do objeto ELF Xtensa derivado do Wasm confirmou o laço de 2.000 iterações. Os recibos, logs, hashes, manifest e disassembly estão em [e1-scenarios](e1-scenarios/README.md).

## Limites e próximo gate

Este checkpoint valida o executável host no CT107, gera e repete artefatos Wasm/AOT, compila os três cenários e inspeciona o objeto AOT Xtensa. Não demonstra execução no MCU: nenhum firmware foi gravado e nenhum checksum foi executado no ESP32-S3. O recibo do compilador permanece unsigned e aguarda rebuild/atestado independente. #7 e #9 permanecem abertas. Nenhuma suíte de testes foi executada.

`wamrc-build-receipt-r5.json` é unsigned. `SHA256SUMS` detecta alteração dos arquivos preservados, mas não substitui uma assinatura independente. Caminhos absolutos no recibo e em `commands-r5.json` descrevem o host de build de 12/10.

## Arquivos

- `commands-r5.json`: comandos efetivamente registrados para a linkagem compatível.
- `llvm-build-r4.log`, `llvm-configure-r4.log`: build do LLVM e configuração.
- `wamrc-configure-r5.log`, `wamrc-build-r5.log`: configuração e build R5.
- `wamrc-build-receipt-r5.json`: recibo emitido por `tools/record_wamrc_build.py`.
- `ct107-probes-r4.log`, `ct107-probes-r5.log`: comparação de execução e dependências no guest.
- `glibc-compatibility.json`: versões, hashes e destinos de R4/R5.
- `e1-scenarios/`: manifests e payloads, recibos de builds A/B/C, logs de tentativas/correções, hashes de ELF/BIN e disassembly Xtensa.
- `SHA256SUMS`: hashes dos arquivos deste complemento, exceto o próprio índice.

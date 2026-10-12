# Preparo E1 no CT107 — 10/10/2026

Revisão local candidata `187ff96279ed8e6d3da14b7663ce5b5e856dff49`, árvore
`70580c660a5642e9f7226c0d9813b805ee8b3c64`. Este recibo é ignorado pelo Git.
O mapa completo continua em `docs/checklist-execucao.md`; esta lista registra
somente o avanço local da toolchain depois da integração de #6.

- [x] Confirmar CT107, usuário, diretórios exclusivos e Python do sistema.
- [x] Instalar exatamente 21 pacotes novos, sem upgrade/removal de outros.
- [x] Preparar venv isolado e 57 distribuições Python por URLs/hashes congelados.
- [x] Validar pip check, imports e dependências oficiais IDF com constraints.
- [x] Adquirir e executar WASI-SDK 27 e PowerShell 7.6.6 com hashes conferidos.
- [x] Adquirir ESP-IDF 5.2.6, revisão/submódulos e ferramentas fixadas.
- [x] Transferir a revisão candidata por bundle Git; confirmar commit/tree limpos.
- [x] Materializar e conferir runtime 2.4.5-wasmhome.1/AOT v5 no CT107.
- [x] Configurar e compilar nativo ESP32-S3 em diretório novo, um job por vez.
- [x] Validar configuração/símbolos, otimização -O3 e disassembly do workload.
- [x] Preservar dependencies.lock, os 18 arquivos managed component e seus hashes.
- [x] Confrontar os 16 hashes do CHECKSUMS.json e as 4 fontes externas compiladas.
- [x] Receber revisão Luna da coleta nativa: sem bloqueio para afirmar build local concluído.
- [ ] Resolver capacidade e autoridade do Linux doador; compilar wamrc real.
- [ ] Transferir/conferir ABI e recibo do wamrc no CT107.
- [ ] Gerar o conjunto A/B/C e reproduzir em outra árvore/diretório limpo.
- [ ] Compilar B/C, comprovar checksum na placa e produzir evidências de #9.
- [ ] Publicar/integrar #7 sob autoridade específica, após revisão dos resultados.

O build nativo terminou com exit 0. O app tem 737088 bytes e SHA-256
`bef4b0b0ce7061d3f2cb693a8d3f0240a95d84a80fa31b5379aaa3bb2e74f73c`.
O ELF tem SHA-256
`3ffca7b09a450e0ac63f5e0d1ea67fb3d672eb55684ad9b6d7953898f90df500`.
`benchmark_run` contém o laço de 2000 iterações e a aritmética do workload;
não foi reduzido ao retorno de uma constante. Isso não prova execução na placa.

O componente externo é `espressif/onewire_bus` 1.0.2, originado do registry
`https://components.espressif.com/`, component_hash
`c4c781940d8bd988432b47ac3ee68479c69e14e01fdfba0b61365b233a3ba5d6`.
Lock coletado SHA-256
`aa459686d7f8cb76b051bfae72d2cd1c7005f2a3f2460937ccb567b0e4af94ad`.
O tar preservado contém lock e todos os arquivos usados; o inventário e os
compile_commands coletados vinculam as quatro fontes compiladas ao pacote.

Python do sistema permaneceu 3.13.5, pacote 3.13.5-2 e binário SHA-256
`e59d0124ff06c248546876e01fcfb1ea3cda63534949f94a9372bfcfe3bfc3f5`.
O configure/build nativo é uma prova específica de compatibilidade neste
ambiente com o venv congelado; não aceita automaticamente os builds B/C.

A revisão Luna em `revisao-native-ct107.md` (SHA-256
`ec7200a9848ddd2e8aa03808b45f93da65ab46c6cde316a9e8d1fa4945f93176`)
confirmou a afirmação limitada de build nativo concluído,
os hashes locais, o componente externo e o laço de 2000 iterações. O build-check
bruto conserva a pendência de disassembly escrita antes da coleta; ela está
resolvida somente para o nativo por este complemento e pelo parecer. AOT,
execução física e checksum continuam pendentes. Preservar o recibo bruto como
snapshot histórico; não publicá-lo como estado final sem este complemento.

Evidências: `build-native-ct107.log`, `native-final-ct107/` (build-check, comandos,
inventário, lock, pacote externo, disassembly e verificação do coordenador).
`metadata-r3/` preserva compile_commands completo, sdkconfig e CMakeCache.
Não tratar a cópia incompleta de ELF feita por SCP como binário verificado.
A transferência posterior R4 concluiu a cópia local em
`native-final-ct107/elf-r4/wasmhome_bench.elf`: 9196308 bytes, SHA-256 igual ao
ELF conferido no CT107, com recibo em `elf-r4/transfer-receipt.json`.
O coordenador executou objdump Xtensa local sobre esse ELF; todos os tokens de
endereços, opcodes, instruções e operandos do workload coincidiram com a coleta
remota, desconsiderando somente espaçamento de colunas entre os executáveis
Windows/Linux. Recibo: `elf-r4/root-local-elf-check.json`.
As transferências interrompidas e a
colisão inicial de saída foram preservadas; a coleta final usou um diretório
novo, hashes verificados e blocos comprimidos em ASCII com timeout.

Não houve flash, nova coleta física, aceite/encerramento #7 ou #9, publicação Git
ou alteração de configuração de recursos do Proxmox por este preparo.

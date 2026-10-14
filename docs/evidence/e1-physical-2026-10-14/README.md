# Aceite físico E1 — 14/10/2026

A issue #9 atende à prova mínima de viabilidade: a mesma função executou no
ESP32-S3 em nativo, interpretador clássico e AOT. O ensaio fechado
`abc-6317fa9-run01` usou fonte `6317fa9a39e5d99e8ba1293dae80da801d2b6d55`,
árvore `e0eeacede32eb98d0bc4444aea044f531b875f8b`, runtime
`2.4.5-wasmhome.3` e ESP-IDF 5.2.6. A sessão terminou com status `passed`,
três etapas e zero erros. Dois agentes Luna xhigh revisaram builds e restauração;
o verificador offline conferiu logs, artefatos e checksum.

| Cenário | Aquecimentos | Chamadas medidas | Checksum | Eventos de memória | Boots |
|---|---:|---:|---|---:|---:|
| Nativo | 5 | 30 | DBB2F0D4 | 67 | 1 |
| Interpretador clássico | 5 | 30 | DBB2F0D4 | 67 | 1 |
| AOT | 5 | 30 | DBB2F0D4 | 67 | 1 |

[Builds e proveniência](builds/port3-public-metadata.md) identificam ELF/BIN,
configurações e recibos por SHA-256, além do helper de relocação incorporado no
objeto Xtensa real. Wasm: 174 bytes; AOT: 812 bytes. O compilador foi recompilado
com os 98 archives LLVM originais verificados e reutilizados. Não houve nova
compilação de LLVM. Validator AOT e bounds checks permaneceram habilitados.
A suíte local da correção teve 96 casos, 95 aprovados e um skip de junction
Windows; os logs correspondentes estão na [validação do aliasing](../aot-alias-fix-2026-10-14/README.md).

## Restauração e limites

As três leituras integrais após os cenários mostraram zero bytes alterados fora
de `ota_0`. Apenas essa aplicação foi gravada/restaurada. Seu readback de 7 MiB
coincidiu com `67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b`.
A flash completa de 16 MiB, antes do boot original, coincidiu com o backup
`4fcc8eb606747bd4cbb016de24e06a12e08d02fbe9628143b9a933a118c74ed3`.

Após o boot original, uma leitura separada produziu
`bcbd8b0f77e5d34d6d6d08a19fc9b40558da264067f81a39ed61877cb6636a5c`:
1.961 bytes diferentes, todos em NVS (`0x9000`–`0xEFFF`, primeiro `0xB038`,
último `0xDC3D`); zero diferenças fora dessa região. A aplicação original
permaneceu idêntica. A NVS foi preservada, sem apagamento e sem atribuir causa
exclusiva à deriva. A captura final confirmou `6d7382b-dirty` executando de
`0x20000`, depois da última leitura; não se afirma igualdade integral após
essa inicialização final.

[Parecer independente, consumo inicial e memória por fase](physical/physical-abc-final-review.md)
registram os três critérios, os pins do instrumento e as medições.

O resultado bruto privado tem SHA-256
`3a865350633e761ed97959d1062881293e49095344dff5502429fc017382585c`.
A captura original final tem SHA-256
`e95f8e4415fbb196e4a44e423a2036205274b819835af8b8e5e782f22ca44a9b`.
BIN/ELF, configurações completas, flash, credenciais e UART bruta permanecem
privados. Esta pasta publica resumos e hashes verificáveis.

É uma sessão com 30 chamadas por cenário; não são 30 atualizações experimentais
independentes. Valores de heap livre não representam consumo total de RAM.
A prova não demonstra dois módulos simultâneos, troca de controle, OTA,
resiliência ou a campanha completa. Esses passos começam pela #12.

As [tentativas anteriores](../aot-run-8b3bddd-2026-10-14/README.md) e os
[diagnósticos da correção](../aot-alias-fix-2026-10-14/README.md) preservam falhas
históricas e limites. A #11 tem [aceite separado](../native-services-2026-10-14/README.md).

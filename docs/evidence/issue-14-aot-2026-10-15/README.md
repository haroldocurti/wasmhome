# Issue #14 — dois módulos AOT simultâneos

**Resultado:** aceite físico aprovado no ESP32-S3-N16R8. Telemetria e controle
executaram ao mesmo tempo em módulos AOT, com controle v1, atualização do sensor
e loopback GPIO observados durante uma janela de 60 segundos.

## Build e configuração

O firmware foi construído do commit `298bfaa33584e64763ca97b35407f662cca7938b`
(árvore `aef782e794ff86c8f4181a3281b2d035f92c4bc9`) com ESP-IDF 5.2.6 no commit
`9ef24e3e2a2c96e720d83c574a3f8699177573da`; a árvore é
`e74c4c35593551e612df306248b403a92ec8a403`. Os 24 submódulos foram conferidos
antes do build; o CMake cache e os comandos de compilação apontam para esse
checkout fixado.

O runtime usado foi `2.4.5-wasmhome.3` (pacote SHA-256
`7928f0852db4c43b1e2d7ae19ac957d0cddae77e458046612c6100e9e9dbee39`). Os
módulos AOT foram gerados pelo `wamrc 2.4.5`, SHA-256
`05689efa0236defc1a61b2931a251b1e7c7f6c184d0d82a21d384f9313fa7760`, receipt
SHA-256 `1a690cb7d20e8d2563664d4eddbb9758fc65cbda152c8f9cbc1c9b885dbe8f76`, e
WASI SDK 27. O gerador usou `--target=xtensa --bounds-checks=1
--disable-llvm-lto --opt-level=3`.

| Artefato privado | SHA-256 |
|---|---|
| Firmware BIN gravado em OTA0 | `3f7891d1f3f422dafafbc8b820cb430965833fa29f6dd1f59e807f182a719a7b` |
| ELF do firmware | `1854af9256ad46e2af542f0a3ee8b6e0f30867aa02107d7fd2a9a5defb858d3e` |
| [Trecho sanitizado da configuração e auditoria AOT](aot-config-sanitized.txt) | `67388f44f74c11f834d48593b48e8f8b81e676834be3062f00f0b2b9a4ee961a` |
| Telemetria AOT v1 | `e8c6e6ff9c7fc01445adc32b181d755252b99758c01af68970b51d6ad445ea63` |
| Controle AOT v1 (carregado no ensaio) | `a9c4c7e440ee37e7b53dbcb6d2e8009dd00e4a6fe278180bb9b9ef485e0a5746` |
| Controle AOT v2 (incluído, não ativado neste ensaio) | `293d78b1af5e0c7e76593d9b8e755692ee311d8427c43d94673ddfd499d5bf53` |

A configuração selecionou `module-aot`, habilitou o WAMR AOT e os serviços
modulares, e escolheu controle v1. O interpretador não foi compilado no firmware
de captura. A auditoria do build capturado, registrada no trecho sanitizado
abaixo, confirma AOT nas 38 unidades WAMR, ausência de fontes e símbolos do
interpretador, inclusão dos módulos AOT sem módulos Wasm, presença do
`aot_module_validate` e bounds checks ativos. O BIN tem 829.280 bytes e cabe na
partição física OTA0 de 7 MiB. A configuração `sdkconfig` completa permanece
privada, pois contém credenciais e endereços locais.

## Captura física

O observador externo foi vinculado à interface local do host e apontou para a
placa; os endereços e a configuração privada não são publicados. A janela durou
60 s, com controles a cada 100 ms, timeout de 300 ms e idade máxima do sample de
1.000 ms. Um boot foi observado.

| Medida | Resultado |
|---|---:|
| Telemetrias válidas e recentes | 480 |
| Intervalo máximo entre telemetrias válidas | 188,5 ms |
| Samples distintos do sensor | 97, IDs 4–100 |
| Intervalo mediano/máximo entre novos samples | 499,8 / 529,9 ms |
| Idade mediana/máxima dos samples | 218 / 418 ms |
| Controles enviados / respostas válidas | 477 / 477 |
| Controles perdidos ou fora do prazo | 0 |
| Lacunas de sequência | 0 |
| Versão informada nas respostas de controle | v1 em 477/477 |
| Latência de controle mediana / p95 / máxima | 10,9 / 33,5 / 82,4 ms |
| Intervalo mediano/máximo entre respostas de controle válidas | 100,0 / 172,2 ms |
| Pacotes PCAP capturados / descartados pelo kernel | 1.451 / 0 |

As respostas de controle corresponderam aos três estímulos de temperatura:
159 resultados solicitaram e observaram nível baixo; 318 solicitaram e
observaram nível alto. O estado reportado pelo firmware confirma o loopback
GPIO5→GPIO6. Esse é o readback do pino pelo firmware, não uma medição elétrica
independente por osciloscópio. A telemetria não carrega uma versão do módulo; a
versão do módulo de telemetria fica identificada pelo artefato AOT e pela UART,
enquanto `module_version=1` é conferido nas respostas de controle.

A UART registrou `module_ready` para telemetria e controle e `services_active`
com status `ok`. Houve duas mensagens WAMR de limite de memória: o firmware
solicita até duas páginas, enquanto estes módulos sem `memory.grow` têm máximo
efetivo de uma página (64 KiB). O runtime limita o valor ao máximo do módulo; os
dois módulos instanciaram e operaram normalmente. Isso não bloqueia o aceite. Em
um ajuste futuro, `CONFIG_WASMHOME_MODULE_MAX_MEMORY_PAGES=1` alinha o pedido ao
limite efetivo e elimina o aviso.

Uma tentativa preliminar foi descartada porque a configuração privada de rede
daquela imagem estava incompleta; não houve pacotes recebidos nem controles
enviados. A flash foi comparada integralmente ao backup e restaurada antes da
captura aceita. A tentativa corrigida acima usa configuração validada e fica em
diretório separado.

## Restauração e privacidade

O backup inicial da flash completa tem SHA-256
`242b3ee70cd2c32ed6b033f34319606f616abc70a3daf51bd34703224d1ba080`. Após a
captura aceita, 813.324 bytes diferiam do backup, todos dentro de OTA0; 203
setores de 4 KiB foram tocados entre `0x20000` e `0xeafff`. Bootloader, tabela,
NVS, `otadata`, `phy_init`, OTA1 e o restante da flash tinham zero diferenças.

A partição OTA0 completa (7.340.032 bytes) foi restaurada do backup original
SHA-256 `67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b`;
`esptool` confirmou o hash após gravar. A leitura final de 16 MiB coincide byte
a byte com o backup inicial e tem o mesmo SHA-256
`242b3ee70cd2c32ed6b033f34319606f616abc70a3daf51bd34703224d1ba080`. A placa
ficou no bootloader ROM depois da leitura final.

A regra UFW temporária para UDP 3333 foi removida após a captura. As hashes das
regras IPv4/IPv6 antes do ensaio foram
`125c8b51731ef850f4ff490895ef1849205b940a5c85e351e01be0d3b90ddb2a` e
`76d8928cc7072bb1f1fd1664de4c47ce58470581b44a7dcac0f4b24ff082c26f`; as hashes
durante a regra foram `995e6a68863b47b3bce47e62ed0e1f5733e03bd43ea1d0cdf1f98557c0961c5c`
e `76d8928cc7072bb1f1fd1664de4c47ce58470581b44a7dcac0f4b24ff082c26f`. As hashes
finais foram iguais às iniciais e `ufw status` não lista mais a porta. O estado
temporário só permitia tráfego UDP da placa para o host nesta porta.

Os hashes privados da captura aceita são PCAP
`1f62a7d99ec4e1f00c6bb5dec24306c1a8e5d529bc18d50eec38b39d4ccf974b`, JSONL do
observador `87b221fea0a7779f53f61d14657d2548740d1949abbb160ba999d47f91f9076d`,
resumo do observador
`189c1493278056f29c549f5d9ba2288a52290c306003469a66dc7effcd872ebb` e UART
`644e995e46238b7661b0a4113976624b589d07090b7d1ef9d5ae1f14860ef6aa`. A saída
de encerramento do tcpdump preservada do terminal de captura (1.451 capturados,
1.457 recebidos pelo filtro, zero descartes de kernel) tem SHA-256
`87ae068900f0a8634be35642df235ea96252265e056cfbbf5828e7b3c739cdcd`.

Os BIN/ELF, `sdkconfig`, flash, PCAP, UART bruta, credenciais, SSID e endereços
físicos permanecem no arquivo privado local da sessão; este repositório contém
somente os resumos e hashes necessários à auditoria.

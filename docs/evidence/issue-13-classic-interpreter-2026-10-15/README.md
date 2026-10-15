# Issue #13 — telemetria e controle no interpretador clássico

**Estado: prova física de ponta a ponta pendente.** O ESP32-S3 iniciou o
firmware de interpretador clássico e os dois módulos ficaram prontos, mas o
observador não recebeu datagramas durante a execução sincronizada. Este registro
não marca os critérios físicos da #13 como concluídos.

## Perfil gravado

- ESP32-S3, interpretador clássico WAMR habilitado e AOT desabilitado.
- Flags relevantes da configuração efetiva: `CONFIG_IDF_TARGET_ESP32S3=y`,
  `CONFIG_WAMR_ENABLE_INTERP=y`, `CONFIG_WAMR_INTERP_CLASSIC=y` e
  `# CONFIG_WAMR_ENABLE_AOT is not set`.
- Serviços modulares ativos: módulo de telemetria e módulo de controle v1.
- BIN: 843.424 bytes, SHA-256
  `1682f908277cef22778a74acfd43b0d1d60191e764406cd6445df401a5bd5c64`.
- ELF SHA-256: `f20ee30c878e0b2e56499055b7a864083d42be7b951121b12bdb1610b0eef111`.
- `sdkconfig` efetiva SHA-256:
  `b55fd716d61b04bb03eb02786aa8aafd0a2859555c6de753adcb366d43c9e40e`.
- Os hashes acima identificam a configuração e os artefatos do build usado. O
  log privado `flash-module-interp-final.log` registra a gravação de 843.424
  bytes no endereço `0x20000` (partição `ota_0`) e `Hash of data verified`.
  Esse log não contém o nome nem o hash do BIN; a correlação com o build acima
  é feita pelos registros privados da sessão, que documentam o comando e o
  artefato usados. Os arquivos brutos de captura permanecem fora do repositório.

## Execução física em 15/10/2026

As capturas UART correlacionadas cobrem os resets e registram `boot_observed`,
`platform_initialized`, `module_ready` para os dois módulos,
`services_active`, `first_telemetry_operation`, `workers_ready`,
`module_manager_ready`, `wifi_ip_acquired` e `udp_bound`, todos sem erro. Não
aparecem falhas de abertura ou envio UDP, desconexão Wi-Fi, nem reinício durante
a janela observada.

Foram feitas duas execuções de 30 segundos, ambas correlacionadas com UART ativa
desde antes do reset. Na primeira, o observador escutou em todas as interfaces
locais (`0.0.0.0:3333`), com o peer derivado da UART; na segunda, escutou no
endereço local configurado como destino no firmware. O bind curinga da primeira
execução é válido para receber datagramas destinados a qualquer interface local;
portanto, a diferença de bind não explica o resultado. O lease DHCP e o gateway
registrados na UART coincidiram nas duas sessões.

As duas execuções registraram somente `session_start`, 300 ciclos
`awaiting_boot` e `session_end`: zero datagramas recebidos, zero telemetrias
decodificadas e zero controles enviados. O boot, a prontidão dos módulos e o
bind local do socket estão provados; `udp_bound` não confirma o envio de cada
datagrama. A UART não registra confirmação individual de `sendto`, então os
dados disponíveis não distinguem ausência de envio pelo dispositivo de perda
ou isolamento no caminho Wi-Fi–Ethernet.

Uma captura passiva na interface Ethernet não foi possível: o host negou a
abertura do socket de captura (`Operation not permitted`). As duas UARTs
correlacionadas anunciam BSSIDs diferentes sob o mesmo SSID; isso é uma pista de
mudança de ponto de acesso, mas não prova a causa. Sem captura no AP/roteador ou
no host, o ponto da perda continua desconhecido.

## Evidência anterior da #12

A captura estável documentada em
[`issue-12-allocator-alignment-2026-10-15`](../issue-12-allocator-alignment-2026-10-15/README.md)
é apoio, não aceite independente para a #13. Ela registrou 84 telemetrias
válidas, 78 resultados em 79 controles e um lote separado com 10/10 respostas.
O JSONL privado dessa captura também registra bind curinga em todas as
interfaces, que recebeu os datagramas; isso confirma que o bind curinga, por si
só, funciona neste host. Os registros brutos ficam fora do repositório e não
foi possível estabelecer uma proveniência contemporânea completa entre aquela
captura, o endpoint local e a configuração efetiva do BIN. Ela não substitui a
prova da #13, que ainda precisa ser reproduzível e correlacionada.

## Restauração

Ao terminar, o app original de `ota_0` foi regravado e verificado. A NVS foi
restaurada a partir da leitura feita antes do ensaio, lida de volta e comparada
byte a byte. A placa ficou no bootloader ROM, sem alteração da NVS original.

## Próxima etapa

O firmware em desenvolvimento para a PR #31 acrescenta uma linha cumulativa
`WASMHOME_MODULE_IO` a cada cinco segundos. Ela registra os estágios de
telemetria e controle, profundidade das filas e o resultado local de cada tipo
de envio UDP. A instrumentação ainda não foi compilada nem executada na placa;
ela não altera o protocolo UDP. `accepted` significa que `sendto` aceitou o
datagrama localmente, não que o observador o recebeu.

Os vetores JSON da linha usam estas posições, começando em zero:

- `tel`: ticks, execuções do guest bem-sucedidas, chamadas a `publish`,
  publicações aceitas, itens enfileirados, falhas de enqueue, falhas do lock,
  falhas ao montar o corpo, descartes por fila cheia, descartes por contenção
  da reserva, descartes por execução ocupada e profundidade da fila.
- `ctl`: pedidos inválidos, pedidos que chegaram ao manager, pedidos recebidos
  enquanto inativo, admitidos, replays, recusas de admissão, resultados
  produzidos, resultados enfileirados, aceitações de update enfileiradas,
  descartes da fila de saída, falhas de execução, falhas de GPIO, profundidade
  da fila de pedidos e profundidade da fila de saída. `requests_received` é
  contado no manager; não inclui datagramas descartados antes dele pela camada
  UDP. `requests_invalid` agrega envelope inválido e campos semânticos inválidos.
- `tx`: itens retirados das filas de telemetria, resultado de controle,
  aceitação de update e outros; falhas retornadas pela API de envio; falhas
  agregadas da fila de saída de controle e da API de envio (sem os descartes da
  fila de telemetria, que aparecem em `tel`); profundidades das filas de
  telemetria e de saída de controle.
- `udp.tel`, `udp.ctl` e `udp.upd`: chamadas, chamadas a `sendto`, datagramas
  aceitos localmente, falhas de `sendto`, chamadas sem transporte pronto,
  socket indisponível, rede indisponível, sequência esgotada, falhas de
  codificação, resultado bruto da última falha de `sendto`, `errno` dessa falha
  e sua sequência. Os três últimos valores permanecem zero se não houve falha.

Antes de repetir o ensaio é necessário observar passivamente o caminho UDP
entre a estação Wi-Fi da placa e o host, por captura no AP/roteador ou por uma
captura local autorizada. A próxima execução deve correlacionar a linha
diagnóstica, o boot UART e os datagramas observados na mesma janela. A PR #30,
dependência da #12, foi integrada à `main` no commit
`298bfaa33584e64763ca97b35407f662cca7938b`; a PR #31 continua baseada em `main`
e em rascunho até a prova física de ponta a ponta.

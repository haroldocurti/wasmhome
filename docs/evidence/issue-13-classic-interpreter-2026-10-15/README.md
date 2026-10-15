# Issue #13 — telemetria e controle no interpretador clássico

**Estado: prova física de ponta a ponta pendente.** O ESP32-S3 iniciou o
firmware de interpretador clássico e os dois módulos ficaram prontos, mas o
observador não recebeu datagramas durante a execução sincronizada. Este registro
não marca os critérios físicos da #13 como concluídos.

## Perfil gravado

- ESP32-S3, interpretador clássico WAMR habilitado e AOT desabilitado.
- Serviços modulares ativos: módulo de telemetria e módulo de controle v1.
- BIN: 843.424 bytes, SHA-256
  `1682f908277cef22778a74acfd43b0d1d60191e764406cd6445df401a5bd5c64`.
- ELF SHA-256: `f20ee30c878e0b2e56499055b7a864083d42be7b951121b12bdb1610b0eef111`.
- `sdkconfig` efetiva SHA-256:
  `b55fd716d61b04bb03eb02786aa8aafd0a2859555c6de753adcb366d43c9e40e`.
- O receipt do esptool confirma escrita e verificação do BIN em `ota_0`.

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
Os registros brutos ficam fora do repositório e não foi possível estabelecer
uma proveniência contemporânea completa entre a captura, o endpoint local e a
configuração efetiva do BIN. A #13 precisa de uma execução correlacionada e
reproduzível.

## Restauração

Ao terminar, o app original de `ota_0` foi regravado e verificado. A NVS foi
restaurada a partir da leitura feita antes do ensaio, lida de volta e comparada
byte a byte. A placa ficou no bootloader ROM, sem alteração da NVS original.

## Próxima etapa

Confirmar e observar passivamente o caminho UDP entre a estação Wi-Fi da placa
e o host antes de repetir o ensaio. A prova de aceite deve combinar, na mesma
execução, o boot UART e a captura de telemetria e respostas de controle. A PR
#30, dependência da #12, foi integrada à `main` no commit
`298bfaa33584e64763ca97b35407f662cca7938b`. A PR #31 agora usa `main` como base;
seu diff permanece restrito a este relatório.

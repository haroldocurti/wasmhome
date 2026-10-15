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

A captura UART cobre o reset e registra `boot_observed`,
`platform_initialized`, `module_ready` para os dois módulos,
`services_active`, `first_telemetry_operation`, `workers_ready`,
`module_manager_ready`, `wifi_ip_acquired` e `udp_bound`, todos sem erro. Não
aparecem falhas de abertura ou envio UDP, desconexão Wi-Fi, nem reinício durante
a janela observada.

O observador foi iniciado antes do reset, usou o endereço e a porta compilados
no firmware, e executou por 30 segundos com período de 100 ms. O lease DHCP
registrado na UART era o mesmo da execução imediatamente anterior. Ainda assim,
o resultado contém somente `session_start`, 300 ciclos `awaiting_boot` e
`session_end`: zero datagramas recebidos, zero telemetrias decodificadas e zero
controles enviados. Portanto, o boot e a prontidão dos módulos estão provados;
a entrega de telemetria ao host e o retorno de controle continuam sem prova.

Uma captura passiva na interface Ethernet não foi possível: o host negou a
abertura do socket de captura (`Operation not permitted`). Sem essa observação,
os registros não distinguem entre ausência de envio pelo dispositivo e perda ou
isolamento no caminho Wi-Fi–Ethernet.

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

# Issue #13 — telemetria e controle no interpretador clássico

**Estado: critérios de aceite físicos atendidos em 15/10/2026.** Uma captura
passiva no host confirmou que a placa enviava telemetria; o firewall local
descartava os datagramas antes de chegarem ao socket do observador. Com uma
exceção temporária e restrita para UDP/3333, o observador validou telemetria,
respostas de controle v1 e o loopback da GPIO durante 30 segundos. A aplicação
e a NVS anteriores foram restauradas e verificadas, e a placa ficou no
bootloader ROM.

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

A UART da inicialização registrou `boot_observed`, `platform_initialized`,
`module_ready` para telemetria e controle, `workers_ready`,
`module_manager_ready`, `wifi_ip_acquired` e `udp_bound`, sem erro. O cenário
foi `module-interp`, usando o BIN e os hashes listados acima. O boot ID da UART
`03320a62c09de399` corresponde ao boot ID decimal
`230257950203962265` observado nos datagramas.

As duas janelas anteriores de 30 segundos tiveram zero pacotes no socket do
observador. A captura passiva seguinte na Ethernet do host (`enp2s0`,
`192.168.0.13`) encontrou 522 datagramas UDP/3333 de `192.168.0.145` para o
host; os pacotes tinham 64 bytes, começavam com o cabeçalho `WHOB` v1 de
telemetria, e o tcpdump não perdeu pacotes. O UFW estava ativo com entrada
negada por padrão e sem regra para essa porta. Isso explica o resultado: os
datagramas chegavam à interface, mas o firewall os bloqueava antes do socket.

Foi adicionada uma regra temporária que permitia somente UDP/3333 de
`192.168.0.145` para `192.168.0.13`. Uma escuta curta recebeu 31 datagramas em
três segundos. Em seguida, a captura passiva e o observador foram executados
juntos por 30 segundos, com bind em `192.168.0.13:3333`:

- O PCAP registrou 967 pacotes, sem descartes pelo kernel.
- O observador recebeu e decodificou 304 telemetrias válidas e 299 resultados
  de controle; enviou 299 controles, todos válidos, sem perdas.
- As 299 respostas vieram do módulo v1 com status `APPLIED`. A leitura da GPIO
  de loopback coincidiu com o valor pedido: baixo em 100 respostas e alto em
  199. O caminho usa `gpio_get_level(CONFIG_WASMHOME_LOOPBACK_GPIO)` depois de
  aplicar a saída.
- A maior lacuna entre telemetrias válidas foi 111,67 ms; entre resultados de
  controle válidos, 153,43 ms. Não houve lacunas de sequência não resolvidas.

Assim, a mesma execução provou telemetria e controle simultâneos nos módulos
interpretados, as cadências observadas, a versão v1 e o loopback físico. A regra
temporária do UFW foi removida depois da captura; a política de entrada voltou
ao estado anterior.

Os artefatos brutos permanecem fora do Git, no diretório local privado
`~/.local/share/wasmhome-private/issue13-classic-interpreter-20261015/host-capture-01/`.
SHA-256 do PCAP principal: `14ec59f3a47da007cdd7329cde099108c5a5be731454a323b6788bdf275fcae8`;
do `observations.jsonl`: `fddd83dcc72f1bc02665e7b38f054d03aff269087aca32389c67454ea49168f5`;
do `summary.json`: `f3151b07a3eb67d3777caa69e20391a9f59da0ac07ea91c33969c3bcb367f83e`;
e do `uart.log`: `c38c3df9d6cd4d5d949e5e2ca33721d0fc3d88e3566cd0dbc32f16dc8fd5969b`.

## Evidência anterior da #12

A captura estável documentada em
[`issue-12-allocator-alignment-2026-10-15`](../issue-12-allocator-alignment-2026-10-15/README.md)
é apoio, não aceite independente para a #13. Ela registrou 84 telemetrias
válidas, 78 resultados em 79 controles e um lote separado com 10/10 respostas.
O JSONL privado dessa captura também registra bind curinga em todas as
interfaces, que recebeu os datagramas; isso confirma que o bind curinga, por si
só, funciona neste host. Os registros brutos ficam fora do repositório e não
foi possível estabelecer uma proveniência contemporânea completa entre aquela
captura, o endpoint local e a configuração efetiva do BIN. Ela segue como
contexto separado e não substitui a captura física própria da #13 documentada
acima.

## Restauração

Antes de gravar o BIN de ensaio, `ota_0` e NVS foram lidas da placa e salvas
como cópias de restauração. O `esptool` regravou ambas no mesmo endereço e
informou `Hash of data verified` para cada região. A placa permaneceu no
bootloader ROM. A regra temporária do UFW também foi removida e o status final
voltou à lista de regras anterior ao ensaio.

## Instrumentação da PR #31

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

Os critérios físicos da #13 foram comprovados com o BIN já identificado neste
relatório; a linha nova `WASMHOME_MODULE_IO` não foi necessária para localizar a
causa, nem foi compilada ou executada nesta campanha. A PR #31 permanece em
rascunho até que essa alteração de instrumentação seja compilada e revisada. A
PR #30, dependência da #12, foi integrada à `main` no commit
`298bfaa33584e64763ca97b35407f662cca7938b`.

# Adendo de baseline aprovada — issue #20

**Data:** 17/10/2026 (America/Sao_Paulo)  
**Protocolo:** `issue20-v1.0`  
**Escopo:** substituir somente a imagem-base; manter todos os outros parâmetros
pré-registrados sem alteração.

## Aprovação

Em 17/10/2026, o usuário aceitou explicitamente o estado atual da ESP32-S3 como
baseline aprovada e autorizou os ensaios físicos. Essa aprovação substitui, para
esta execução, o hash de imagem-base `b66c24a3…` registrado em `issue20-v1.0`.
Nenhuma regra temporal, critério de aceite, agenda, classificação de resultado,
regra de invalidação ou requisito de evidência foi alterado.

## Recibo sanitizado da captura integral

- Captura somente de leitura em `/dev/ttyACM0`, com esptool 4.12.0, concluída
  em 17/10/2026; a ferramenta reiniciou a placa via RTS ao terminar.
- Identidade observada: ESP32-S3 QFN56, revisão 0.2, mesma identidade física
  confirmada no preflight anterior. O identificador completo e o endereço de
  rede permanecem no recibo privado.
- Tamanho: 16.777.216 bytes (16 MiB).
- SHA-256 da baseline aceita:
  `00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`.
- O dump integral e o log serial permanecem em armazenamento local ignorado
  pelo Git. Nenhum conteúdo de flash, credencial ou endereço privado foi
  publicado.
- A comparação local com a captura de 16/10 encontrou diferenças somente na
  partição NVS. Não houve diferença em bootloader, tabela de partições,
  `otadata`, partições de aplicação ou slots de módulo. A captura nova, aceita
  pelo usuário, é a fonte integral a restaurar e verificar antes de cada slot.

## Parâmetros congelados preservados

Permanecem em vigor, sem ajuste após observar o piloto: aquisição DS18B20 a
500 ms; observação e controle a 100 ms; amostra válida com idade máxima de
1.000 ms; timeouts funcionais e de aceite de 300 ms; janela anterior de 60 s
com pelo menos 30 controles corretos e 30 telemetrias válidas sob um único
boot; prazo de atualização de 60 s; janela posterior de 30 s (e drenagem de
até 301 ms); e no mínimo cinco atualizações aceitas, completas e terminais por
cenário A/B/C. A agenda balanceada inicial continua com seis blocos e 18 slots;
tentativas, recusas, timeouts, respostas tardias e falhas permanecem nos
registros e denominadores definidos pelo protocolo.

O piloto só começa após a confirmação de firmware/boot, piso antirretorno,
sensor, GPIO5→GPIO6, endpoint isolado e espaço dos logs. Se qualquer pré-condição
falhar, nenhum disparo ou gravação é iniciado.

## Estado de leitura antes do piloto

Depois da captura aceita, a identificação somente de leitura reconfirmou a
mesma ESP32-S3, revisão 0.2, e a flash de 16 MiB. A leitura eFuse indicou
`SECURE_BOOT_EN=False`, `SPI_BOOT_CRYPT_CNT=Disable (0b000)`,
`DIS_DOWNLOAD_MANUAL_ENCRYPT=False` e `SECURE_VERSION=0`; portanto, os bits de
secure boot e flash encryption não impedem a restauração bruta previamente
autorizada. O recibo completo continua privado.

O firmware que já estava no snapshot é um app de diagnóstico de plataforma, não
um build de serviço do piloto. Em duas observações UDP somente de leitura de
60 s, não houve pacote recebido pelo observador e nenhum controle ou
`UPDATE_TRIGGER` foi enviado. O console serial confirmou boot único, associação
Wi-Fi, socket UDP aberto, DS18B20 funcional após a primeira amostra de partida e
loopback GPIO5→GPIO6 correto; também registrou falhas repetidas de `sendto` e o
evento `platform_selftest_diagnostic_only`. Essa falha do app atual não foi
classificada como falha instrumental nem como resultado de tentativa: não houve
tentativa. O preflight será refeito com o firmware temporário de serviço A/B/C,
usando observador e endpoint de artefatos isolados. Nenhuma gravação nem
disparo de atualização ocorreu até este ponto.

## Resultado posterior da tentativa de 17/10

Depois deste preflight somente de leitura, foi executada a tentativa
`B0001-native` da campanha `164c919d-d1d9-401b-9451-84c39ed930b9`. A janela
pré-disparo não recebeu UDP válido suficiente e o runner terminou com
`dispatch_status=not_sent`; nenhum `UPDATE_TRIGGER` ou pedido HTTPS ocorreu.
Foram gravados apenas o firmware de aplicação e a tabela de partições para
essa preparação. A imagem integral aceita foi restaurada e o esptool concluiu
`Hash of data verified.` A ausência de telemetria/controle observados permanece
sem causa determinada. Detalhes sanitizados: [tentativa HIL](hil-attempt-2026-10-17.md).

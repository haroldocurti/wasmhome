# Adendo de baseline aprovada — issue #20

**Data:** 17/10/2026 (America/Sao_Paulo)

**Protocolo:** `issue20-v1.0`
**Escopo:** substituir somente a imagem-base; manter todos os outros parâmetros
pré-registrados sem alteração.

## Aprovação

Em 17/10/2026, o usuário aceitou explicitamente o estado atual da ESP32-S3 como
baseline aprovada e autorizou os ensaios físicos. Essa aprovação substitui, para
esta execução, o identificador da imagem-base registrado em `issue20-v1.0`.
Nenhuma regra temporal, critério de aceite, agenda, classificação de resultado,
regra de invalidação ou requisito de evidência foi alterado.

## Recibo sanitizado da captura integral

- Captura somente de leitura em `/dev/ttyACM0`, com esptool 4.12.0, concluída
  em 17/10/2026; a ferramenta reiniciou a placa via RTS ao terminar.
- Identidade observada: ESP32-S3 QFN56, revisão 0.2, mesma identidade física
  confirmada no preflight anterior. O identificador completo e o endereço de
  rede permanecem no recibo privado.
- Tamanho: 16.777.216 bytes (16 MiB).
- O fingerprint da baseline aceita consta somente no recibo privado.
- O dump integral e o log serial permanecem em armazenamento local ignorado
  pelo Git. Nenhum conteúdo de flash, credencial ou endereço privado foi
  publicado.
- A comparação encontrou uma diferença em armazenamento NVS, sem causa
  determinada; conteúdo e detalhes permanecem privados. A captura nova, aceita
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

O despacho de uma atualização medida só começa após a confirmação de
firmware/boot, piso antirretorno, sensor, GPIO5→GPIO6, endpoint isolado, espaço
dos logs e janela válida. A gravação de firmware/tabela de partições usada para
preparar o perfil de ensaio é uma etapa de preparação separada: se a
qualificação falhar, não há `UPDATE_TRIGGER` e a baseline integral aceita é
restaurada e verificada. Foi o que ocorreu no slot de 17/10 descrito abaixo.

## Estado de leitura antes do piloto

Depois da captura aceita, a identificação somente de leitura reconfirmou a
mesma ESP32-S3, revisão 0.2, e a flash de 16 MiB. Metadados de eFuse foram
consultados localmente para planejar a restauração bruta autorizada; os valores
de segurança permanecem no recibo privado e não são publicados aqui.

O firmware que já estava no snapshot é um app de diagnóstico de plataforma, não
um build de serviço do piloto. Em duas observações UDP somente de leitura de
60 s, não houve pacote válido ou `boot_id` recebido pelo observador. Sem boot,
ele permaneceu em `awaiting_boot` e enviou zero pedidos `CONTROL_REQUEST`; nenhum
`UPDATE_TRIGGER` foi enviado. O console serial confirmou boot único, associação
Wi-Fi, socket UDP aberto, DS18B20 funcional após a primeira amostra de partida e
loopback GPIO5→GPIO6 correto; também registrou falhas repetidas de `sendto` da
aplicação e o evento `platform_selftest_diagnostic_only`. Essa falha do app
atual não foi classificada como falha instrumental nem como resultado de
tentativa: não houve tentativa. O preflight será refeito com o firmware
temporário de serviço A/B/C, usando observador e endpoint de artefatos isolados.
Até este ponto não houve gravação nem disparo de atualização.

## Resultado posterior da tentativa de 17/10

Depois deste preflight somente de leitura, um dos 18 slots planejados iniciou a
qualificação de `B0001-native` na campanha piloto. O observador registrou 601
eventos `awaiting_boot`, enviou zero pedidos de controle e não recebeu `boot_id`. A
entrada de despacho registrou `dispatch_status=not_sent` e `attempt_id=null`;
isso não remove o ID de execução próprio do observador. Nenhum
`UPDATE_TRIGGER` ou pedido HTTPS ocorreu.
Foram gravados apenas o firmware de aplicação e a tabela de partições para
essa preparação. A imagem integral aceita foi restaurada e o esptool concluiu
`Hash of data verified.` A ausência de telemetria/controle observados permanece
sem causa determinada. Detalhes sanitizados: [tentativa HIL](hil-attempt-2026-10-17.md).

# Tentativa HIL #20 — 17/10/2026

## Baseline e isolamento

- Baseline explicitamente aprovada pelo usuário antes dos ensaios.
- Leitura integral de 16 MiB antes da primeira escrita: SHA-256
  `00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`.
- A comparação com o arquivo da baseline aceita retornou igualdade byte a
  byte (`byte_for_byte_match=true`). O dump e o recibo completo permanecem
  privados.
- UUID da campanha: `164c919d-d1d9-401b-9451-84c39ed930b9`. A agenda e os
  limites congelados não foram alterados.

## Slot planejado B0001-native

- O ESP iniciou o perfil nativo, associou-se à rede e respondeu a ping; esses
  fatos não substituem telemetria ou respostas de controle da aplicação.
- Um dos 18 slots planejados iniciou a qualificação pré-disparo. O observador
  não identificou `boot_id`, registrou 601 eventos `awaiting_boot` e enviou
  zero pedidos de controle. A entrada de despacho ficou com
  `dispatch_status=not_sent` e `attempt_id=null`; o observador mantém um ID de
  execução separado. Não houve `UPDATE_TRIGGER`, transferência HTTPS,
  `update_accepted` ou atualização completa. O log privado do servidor não
  registrou requisições.
- Classificação: `pre_window_failed`, com razões registradas
  `boot_not_stable_for_full_window`, `boot_changed_during_window`,
  `too_few_correct_controls` e `too_few_valid_telemetry_messages`. Nenhuma
  causa de rede ou firmware foi inferida a partir dessas razões. Como zero
  boots foram observados, `boot_changed_during_window` significa apenas que
  não houve exatamente um boot na janela; não indica que uma transição de boot
  tenha sido vista.
- Foram escritos somente a tabela de partições e o firmware de aplicação do
  slot. Esptool verificou os hashes desses dois intervalos.
- Após a tentativa, a baseline integral aceita foi restaurada a partir da
  leitura pré-escrita. O esptool concluiu a verificação automática com
  `Hash of data verified.`

## Resultado e próximo passo

Nenhum aceite físico foi obtido: zero triggers e zero aceites nos 18 slots do
plano. Um slot (1/18) chegou à fase de qualificação pré-disparo; os outros 17
não foram iniciados. A meta mínima continua em 0 de 5 aceites completos por
cenário (A/B/C). Dumps integrais, UART bruta, logs de transporte e arquivos de
campanha permanecem privados e não fazem parte do repositório.

O bloqueio é confirmar e corrigir a recepção UDP pré-disparo. Depois de
demonstrar uma janela qualificada de controles e telemetria, criar outra
campanha com novo UUID; não reutilizar este slot como ensaio de atualização.

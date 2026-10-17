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

## Slot B0001-native

- O preflight serial passou com DTR/RTS inativos. O ESP iniciou o perfil nativo,
  associou-se à rede e respondeu a ping; esses fatos não substituem telemetria
  ou controle da aplicação.
- A qualificação pré-disparo não recebeu UDP válido suficiente. O resultado do
  runner foi `dispatch_status=not_sent`, sem `attempt_id`; não houve
  `UPDATE_TRIGGER`, transferência HTTPS, `update_accepted` ou atualização
  completa. O log privado do servidor não registrou requisições.
- Classificação: `pre_window_failed`, com razões registradas
  `boot_not_stable_for_full_window`, `boot_changed_during_window`,
  `too_few_correct_controls` e `too_few_valid_telemetry_messages`. Nenhuma
  causa de rede ou firmware foi inferida a partir dessas razões.
- Foram escritos somente a tabela de partições e o firmware de aplicação do
  slot. Esptool verificou os hashes desses dois intervalos.
- Após a tentativa, a baseline integral aceita foi restaurada a partir da
  leitura pré-escrita. O esptool concluiu a verificação automática com
  `Hash of data verified.`

## Resultado e próximo passo

Nenhum aceite físico foi obtido. O piloto permanece A `0/5`, B `0/5`, C `0/5`.
O resultado pertence ao slot sem disparo da campanha acima; os demais slots não
foram executados. Dumps integrais, UART bruta, logs de transporte e arquivos de
campanha permanecem privados e não fazem parte do repositório.

O bloqueio é confirmar e corrigir a recepção UDP pré-disparo. Depois de
demonstrar uma janela qualificada de controles e telemetria, criar outra
campanha com novo UUID; não reutilizar este slot como ensaio de atualização.

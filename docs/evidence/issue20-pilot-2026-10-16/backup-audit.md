# Auditoria offline da imagem-base da #20

**Data:** 16/10/2026. **Escopo:** auditoria somente de leitura da proveniência
de capturas integrais. Os dumps e recibos completos permanecem em armazenamento
privado; não houve gravação durante esta auditoria.

## Atualização da decisão

Este documento registra o preflight de 16/10, anterior à aprovação de uma nova
baseline pelo usuário em 17/10. A conclusão de não iniciar gravação foi
substituída pela aprovação da captura atual e pela tentativa posterior descrita
no [registro HIL de 17/10](hil-attempt-2026-10-17.md). Fingerprints e recibos
completos não são publicados.

## Resultado

Os snapshots integrais examinados têm 16 MiB e proveniência conferida contra
recibos privados. Artefatos de sessões distintas, incluindo backups parciais de
aplicação, foram reconciliados separadamente e não tratados como equivalentes;
os fingerprints permanecem nos registros privados. A leitura pré-escrita de
16/10 não coincidiu com a baseline então selecionada, portanto a gravação
permaneceu bloqueada até que o usuário aprovasse a baseline aplicável em 17/10.

## Limites da comparação

O preflight identificou uma ESP32-S3 QFN56 revisão 0.2 com flash de 16 MiB. A
comparação somente de leitura revelou uma diferença em armazenamento NVS; o
conteúdo, os detalhes e os fingerprints permanecem privados, e a causa não foi
determinada. O MAC e os dados completos de captura permanecem no recibo privado.
Os comandos `chip_id` e `flash_id` do esptool 4.12.0 acionaram hard reset, sem
gravação.

Na tentativa posterior, a captura aprovada foi restaurada e verificada após a
preparação do slot, conforme o [registro HIL](hil-attempt-2026-10-17.md). Essa
verificação confirma a restauração da baseline aprovada; ela não determina a
causa da diferença observada no preflight anterior.

## Interpretação de conectividade

A falta de respostas de rede observadas não identifica, por si só, se a causa
era conectividade ou firmware. A análise histórica não determinou essa causa e
não fornece base para atribuir a divergência de armazenamento a uma condição
específica. A documentação do ESP-IDF descreve mecanismos gerais de calibração
RF, mas eles não determinam o que ocorreu nesta captura ([guia RF Calibration do
ESP-IDF 5.2](https://docs.espressif.com/projects/esp-idf/en/v5.2/esp32s3/api-guides/RF_calibration.html)).

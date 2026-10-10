# Verificação local do observador externo

Data: 02/10/2026. Trabalho de [Construir e calibrar o observador externo](https://github.com/haroldocurti/wasmhome/issues/10)
na branch `codex/issue-10-external-observer`, a partir da main `9d332d6`.
Este registro descreve a validação local. Merge e fechamento da issue são
acompanhados no GitHub.

## Resultado

`tools/test-platform.ps1` compilou o codec C com MSVC C11 `/W4 /WX` e executou
26 testes, sem falhas ou skips. Nove testes do observador verificam correlação,
perdas, atrasos, duplicatas, respostas incorretas, boots, regressão de versão,
reordenação, telemetria e configurações inválidas. Os 17 testes anteriores
também passaram, incluindo mutações e comparação com o codec C.

As duas capturas finais usaram 100 ms de cadência, timeout de 300 ms e
interrupção configurada de 1000 ms. A duração conhecida, os datagramas da fixture,
os enviados/recebidos pelo observador e os resumos estão preservados:

| Ensaio | Estimativa | Erro absoluto | Resolução prevista | Resultado |
|---|---:|---:|---:|---|
| Mesmo boot | 999,2943 ms | 0,7057 ms | 100 ms | Passou |
| Reboot simulado | 1099,5196 ms | 99,5196 ms | 200 ms | Passou |

- [Calibração no mesmo boot](observer-2026-10-02/final-same-boot/calibration.json),
  [resumo](observer-2026-10-02/final-same-boot/summary.json),
  [JSONL do observador](observer-2026-10-02/final-same-boot/observations.jsonl),
  [JSONL da fixture](observer-2026-10-02/final-same-boot/fixture.jsonl).
- [Calibração com reboot simulado](observer-2026-10-02/final-reboot/calibration.json),
  [resumo](observer-2026-10-02/final-reboot/summary.json),
  [JSONL do observador](observer-2026-10-02/final-reboot/observations.jsonl),
  [JSONL da fixture](observer-2026-10-02/final-reboot/fixture.jsonl).

Uma captura preliminar com reboot estimou 1100,8532 ms, excedendo a tolerância
inicial de 100 ms por 0,8532 ms. A análise mostrou que reaprender o boot por
telemetria acrescenta até uma cadência antes do controle. O calibrador passou
a declarar 200 ms para o ensaio com reboot antes da captura; manteve 100 ms
no mesmo boot. Os ensaios finais acima foram executados com esse critério.
Tentativas preliminares ficaram locais sob `results/`, fora da evidência final.

## Limites

As capturas são entre dois processos lógicos no PC Windows usando sockets
UDP em loopback. Elas comprovam envio de estímulos/disparo, correlação de aceite
e v2, detecção de perdas e medição da interrupção conhecida inclusive com novo
boot simulado. A fixture aplica um oráculo simples e não executa regras de
módulos reais, HTTPS, assinatura, antirretorno ou OTA.

As capturas descritas acima não fizeram flash, reset ou ensaio físico
no ESP32-S3. A [verificação posterior na placa](observador-hardware-2026-10-02.md)
acrescentou essa integração usando uma fixture separada. A integração com os
módulos definitivos continua nas próximas etapas. Não há
resultados da campanha nem validação de ruído/atraso da rede física. O limite
de idade de telemetria é provisório e deve ser confirmado no piloto.

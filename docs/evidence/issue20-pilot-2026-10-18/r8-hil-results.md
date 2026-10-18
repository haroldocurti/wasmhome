# Issue #20 — resultados finais do piloto r8 (18/10/2026)

## Escopo e decisão

- Protocolo `issue20-v1.0`, congelado antes da coleta. A campanha piloto `5bb0678a-b17b-4f59-9155-c0885eaef22e` executou seis blocos balanceados, 18 slots no total e seis atualizações aceitas por cenário.
- Os 18 slots foram concluídos com instrumentação válida. `native`, `interp` e `aot` tiveram 6/6 atualizações completas e bem-sucedidas; nenhum cenário teve recusa, timeout de aceite, timeout de atualização ou resultado incorreto. Todas as janelas pós-atualização foram estáveis.
- A baseline integral aprovada foi restaurada e lida de volta após cada slot: 16 MiB, SHA-256 `00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`.
- O piloto atende ao mínimo de cinco aceitações completas por cenário. Os parâmetros de `issue20-v1.0` permanecem congelados para a campanha definitiva. A campanha normal segue em N=30 blocos/90 slots, por viabilidade descritiva; este piloto não é um estudo confirmatório de equivalência ou poder estatístico.

## Tempos observados

Medidas em segundos, exceto o RTT de aceite. `Disparo → v2` termina na primeira resposta v2 correta; o prazo protocolar é 60 s. O RTT de aceite é comparado ao limite congelado de 300 ms.

| Cenário | Atualizações completas | Disparo → v2 (mín.; mediana; máx.) | IQR de disparo → v2 | RTT de aceite (mediana; faixa) |
| --- | ---: | ---: | ---: | ---: |
| Native | 6/6 | 12,975; 14,658; 16,870 | 14,324–15,456 | 5,789 ms; 4,620–6,951 ms |
| Interp | 6/6 | 4,660; 6,782; 8,922 | 5,168–7,086 | 7,241 ms; 4,819–10,875 ms |
| AOT | 6/6 | 4,676; 6,146; 7,391 | 5,589–6,736 | 10,849 ms; 4,633–12,692 ms |

Todos os tempos de disparo até v2 ficaram abaixo de 60 s e todos os aceites chegaram abaixo de 300 ms.

## Continuidade e instrumentação

| Cenário | Timeouts de controle | Respostas tardias | Maior lacuna de controle | Maior lacuna de telemetria |
| --- | ---: | ---: | ---: | ---: |
| Native | 170 | 0 | 2,911 s | 2,873 s |
| Interp | 94 | 48 | 1,535 s | 0,639 s |
| AOT | 84 | 48 | 1,525 s | 0,302 s |

Os timeouts e respostas tardias foram mantidos nos denominadores e relatados como efeitos observados durante os slots; não foram usados para invalidar medições. As 18 capturas foram classificadas como válidas, e as janelas posteriores cumpriram o critério de estabilidade.

## Agendamento e integridade

A agenda final foi gerada antes da coleta com uma ocorrência de cada permutação A/B/C por bloco; a cópia pública sanitizada está em [`r8-pilot-schedule.json`](r8-pilot-schedule.json). A análise é determinística e não modificou os dados brutos.

- SHA-256 da agenda pública sanitizada: `52ccaddd2e801f30fa3ffbf5aa527b382bf84f14ce5dd0b406f6ef7e3a7a0d93`.
- SHA-256 da análise sanitizada: `00d84beed4476c6bccb9c9b365d9af43e90d3bc719c1f22d857cf7911c8b615b`.
- SHA-256 do relatório de adjudicação: `571c9cb08ab4eb3ad5bb481e81baf06633f2994c5ac23bb22634bfe793a430a8`.
- Os logs UART, observações UDP/HTTPS e imagens integrais de flash permanecem no armazenamento privado da bancada. Este resumo não contém credenciais, endereços privados, dados seriais ou dumps de flash.

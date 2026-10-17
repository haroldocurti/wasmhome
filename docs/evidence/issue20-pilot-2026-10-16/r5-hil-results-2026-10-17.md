# Issue #20 — resultados HIL r5 (17/10/2026)

## Escopo

- 18/18 slots planejados e concluídos, em seis blocos balanceados e seis
  tentativas por cenário (`native`, `interp` e `aot`).
- A campanha r5 usou os perfis legados, antes do gate schema 2 que congela e
  instala bootloader, tabela, app e headers gerados de configuração do mesmo
  build. Os resultados servem para diagnóstico; não qualificam o perfil nativo
  completo nem satisfazem o mínimo de aceites da issue.
- A baseline aceita foi lida antes das operações e restaurada/verificada após
  cada slot. O journal terminou `complete`, sem restauração pendente.

## Resultados

| Cenário | Ativações V2 | Mediana até ativação | Aborts instrumentais | Lacunas de sequência não resolvidas |
| --- | ---: | ---: | ---: | ---: |
| Native | 0/6 | Sem ativação dentro de 60 s | 0 | 3 no total |
| Interp | 6/6 | 6,533 s | 0 | 0 |
| AOT | 6/6 | 7,401 s | 0 | 0 |

Os 18 slots observaram pelo menos 30 controles estáveis e 30 mensagens de
telemetria estáveis, conforme o mínimo por tentativa. As seis tentativas
nativas foram classificadas como `accepted_without_v2`: a atualização foi
aceita, mas a imagem V2 não chegou a ativar dentro do prazo. As lacunas
registradas nesse cenário não foram classificadas como abort da instrumentação.

## Interpretação e próxima rodada

A rodada demonstra que os cenários `interp` e `aot` ativaram repetidamente e
que o caminho nativo precisa de nova qualificação. A inspeção offline dos
artefatos apontou que cabeçalhos de configuração não bastam para qualificar a
compatibilidade entre imagem e bootloader. A campanha schema 2 passa a exigir
bootloader e configuração gerados do mesmo build, rollback coerente entre
bootloader e app, imagem estruturalmente íntegra e restauração integral após
cada slot. Esses controles serão aplicados à nova rodada; os dados r5 não serão
reutilizados como aceites.

Os resumos privados contêm detalhes de cada slot, capturas e hashes de entrada.
Eles não são publicados neste repositório.

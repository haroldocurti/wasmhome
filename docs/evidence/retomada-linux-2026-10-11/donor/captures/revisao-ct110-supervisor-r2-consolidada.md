# Re-revisão consolidada R2 — supervisão CT110

Escopo focal: os dois achados P2 de R1, a resolução R2 e o cleanup entre receiver e builder. Isto não é uma revisão integral de diff Git; o pacote de preparação está fora do Git. O candidato mantém commit `187ff96279ed8e6d3da14b7663ce5b5e856dff49`, tree `70580c660a5642e9f7226c0d9813b805ee8b3c64`, índice R2 SHA-256 `f258587215f70326ac913d7ee086a76e8f3d9988446a2e8854b6d366645e9769` e índice R1 preservado SHA-256 `afb4a592322197b9562585d311b2debde514d7bef60d7512ccb1506eca2f7ebc`.

## Resultado por eixo

- **Standards:** a whitelist do guard fecha as classes de configuração que motivaram R1. O receiver R2 confirma UID/PGID/starttime antes de sinalizar o grupo; quando não pode confirmar a identidade, limita o sinal ao filho pinado e registra cleanup. Esses controles são apropriados, mas a garantia completa depende de o builder também limpar seu próprio grupo.
- **Spec:** há um P2 residual no builder fixado. Em `donor_wamrc.py:167-178`, `Runner.run` cria um subprocesso com `start_new_session=True` na linha 167, mas o `try/except BaseException` que chama `Runner.interrupt` começa somente na linha 168. Um SIGINT/KeyboardInterrupt ou o handler SIGTERM instalado em `:280-282`, processado após o `Popen` criar o subprocesso e antes da entrada no `try`, pode alcançar o catch superior em `:289-290` sem executar `Runner.interrupt`. O subprocesso de build pertence a uma sessão/grupo separado. No fallback do receiver em que a identidade de grupo não foi comprovada, `cleanup_spawn` sinaliza apenas o líder pinado (`ct110_supervisor_receiver.py:65-105`); se o líder sai pela exceção, o endpoint pode considerá-lo reaped sem confirmar que o processo/grupo descendente terminou. Isso contradiz a sequência de interrupção descrita na proposta, que depende de o Runner interromper seu próprio grupo.

## Achados R1 e testes

Os dois achados originais foram corrigidos na superfície revisada: cleanup do filho após falha de spawn/recibo no endpoint e whitelist de configuração CT110. Root relata 26 testes Linux R2 sem falhas ou skips; não executei a suíte. Os quatro novos testes do receiver cobrem falhas de recibo, identidade, pidfd e processo curto, sem testar uma interrupção no intervalo pós-`Popen` dentro do builder. Portanto, testes verdes não resolvem o P2 residual.

Correção recomendada: proteger a transição de spawn até a instalação do caminho de cleanup, ou adiar o tratamento de sinais nesse intervalo; acrescentar teste de falha/interrupção imediatamente após a criação real do processo e verificar que o filho/grupo descendente não permanece ativo. Esta é uma correção de cleanup sob o contrato existente, não um gate novo de assinatura ou de dupla compilação.

O pacote continua bloqueado para execução também por gates independentes: snapshot observado com 2,15 GiB disponíveis contra gate de 10 GiB, e autorização humana para criação/start do CT110 ainda pendente. Não fiz SSH, instalação, build, chamada de infraestrutura ou alteração de hardware nesta re-revisão.

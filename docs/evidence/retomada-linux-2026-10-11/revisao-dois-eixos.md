# Revisão adversarial da integração, dois eixos

Consolidada em 12/10/2026. Revisores Luna xhigh distintos, seguindo
`code-review/SKILL.md`. Base de código:
`8b372147e739e46c8952ab8c7907b6c76a76345b`; candidato:
`187ff96279ed8e6d3da14b7663ce5b5e856dff49`.
Range de código `git diff 8b372147...187ff962`; as alterações documentais
pendentes do worktree de planejamento foram examinadas separadamente e depois
reconferidas na composição da `main`. O recibo focal de preservação fica em
[revisao-publicacao.md](revisao-publicacao.md).

## Standards

Não foi encontrado defeito concreto de traversal, sobrescrita ou proveniência
no diff fixado. A documentação do port corresponde à transação implementada e
explica o limite do recibo sem assinatura. Não foi localizado documento próprio
de normas de código do repositório.

Possível **Duplicated Code**, heurística não bloqueante: `tools/runtime_package.py`
constrói o mesmo dicionário de identidade em `verify()` e `acquire()`. Um helper
comum reduziria a possibilidade de divergência futura. É sugestão de manutenção,
não violação documentada nem requisito para este checkpoint.

O revisor não executou testes nem alterou arquivos.

## Spec

A primeira revisão identificou dois defeitos documentais: snapshot/agenda que
ainda diziam #10 sem integração e #6 aberta; e ambiguidade entre a referência
de 90 tentativas e a proposta de agenda com 90 slots.

A re-revisão dos quatro documentos atuais confirmou ambos corrigidos para a
publicação do checkpoint. O estado agora registra #10 integrada pela PR28,
#6 encerrada pela PR29 e CT107 com build nativo, mantendo LLVM/wamrc e A/B/C
pendentes. Os snapshots anteriores estão identificados como históricos.

A amostra foi registrada como decisão pendente: 90 tentativas seguem como
referência; 90 slots são uma proposta que pode resultar em menos tentativas
aceitas. Checklist, plano detalhado, plano de execução e retomada exigem
conciliação antes de #20/#21. Não há contradição normativa residual para publicar
este checkpoint e não é necessário decidir a amostra durante seu arquivamento.
Esse gate continua aberto antes da campanha.

Não foi encontrada inconsistência material nos serviços, na histerese proposta
e explicitamente pendente de confirmação, no pré-registro ou na matriz de
resiliência. O revisor não executou testes nem alterou arquivos.

Resultado: Standards, 1 sugestão heurística não bloqueante; Spec, 0 achados
residuais após fechar 2 defeitos documentais. Nenhum bloqueador de publicação.

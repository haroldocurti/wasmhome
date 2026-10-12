# Cópia reconstruída, não original — revisão independente R2

**Aviso de proveniência:** este texto foi reconstruído após uma sobrescrita acidental e não é byte-idêntico ao recibo intermediário original. O digest antigo `1c8417cf60c3da79f554d83bb5e24bdf6a001479c0a5bdf4eeca929abdfa5aaa` não pode ser usado para validar este arquivo. A conclusão sem achados foi supersedida; use `revisao-ct110-supervisor-r2-consolidada.md` como parecer R2 canônico.

Pacote candidato: commit `187ff96279ed8e6d3da14b7663ce5b5e856dff49`, tree `70580c660a5642e9f7226c0d9813b805ee8b3c64`. Índice R2 congelado: `proposal-index.json` SHA-256 `f258587215f70326ac913d7ee086a76e8f3d9988446a2e8854b6d366645e9769`. O índice R1 foi preservado como `proposal-index-r1.json`, SHA-256 `afb4a592322197b9562585d311b2debde514d7bef60d7512ccb1506eca2f7ebc`.

## Achados R1 reavaliados

- Cleanup pós-Popen: **resolvido no código revisado**. O endpoint obtém pidfd, confirma UID/PGID/starttime antes de sinalizar o grupo, usa fallback restrito ao filho não reaped quando necessário e tenta reaper o filho antes de propagar a falha. Quatro testes injetam falha de identidade, falha de gravação do recibo, processo curto e falha de pidfd; processos descartáveis independentes permanecem vivos.
- Configuração CT110 aberta: **resolvida no código revisado**. Guard usa whitelist das dez chaves permitidas, exige arch amd64, volume de root exclusivo `vm-110-disk-N` em DevServer/32G e somente net0 com campos/defaults limitados. Os testes rejeitam mp0, lxc.mount.entry, net1, hookscript, lxc.cgroup2.devices.allow, dev0, features, chaves desconhecidas e opções adicionais de rootfs/net.

## Verificação desta rodada

Conferi SHA/bytes dos scripts e do documento contra o índice. Um parser que rejeita chaves JSON duplicadas verificou 33 payloads indexados, incluindo caminhos externos e o snapshot R1; todos os hashes/tamanhos conferiram. Li o recibo de testes R2 e sua saída: 26 testes Linux passaram, sem falhas ou skips, conforme root; não reexecutei testes nem fiz SSH/ações remotas. O `supervise_ct110.py` continua default sem chamadas remotas; execução requer flags explícitas, identidade/baseline e shutdown normal somente do CT110 novo.

## Resultado R2

Sem achados residuais no escopo desta re-revisão. Confirmei diretamente os bytes congelados do documento: `doador-proposta.md` SHA-256 `f3cecc7ca9091e905ddbcc2183d7821293bb327f6ed3aaa161d737009ae942a3` registra 26 testes em `:158` e suíte final 26/26 em `:173`, coerentes com `supervisor-guard-tests-r2-receipt.json` e a saída. As correções de cleanup/configuração acima correspondem aos dois achados P2 R1.

A proposta continua bloqueada para execução: snapshot de recursos observado tem 2,15 GiB disponíveis, abaixo do gate de 10 GiB; criação/start do CT110 aguardam autorização humana própria. A configuração deve ser confrontada com o CT real futuro; divergência não autoriza relaxar o guard. Nenhuma instalação, build LLVM, criação de guest, acesso SSH ou mudança de recursos ocorreu nesta revisão. Não há requisito de assinatura ou dupla compilação imposto por este parecer.

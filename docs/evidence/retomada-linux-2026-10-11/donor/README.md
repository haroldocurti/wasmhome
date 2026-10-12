# Preservação do doador e retomada Linux — 11/10/2026

Em 11/10, o usuário decidiu iniciar Linux no computador. A alternativa de doador
CT110/WSL foi substituída por essa rota. **CT110 não foi criado nem autorizado**;
nenhum guest foi criado, iniciado ou parado por estas receitas. Este diretório
preserva preparação, código e limites históricos; não autoriza executar
infraestrutura ou o compilador arquivado.

Foram copiadas byte a byte 48 capturas pequenas, totalizando 203919 bytes:
proposta e scripts finais R3, planos/approval pendente, testes, índices R1/R2/R3,
logs, pareceres do doador/runtime e três snapshots de recursos referenciados.
Não foram copiados chaves, credenciais, SDKs, executáveis, bibliotecas, fontes
LLVM materializadas ou caches. Arquivos `.bin` são somente stderr diagnóstico
bruto dos testes/preflight; não são binários de toolchain.

`path-map.json` associa cada caminho absoluto histórico à cópia relativa, com
tamanho e SHA256. `inventory.json` e `SHA256SUMS` cobrem os bytes publicados;
`.gitattributes` impede conversão de newline dessas capturas pelo Git. Os originais
do worktree não foram modificados. Caminhos Windows em recibos são proveniência
histórica, não diretórios necessários no novo Linux.

## O que foi comprovado

O candidato de runtime foi preparado em `40aca4c14c09a2fe5058eec9bfce9c7b08f5379c`
e corrigido em `187ff96279ed8e6d3da14b7663ce5b5e856dff49`, tree
`70580c660a5642e9f7226c0d9813b805ee8b3c64`. Os pareceres em `runtime-reviews/`
delimitam essa revisão; não equivalem a aceite das issues #7/#9.

O índice final R3 preservado em `captures/proposal-index.json` tem SHA256
`1696cd5604c1fc86f9a688acb01df004012d774d419dec78185e9bc06fe73b79`.
As rodadas locais do doador passaram 20 testes R1, 26 R2 e 28 R3. Houve testes
de SIGINT/SIGTERM com Python descartável real, grupo próprio, filho aninhado e
outro processo preservado; as chamadas SSH/CT/shutdown foram mocks. **Esses
testes não provam infraestrutura, compilação LLVM/wamrc, ABI Xtensa, AOT ou
execução física A/B/C.** Não existe recibo real de build wamrc nestas capturas.

A revisão `captures/revisao-ct110-supervisor-r3.md` foi escrita após congelar o
índice R3; portanto está fora daquele índice original. Foi preservada e tem hash
no inventário desta transferência. Ela aprova focalmente a correção da janela
de spawn do Runner, sem declarar readiness integral do doador.

O snapshot Proxmox tinha cerca2,15GiB disponíveis contra gate10GiB do CT110. O
snapshot WSL tinha cerca13,87GiB físicos livres em C:, abaixo do orçamento20GiB,
apesar de aproximadamente953GiB virtuais no VHDX; faltavam gcc/g++/cmake/ninja e
havia falha DNS/rede WSL. Esses dados são históricos, não capacidade atual do
Linux físico. RAMmax de uma VM não foi tratada como memória recuperável.

## Integridade e perdas conhecidas

Os scripts em `captures/` são os bytes finais R3. Índices R1/R2 preservam hashes
e recibos anteriores; arquivos sobrescritos entre rodadas não têm suas versões
antigas completas guardadas. Assim, não se pode reconstruir os bundles R1/R2
inteiros somente deste arquivo, nem esperar que todo hash antigo coincida com
o script R3. Isso é evolução registrada, não uma alegação de cópia antiga completa.

Um recibo intermediário R2 foi sobrescrito acidentalmente pelo processo de
revisão e seus bytes originais não foram recuperados. O digest histórico
`1c8417cf60c3da79f554d83bb5e24bdf6a001479c0a5bdf4eeca929abdfa5aaa`
não valida o texto reconstruído. O arquivo
`revisao-ct110-supervisor-r2-reconstructed-not-original.md` identifica essa perda;
o parecer canônico R2 é `revisao-ct110-supervisor-r2-consolidada.md`. Não foi
fabricado substituto com a identidade do original perdido.

Os hashes não são assinaturas. Eles detectam mudança de bytes e vinculam as
cópias aos materiais desta sessão, sem autenticação independente do doador.

## Reprodução local no Linux, sem infraestrutura

Na raiz de um clone que contenha este diretório, conferir as cópias e repetir
somente os testes locais:

```sh
python3 -B docs/evidence/retomada-linux-2026-10-11/donor/verify_preservation.py
python3 -B -m unittest discover -s docs/evidence/retomada-linux-2026-10-11/donor/captures -p 'test_*.py' -v
python3 -B docs/evidence/retomada-linux-2026-10-11/donor/captures/supervise_ct110.py
```

A suíte precisa Linux com Python e primitivas POSIX, executada como UID1000 para
os testes de identidade existentes. Cria apenas arquivos temporários e processos
Python descartáveis; não chama SSH, APT, Proxmox, compiladores ou hardware.
O default do supervisor imprime `proposal_only_no_remote_calls`.

Não executar `donor_wamrc.py --execute`, `--profile lxc110`, nem
`supervise_ct110.py --execute` desta captura. O builder congelado tem default
`--profile wsl` e exige kernel Microsoft/Ubuntu24.04; o perfil lxc110 exige o
hostname/control lease do container proposto. **Ambos são perfis legados, sem
autorização para execução no Linux físico.** `donor_wamrc.py` sem execute é só
preflight legado e pode rejeitar o novo sistema; isso não é prova de que o novo
Linux seja inviável.

## Próxima receita para Linux físico

O plano de retomada atual fica em `docs/retomada-linux.md`. Antes de compilar,
registrar distro/versão/arquitetura, UID, memória/swap disponíveis, filesystem
real/espaço livre, versões gcc/g++/cmake/ninja/Python/Git e acesso às origens
oficiais. Não transportar o gate de RAM Proxmox ou o tamanho virtual WSL para
o novo host; medir a capacidade física e revisar o orçamento Release do perfil.

O clone Linux terá uma revisão diferente de `187ff96` depois da integração e
publicação solicitadas. A nova receita precisa fixar **o commit completo e tree
realmente publicados**, revalidar limpeza (`status --porcelain --untracked-files=all`
e ausência de resíduos nos sources), e registrar novos hashes dos geradores.
Um bundle Git, se utilizado, deve conter essa nova revisão; o bundle histórico
187ff96 e o approval pendente desta captura não passam a aprovar a árvore nova.
Prepare um novo recibo de revisão/approval ligado aos novos pins e à receita
Linux revisada; não editar silenciosamente estas capturas congeladas.

Preservar, salvo decisão explícita revisada, os pins técnicos já selecionados:
WAMR/wamrc2.4.5 upstream25bd7eb63e828e4bd242cc9b38d260b4b31c6605, framework
e97860611e371d91d2ef25101b02b29719476a48, LLVM/Xtensa Espressif
344b928e67bfc1d07cb150609c55548e86a9bf19, IDF5.2.6 e WASI-SDK27. A receita
nova deve conferir lock/patch e fonte materializada, toolchain/SDK por origem e
hash, sources limpos, builds novos, Release/Ninja X86+Xtensa, sem projetos LLVM
extras, sem LTO, compile/link1 e compile_commands. Flags detalhados estão no
builder histórico; não representam um build já executado.

Só após preflight, dependências e revisão da receita Linux concreta, gerar
wamrc real e seu recibo com `tools/record_wamrc_build.py`, conferir ABI no destino
e então gerar B/C. Não declarar esses passos cumpridos pelos testes arquivados.

## Auditoria das pendências Git

`audit-git.json` registra leitura anterior à integração pelo root, com main
`8b372147e739e46c8952ab8c7907b6c76a76345b`. Os únicos commits de patches ainda
não integrados eram `40aca4c`/`187ff96`. Branches issue2/research tinham identidades
não ancestrais, mas patches equivalentes já integrados (`git cherry` marcou `-`);
issue3/issue8/plano-coleta eram ancestrais.

Além dos commits havia conteúdo não commitado no worktree plano-coleta-analise:
README.md, docs/plano-de-execucao.md, docs/research/ensaios-resiliencia.md e novo
docs/plano-detalhado-coleta-analise.md. O root está responsável por preservá-lo e
integrá-lo; estes arquivos não foram copiados por esta subentrega. Nenhuma branch,
worktree ou histórico foi removido nesta auditoria. O estado após integração deve
ser conferido no recibo final do root.

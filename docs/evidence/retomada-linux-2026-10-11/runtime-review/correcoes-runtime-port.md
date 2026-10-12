# Correções do candidato runtime-port, 10/10/2026

Commit local: `187ff96279ed8e6d3da14b7663ce5b5e856dff49`. Tree: `70580c660a5642e9f7226c0d9813b805ee8b3c64`.
Branch `codex/issue-7-runtime-port`, worktree exclusivo. Sem publicação Git.
Base revisada: `40aca4c14c09a2fe5058eec9bfce9c7b08f5379c`.

## Findings tratados

- P2 publicação: conjunto completo gerado/validado em staging antes de trocar;
  manifesto anterior preservado. Destino/staging rejeitam symlinks e reparse;
  Windows junction real foi testada. Backup preservado, rollback e recuperação
  por journal estrito. Duas renomeações individuais, não exchange atômico único;
  nenhuma garantia de leitores concorrentes ou recuperação automática de lock
  cuja interrupção antecedeu a escrita do journal.
- P2 proveniência: revisão Git limpa/untracked do WasmHome + hashes geradores;
  IDF5.2.6 SHA/tag/origin/tree/submódulos; comparação completa do SDK ao tar
  oficial WASI27; wamrc binaryhash vinculado ao recibo com fontes materiais,
  caches, comandos, ferramentas e bibliotecas LLVM. Ausência de prova bloqueia
  antes de criar staging. Recibo sem assinatura e sem rebuild independente;
  relato de diretórios novos depende da confiança no doador. Não existe ainda
  recibo real wamrc/LLVM nem artefato AOT real desta entrega.
- P3 JSON/identidade: parser estrito recusa duplicadas aninhadas; lock/recibo
  vinculados à identidade própria, versão2.4.5 e AOT5 do core, IDF==5.2.6.
  A referência Espressif foi explicitamente limitada a delta de comparação
  revisada; o acquirer não autentica checkout do fork. Ele verifica objetos
  upstream/framework e hashes do delta/resultados, conforme o lock.
- P3 warnings: removido -Wno-type-limits global. Sem exceção substituta até
  diagnóstico real. Patch, metadata, arquivos alterados e pacote rehashados.

## Verificação na revisão congelada

`tools/test-platform.ps1`: **60 testes aprovados, zero skips**, codec e runner
compilados com MSVC. Log `fixes-test-platform-frozen.log`, SHA256
`e484e050fb38ee05606b8fadb2beb71af28ed1d4745d495169473f482c8fe585`.
`git diff --check` e parser PowerShell passaram. Aquisição fresca real e
repetição verificaram o digest `7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`.
`fixes-source-provenance.json` registra revision/tree/hashes geradores reais.

Os negativos incluem falha real do processo gerador, ausência de recibos,
frontend PowerShell sem prova, conjunto inválido, swap/rollback/interrupção,
junction real, JSON ambíguo, cache/ferramenta/commands incompatíveis, source
untracked e core/metadados/pins divergentes. Não equivalem a compilar B/C.

## Dependências e limites preservados

`dependencias-linux-nota.md` distingue simulação 28 novos/6 upgrades, que
contraria “Não alterar Python do sistema”, da nova 21 novos/0 upgrades/removes.
Venv sem pip + bootstrap PyPA fixado/hash em userspace segue candidato sem
prova executada. Headers libssl/libffi e compilador host ficam fora da rota
pré-compilada/wheels, com gate real de Python3.13 e fallback userspace separado.
Não foi executado APT, build LLVM/wamrc, alteração CT/Proxmox/WSL/SDK ou flash.
#7/#9 continuam pendentes de revisão, instalação/validação real e aceite.

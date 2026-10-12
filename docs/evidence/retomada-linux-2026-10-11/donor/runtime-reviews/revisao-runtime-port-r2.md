# Re-revisão adversarial do port WAMR candidato — rodada 2

Data: 2026-10-10
Worktree: `C:\Users\inomi\.codex\worktrees\issue7-runtime-port\wasmhome`
Base da rodada: `40aca4c14c09a2fe5058eec9bfce9c7b08f5379c`
HEAD: `187ff96279ed8e6d3da14b7663ce5b5e856dff49`
Tree: `70580c660a5642e9f7226c0d9813b805ee8b3c64`
Delta: 18 arquivos, 856 inserções e 166 remoções. Checkout estava limpo no início.

## Resultado dos achados anteriores

1. **Preservação da saída e contenção de paths — resolvido para geração serial.** `tools/generate_artifacts.py` rejeita symlink/junction/reparse em destino, estágio e backup; só chama publicação depois da compilação e validação do conjunto completo. Renomeia a saída anterior para backup, renomeia estágio para o caminho canônico e tenta rollback em falha. Journal e `--recover-publication` preservam dados para recuperação após interrupção. O intervalo entre as duas renomeações pode deixar `artifacts/` ausente e concorrência de leitores segue proibida; a documentação assume esse limite. `.gitignore:1` (`build*/`) cobre journal/stage/backup, e a regra nova cobre `artifacts/`; portanto a rechecagem de limpeza da revisão não se contamina com os próprios outputs.

2. **Proveniência do compilador — requisito documental claro; prova real ausente nesta entrega.** O preflight fixa WASI-SDK 27 por digest, ESP-IDF 5.2.6 por origem/SHA/estado/submódulos, WasmHome por revisão/tree/hashes de geradores e vincula o hash do executável `wamrc` ao recibo estruturado e à identidade do runtime. O recibo `wasmhome-wamrc-build-v1` é JSON sem assinatura; suas declarações de diretórios limpos, fontes e comandos não são autenticação criptográfica. `docs/proposta-toolchain.md` (linhas 210–223 e 275–279) exige registrar origens, comandos, versões, flags, logs e hashes, além de revisão adversarial e geração real dos artefatos. As linhas 263–266 exigem repetir a produção numa segunda árvore/build limpos; isso se refere à geração/repetição dos artefatos e não exige dois builds independentes de LLVM/`wamrc` ou assinatura externa. Um build real conduzido nesta sessão, seguido de conferência separada de fontes, comandos e outputs pelo revisor, pode cumprir a proveniência documentada se o recibo permanecer identificado como unsigned e não se alegar autenticação independente nem reprodução independente do compilador. Nesta entrega revisada ainda não há recibo de build real, LLVM/Xtensa ou artefatos B/C, então esses requisitos concretos continuam pendentes.

3. **JSON, identidade semântica e origem do port — corrigido com escopo residual explícito.** `evidence_json.py` rejeita chaves duplicadas recursivamente e constantes não finitas; lock/recibo usam esse parser. O lock versão/AOT cruza com `idf_component.yml`, `core/config.h` e `core/version.h`, além dos pins exatos exigidos pelo gate. A referência Espressif em `port_delta_reviewed_reference` continua sem verificação automática do checkout do fork; código e docs agora chamam-na trilha de revisão, não prova. O acquirer verifica upstream/framework e os bytes/hash do patch local. Não declarar que a origem histórica do patch foi autenticada.

4. **Supressão de warning — resolvido.** O delta remove `-Wno-type-limits` do patch do componente e a documentação registra que não há diagnóstico real que justifique supressão global.

## Limites desta rodada

Li o delta e os controles/testes adicionados; não executei a suíte, geração real de Wasm/AOT, ESP-IDF, LLVM/`wamrc`, hardware, rede/CT ou GitHub. Os testes com recibos fixture/mocks validam gates e falhas de processo; não são evidência de compilação real. Root executará a suíte MSVC separadamente. Nenhum código ou arquivo rastreado foi alterado nesta revisão.

Não encontrei outro bloqueador de código para a preparação local do candidato, mantendo os limites documentados: saída publicável ainda depende de prova real do toolchain, e a origem do compilador/patch não é autenticada independentemente por este pacote.

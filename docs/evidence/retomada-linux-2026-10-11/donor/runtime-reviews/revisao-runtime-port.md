# Revisão adversarial do port candidato WAMR

Data: 10/10/2026
Worktree: `C:\Users\inomi\.codex\worktrees\issue7-runtime-port\wasmhome`
Branch: `codex/issue-7-runtime-port`
HEAD revisado: `40aca4c14c09a2fe5058eec9bfce9c7b08f5379c`
Tree: `9b877b8e413642f86e6f15dca366d04880d16461`
Base: `021643140b43d290fb52717f99b14864761b79f4`

## Escopo e provas

Revisado o delta de 16 arquivos (650 inserções, 102 remoções) contra `docs/proposta-toolchain-decisoes.md` e `docs/runtime-port-candidato.md`, incluindo lock, patch Espressif, gerador/verificador de pacote, integração CMake/Kconfig, manifesto e testes. O `git diff --check` passou e o checkout candidato permaneceu limpo.

Executei somente a verificação offline `python tools/runtime_package.py`: passou e retornou o digest `1f5af199cf16d7774c29e35a7ce471998f01dcc6451a2508901fbaa090ea775c`, upstream `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, framework `e97860611e371d91d2ef25101b02b29719476a48` e AOT 5. Os dois caches locais apontam aos SHAs declarados e não mostraram alterações. Root informou a suíte final de 49 testes, sem skips, em `build-wire/candidate-runtime-final-tests.log`; não a repeti.

O pacote materializado contém WAMR 2.4.5 (`core/version.h` 2.4.5 e `AOT_CURRENT_VERSION 5`). A validação de artefatos exige identidade/runtime 2.4.5, manifesto v2 e os oito bytes do cabeçalho AOT v5; o `wamrc` deve reportar 2.4.5 e o SHA declarado é restrito ao upstream 2.4.5. Os payloads antigos v4 e o uso do runtime 2.4.0~1 não passam esses gates.

## Findings

### [P2] Preservação e contenção do destino de artefatos

`tools/build-artifacts.ps1:5,28-33,76-83` resolve `artifacts/` lexicalmente, sem rejeitar junction/symlink, remove o manifesto antes da compilação e substitui cada payload em sequência com `Move-Item -Force`. Uma falha de compilação já descarta o recibo válido anterior; interrupção durante a publicação deixa payloads mistos sem manifesto. O validador impede aceitar esse conjunto incompleto, mas não preserva a saída válida anterior, e um destino por reparse point pode redirecionar escritas para fora da árvore. Esta rotina não foi executada nesta revisão. Antes de gerar a próxima saída, exigir destino real contido no worktree e publicar um diretório preparado/verificado por troca atômica, preservando a saída anterior até o commit marker novo.

### [P2] Pins de toolchain e revisão da árvore não são provados pelo gerador

`runtime-port/idf_component.yml:4-7` aceita qualquer IDF `>=5.2,<5.3`. `tools/build-artifacts.ps1:6-26,61-73` aceita `WASI_SDK_PATH` sem versão/digest do pacote, registra apenas identidade/hash do clang e aceita `-WamrcSourceRevision` como declaração do operador; não confere checkout limpo do compilador nem prova a origem do binário. O manifesto contém hashes de `benchmark.c/.h`, mas não SHA/limpeza da revisão WasmHome nem hashes dos scripts geradores. `tools/validate_build_config.py:66-73,167-189` também não valida a revisão IDF. Isso não impede verificar o pacote de fontes preparado, mas deixa sem prova os requisitos da receita de gerar os artefatos da mesma revisão aprovada com IDF 5.2.6, WASI-SDK 27 e toolchain fixada (`docs/proposta-toolchain.md:249-257,262-267`). Corrigir/fechar esses campos antes de chamar #7 aceito; os documentos atuais corretamente mantêm a geração e adoção pendentes.

### [P3] Verificação de identidade aceita JSON ambíguo e campos do lock são fonte única

`tools/runtime_package.py:38-45,125-129` usa `json.loads` sem rejeitar chaves duplicadas. Também calcula `component`, `version` e `aot_version` diretamente de `lock.json`, sem cruzar `version` com o YAML nem `aot_version` com a constante do core; `port_origin` em `runtime-port/lock.json:14-17` é declarativo e não é consultado pelo verificador, que valida somente upstream/framework e os hashes do patch. O pin rastreado atual bate com as fontes e os gates de artefato posteriores impõem os valores 2.4.5/v5, portanto não achei bypass para os builds E1 mantendo este commit e os fontes fixos. Ainda assim, o CLI isolado pode apresentar identidade enganosa se lock/recibo locais forem editados, e um recibo JSON com chaves duplicadas contraditórias pode ser aceito pela regra “último valor vence”. Recomendo parse estrito e asserts semânticos antes de usar o CLI como recibo de identidade.

### [P3] Supressão de warning sem justificativa no contrato

`runtime-port/esp-idf.patch:60` acrescenta `-Wno-type-limits` ao componente inteiro. Essa alteração não aparece entre os deltas justificados em `docs/proposta-toolchain-decisoes.md:154-160`, e nenhum build B/C demonstra qual diagnóstico exige a supressão. Documentar a justificativa e o diagnóstico exato ou remover a flag após build reproduzível.

## Limites

A checagem comprova integridade do pacote local conforme o lock fixado, não a origem do executável `wamrc`, compatibilidade CMake/Kconfig em build real, adequação de RAM/stack, execução de B/C nem aceitação do runtime. Não houve download, instalação, build ESP-IDF/LLVM, execução física, flash ou alteração remota/GitHub. O pacote candidato está preparado e verificável; os achados P2 mantêm bloqueada a geração rastreável/aceitação final de #7 até serem tratados.

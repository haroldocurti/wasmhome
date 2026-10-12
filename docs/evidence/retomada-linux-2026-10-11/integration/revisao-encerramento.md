# Revisão de encerramento E0/#10 e proposta #7

Data: 10/10/2026. Revisão somente leitura; nenhum código rastreado, hardware, instalação, estado remoto, GitHub ou publicação foi alterado por esta revisão.

## Pins e escopo

- Árvore conjunta revista: `7d4a494bc6e2dd7b74e8c8c46ced764b9689d64d`, tree `dbf88754836ead5dab3daa0d73d32242341a11ed`, pai `2b09fd79d5a6d2b7e7bab08e16b1071edcd33545`, tree `5479211cbe38a8f4a3f3fe6078ae0c64d4c242f6`.
- #10 isolado: `7b8e47025adaee8600af22e2c51d91a96a4257c3`, tree `3359bc585623d27a5d00db7ede6e5890418f7f5b`, pai `810b8bea76faf11854d5fe31694dc69c76095a02`.
- Revi o delta final `2b09..7d4` (verificador nativo, testes e contagens/documentação), além do delta corretivo `810b..7b8` do observador. A revisão integral prévia `9d332d6..810b8be` nos 34 arquivos e seus contratos/evidências está registrada em [revisao-observador.md](revisao-observador.md). A questão do relógio foi tratada conforme resolução/tolerância observada; não infiro falha dos dados físicos.
- A lista de mudanças locais em `7d4` contém apenas documentação, evidências versionadas e o verificador/testes citados. `git diff --check` passou. A árvore rastreada está limpa; existem arquivos não rastreados fora deste recibo (`.codex-remote-attachments/`, `results/`, `tmp/`).

## Achados rechecados

Não restam bloqueadores técnicos nos deltas revistos para o escopo E0/#10 e para preparar a próxima execução da #7.

Os dois pontos P3 do recibo anterior de #10 foram resolvidos no conjunto atual. A diferença de schema dos resumos host antigos, que não têm `valid_controls_by_boot`, fica explicitamente documentada sem reescrita retroativa. As recapturas da fonte exata e da fonte após a correção de outputs permanecem em `docs/evidence/observer-revalidation-2026-10-10/`. Conferi SHA-256 e tamanho de todos os 16 arquivos do inventário contra os blobs Git de `7d4a494`; todos coincidem. As calibrações host após a correção tiveram erros de 21 ms/100 ms no mesmo boot e 103 ms/200 ms no reboot simulado, dentro das tolerâncias declaradas. São loopback host-only, não nova prova física.

A proteção de saída do observador foi corrigida: captura e calibradores abrem nomes de saída em modo exclusivo; os calibradores exigem diretório novo. Os testes negativos preservam arquivos anteriores e mostram que a colisão falha antes de rede/serial. `tools/test-platform.ps1` na exportação #10 corrigida passou 30 testes sem skips. Não houve alteração do algoritmo de medição nem do firmware, portanto não houve nova coleta física.

Na primeira rechecagem, encontrei que `--prepare` ainda sobrescrevia metadados de build em `native/`. O delta `2b09..7d4` corrige isso: `--prepare` exige `--prepare-output`, valida os insumos antes de criar o destino novo, recusa diretório/symlink existente, grava em modo exclusivo e termina com resultado explicitamente limitado a metadados de build. O modo padrão verifica os registros físicos sem escrita; derivação continua opt-in em pasta nova. O teste cobre arquivos anteriores intactos, build ausente sem criar destino e uso da CLI sem destino. Comparei os cinco arquivos gerados em `build-session-e0/native-prepared-exclusive/` com os cinco preservados em `native/`: todos são byte-idênticos. A saída separada do verificador físico mantém `transport_status=unvalidated_connection_failed`; a falha de transporte não foi descartada.

O parser JSON usa `object_pairs_hook`, rejeitando chave duplicada em objetos aninhados. Os testes verificam a rejeição e preservação dos derivados anteriores. A exigência explícita de `--disable-llvm-lto` no gate AOT também está coberta por teste negativo. Isso não equivale a compilar ou executar WASI/WAMR/AOT.

## Proposta de toolchain #7

Revisei `docs/proposta-toolchain.md` e `docs/proposta-toolchain-decisoes.md` como proposta de preparação. O GCC `esp-13.2.0_20250707` e SHA do pacote batem com o `tools/tools.json` da revisão fixada ESP-IDF 5.2.6. O release PowerShell 7.6.6 e o digest escolhido também constam na página oficial de release. O commit `25bd7eb...` é o release upstream WAMR 2.4.5; o helper dessa revisão continua selecionando `xtensa_release_18.1.2`, cujo commit Espressif está fixado. As referências oficiais consultadas estão nos próprios documentos: [tools.json IDF](https://github.com/espressif/esp-idf/blob/9ef24e3e2a2c96e720d83c574a3f8699177573da/tools/tools.json), [release PowerShell 7.6.6](https://github.com/PowerShell/PowerShell/releases/tag/v7.6.6), [release Python 3.11.17](https://www.python.org/downloads/release/python-31117/), [release WAMR 2.4.5](https://github.com/wasm-micro-runtime/wasm-micro-runtime/releases/tag/WAMR-2.4.5) e [helper WAMR fixado](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/25bd7eb63e828e4bd242cc9b38d260b4b31c6605/build-scripts/build_llvm.py).

A proposta separa a rota histórica 2.4.0 da recomendação futura 2.4.5 e não chama nenhuma de pin aprovado. Identifica a incompatibilidade AOT v4/v5, proíbe misturar `wamrc` 2.4.5 com runtime Espressif 2.4.0~1, lista os deltas do port ESP-IDF que precisariam ser transplantados e exige recompilar/validar a matriz E1 após migração. Registra que Python 3.13.5 ainda precisa de resolução/import/build reais no LXC; Python 3.11.17 é fallback de origem oficial, não prova de compatibilidade. PowerShell 7.6.6 tem pacote e hash oficial, mas o gerador Linux ainda precisa de teste real.

Os limites de ambiente são explícitos: CT107 tem 1 GiB e não deve compilar LLVM; a amostra do doador WSL tinha 13 GiB disponíveis, mas o VHDX é limitado pelo espaço físico livre em C: (27,82 GiB na amostra). O orçamento de 8 GiB/20 GiB e `-j1` é gate conservador, sem garantia de build. `TASK_ROOT`, clones, fontes com untracked/ignored e diretórios de build precisam ser novos/limpos; o recibo futuro terá pins, comandos, logs, hashes e bibliotecas. O texto não afirma que um SHA digitado, por si só, prova origem de binário, nem promete determinismo. A rota e o port ainda exigem validação antes de qualquer instalação.

## Verificação e limites

A validação local documentada no pin final conta 68 testes sem skips (47 + 9 + 6 + 6); os logs locais de revalidação e os verificadores offline preservados foram consultados. A #10 isolada corrigida tem 30 testes sem skips. A amostra E0 nativa permanece uma sessão única de 30 chamadas com checksum `0xDBB2F0D4` e otimização `-O3` efetiva (`-O2` anterior foi sobrescrito por `-O3` no comando final). Ela não prova 30 atualizações independentes, transporte funcional, três modos E1, OTA nem campanha.

Não há CI configurada. Esta revisão não verificou publicação GitHub, integração em `main` ou aceite formal de #6/#7. Não houve instalação de toolchain, build LLVM, transferência ao LXC ou geração/execução Wasm/AOT. Nenhum trabalho das issues #11–#24 foi considerado implementado. O parecer técnico não é aceite de issue nem autorização de publicação.
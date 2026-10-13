# Harness da issue #11 publicado para revisão no repositório — r4 (13/10/2026)

Após os pareceres independentes B e C aprovarem o conteúdo r3, copiei o utilitário, seus testes fake e a instrução de uso para novos caminhos no checkout compartilhado. O script agora importa o codec adjacente a `tools/issue11_harness.py`, e o teste acrescenta `tools/` ao caminho a partir do checkout, sem depender do caminho absoluto local. A documentação usa o caminho do repositório, descreve GPIO/flash/OTA e explicita que a validação de boot evidence confere identidade apenas; o coordenador ainda deve garantir/correlacionar reset recente.

Arquivos novos e SHA-256:

- `tools/issue11_harness.py`: `3b4f59e3e05097b0e03b614a88c06de7a48834a89e7a02a7fb73b5199562c038`
- `tests/test_issue11_harness.py`: `8060bcad184b9d59b0691a96dc16cf445e3a471d96d5d641ae32d3f0f568607d`
- `docs/issue11-harness.md`: `d85aee1e476a95ae2221cdeff666c92c26e8db425df0e874d9a63f731a2863cf`

Validação: `python3 -B tests/test_issue11_harness.py -v` passou 7/7; a suíte completa com PowerShell portátil, CMake, Ninja e codec C passou 89 testes, 1 skip esperado de Windows junction, sem falhas. Log completo `/tmp/wasmhome-issue11-harness-20261013/unittest-full-repo.log`, SHA-256 `95fc4da1f779b87245352b969422ef39298e50e1c5355bf505dfb7ab241b069c`. `git diff --check` não reportou problemas e os arquivos novos não têm whitespace trailing.

Os pareceres prévios examinaram o conteúdo r3: B em `/tmp/wasmhome-issue11-harness-review-b-r3-20261013.md` (SHA `c7e91edf8a52b130ab6180e05dfc1eafac17056ef9565338080f223b3cc0a5af`) e C no canal de revisão. A cópia ao checkout altera apenas imports relativos e caminhos/instruções; o harness continua opt-in e não foi executado contra placa. Não abri serial nem enviei UDP. Os diretórios antigos r2/r3 privados e seus relatórios/logs seguem preservados.

# Harness UDP da issue #11 — revisão r3 (13/10/2026)

A revisão de metadados corrigiu a descrição das ações no manifesto: datagramas `CONTROL_REQUEST` exercitam o caminho de controle GPIO e podem atuar a saída; o replay de um ID já admitido espera resultado em cache, sem solicitar nova aplicação; `UPDATE_TRIGGER` é enviado para validar uma rejeição `BUSY`; o utilitário não pede gravação de flash nem transferência OTA.

O manifesto e o README também agora dizem que a verificação de boot-evidence só confere correspondência do ID em `boot_observed`. Ela não confirma recência do log ou que houve reset. A execução física continua a exigir que o coordenador assegure reset recente e correlacione a captura serial de startup com o boot ID da telemetria.

Verificação r3: **7/7 testes fake passaram**; adicionalmente, asserções smoke confirmaram os campos de ações e limites de validação de boot do manifesto. Log: `/tmp/wasmhome-issue11-harness-20261013/tests-r3-metadata.log`, SHA-256 `d1c19f5d95751afeb2f10540bfd3e88e9316899686976c7096f0d6f0f5c4fc77`.

Hashes SHA-256 atuais:

- `issue11_harness.py`: `21a49cabd7cf53fb8d8a11fc7de16167aa04427ab45b5452ee0d58a0741d1812`
- `test_issue11_harness.py`: `6b91e0a3c119c7bfd74febc3774586dbf5aba80223544167a671447ceba7bf8d`
- `README.md`: `476e379ca0a70adb4bae206be64c36a2bc7d490e3b1a6f9106b32272b562e330`
- log r3: `d1c19f5d95751afeb2f10540bfd3e88e9316899686976c7096f0d6f0f5c4fc77`

Os arquivos correspondentes ao r2 continuam preservados como `issue11_harness-r2.py`, `README-r2.md`, além do relatório e logs r2. Nenhuma porta serial foi aberta nem pacote UDP enviado. A revisão C desta correção está em andamento; o utilitário ainda não foi copiado ao repositório.

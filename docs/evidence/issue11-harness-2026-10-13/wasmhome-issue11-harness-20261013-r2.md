# Harness UDP da issue #11 — revisão r2 (13/10/2026)

Atualizei a preparação privada em `/tmp/wasmhome-issue11-harness-20261013/` para rejeitar `NaN`, `+Infinity`, `-Infinity` e timeout acima de 120 segundos. A validação ocorre antes de resolver/criar a saída, abrir socket ou transmitir. O limite operacional de 120 segundos evita espera acidentalmente ilimitada na coleta de amostras.

Teste focal executado com transporte falso: **7/7 passaram**. O teste novo exercita os quatro valores inválidos por `main(... --execute ...)` e verifica erro de argumento, ausência de chamada a `socket.socket` e inexistência do diretório de saída. Os demais testes continuam cobrindo os vetores v1/v2, boot e recusa de telemetria envelhecida. Log r2: `/tmp/wasmhome-issue11-harness-20261013/tests-r2-finite-timeout.log`, SHA-256 `d1c19f5d95751afeb2f10540bfd3e88e9316899686976c7096f0d6f0f5c4fc77`.

A versão anterior do relatório está preservada em `/tmp/wasmhome-issue11-harness-20261013.md`; o primeiro teste pós-ajuste está em `tests-r1-finite-timeout.log`. O caso `-inf` do r1 foi interpretado pelo argparse como argumento ausente, então o r2 usa `--telemetry-wait-seconds=-inf` e confirma a validação numérica prevista.

Hashes SHA-256 atuais:

- `issue11_harness.py`: `43122061a0d734f57d39bbdc30f35825d242b180df35187a5543563722ac3ec2`
- `test_issue11_harness.py`: `6b91e0a3c119c7bfd74febc3774586dbf5aba80223544167a671447ceba7bf8d`
- log dos testes r2: `d1c19f5d95751afeb2f10540bfd3e88e9316899686976c7096f0d6f0f5c4fc77`

A revisão B anterior é `/tmp/wasmhome-issue11-harness-review-b-20261013.md`, SHA-256 `ef507f16a5ca36c55e057fab0d8726a54f97cf8dd659fd246e19c367e021de15`; ela recomendou precisamente a rejeição finita antes de output/socket. Esta versão aguarda revisão dirigida de B e revisão C. Nenhuma porta serial foi aberta e nenhum pacote UDP foi enviado. A captura física/backup já foi feita pelo coordenador; este harness continua apenas preparado, sem execução física.

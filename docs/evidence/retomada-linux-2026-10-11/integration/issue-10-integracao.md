## Parent

[Obter resultados da campanha WasmHome em três cenários](https://github.com/haroldocurti/wasmhome/issues/1)

## What to build

Disponibilizar a ferramenta externa que mede o serviço mesmo durante reinicializações do ESP32-S3.

## Acceptance criteria

- [x] Enviar estímulos e disparos UDP e persistir todos os eventos em JSONL.

- [x] Validar resultados, versões, IDs, boots, perdas, duplicatas e atrasos.

- [x] Detectar dentro da resolução esperada uma interrupção controlada de duração conhecida.

## Blocked by

- [Fechar o protocolo binário de observação](https://github.com/haroldocurti/wasmhome/issues/3)

## Integração em 10/10/2026

Entrega integrada pela [PR #28](https://github.com/haroldocurti/wasmhome/pull/28), merge `6c94ab505672e4056c1a87932403853fe9aa06e7`. A árvore final `3359bc585623d27a5d00db7ede6e5890418f7f5b` coincide com a revisada. Trinta testes locais passaram sem skips; revisão adversarial Luna xhigh sem bloqueadores remanescentes. Não há CI configurada.

A proteção das saídas recusa arquivos existentes antes de abrir rede ou serial. As recalibrações host após essa correção passaram e estão nas [evidências integradas da bancada](https://github.com/haroldocurti/wasmhome/blob/8b372147e739e46c8952ab8c7907b6c76a76345b/docs/evidence/observer-revalidation-2026-10-10/README.md). As [provas originais](https://github.com/haroldocurti/wasmhome/blob/6c94ab505672e4056c1a87932403853fe9aa06e7/docs/observador-externo.md) continuam vinculadas à fixture e ao firmware originais. Esta conclusão não comprova serviços definitivos, OTA, Wasm/AOT ou campanhas, tratados nos tickets próprios.

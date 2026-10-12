# Integração proposta

As duas entregas foram publicadas e integradas em 10/10/2026, após autorização explícita do usuário. O conteúdo abaixo preserva a proposta original e seus pins; o resultado efetivo está registrado nesta seção.

- [PR #28](https://github.com/haroldocurti/wasmhome/pull/28): merge `6c94ab505672e4056c1a87932403853fe9aa06e7`, árvore `3359bc585623d27a5d00db7ede6e5890418f7f5b`; 30 testes sem skips.
- [PR #29](https://github.com/haroldocurti/wasmhome/pull/29): merge `8b372147e739e46c8952ab8c7907b6c76a76345b`, árvore `56a6f161277d16ba725a536b76230aecf1e120d6`; 68 testes sem skips e 67 blobs conferidos. A árvore final foi validada antes da integração e conferida após o merge.
- Issue #6 encerrada com seus sete critérios respaldados, comentário de resolução e remoção de `ready-for-agent`; #1 e #10 atualizadas com os vínculos reais. Leitura posterior confirmou #7 e #9 abertas.
- As duas branches foram removidas após conferência de ancestralidade. CI não está configurada. Não houve deploy.
- A execução completa E0–E7 continua pendente: o candidato local da #7 está em correção após revisão adversarial; Wasm/AOT, serviços, atualizações, piloto, campanhas e análise têm gates próprios.

## 1. Observador externo, issue #10

Branch `codex/issue-10-external-observer`, commit `7b8e47025adaee8600af22e2c51d91a96a4257c3`.

O observador externo passa a enviar estímulos e disparos UDP, preservar eventos em JSONL e distinguir resultados, versões, IDs, boots, perdas, duplicatas e atrasos durante interrupções e reboot do ESP32-S3. A calibração cobre interrupção conhecida no host e na placa; ela não comprova os serviços definitivos nem uma atualização real.

Entrega da issue #10 no commit `7b8e47025adaee8600af22e2c51d91a96a4257c3`, com 30 testes locais sem skips. Duas recalibrações host passaram, com erros de 21 ms/100 ms e 103 ms/200 ms. A proteção dos resumos agora recusa arquivos anteriores antes de abrir rede ou serial. Os registros físicos citados em `docs/observador-externo.md` continuam vinculados à fixture original, sem mudança no algoritmo. CI não está configurada. A integração antecede as implementações dependentes.


## 2. Bancada E0 e preparação E1

Branch `codex/execucao-plano-e0-e1`, commit para publicação `569b6a3dee873e21ad57a6d020187f7be53cf6e4`, tree `56a6f161277d16ba725a536b76230aecf1e120d6`. Fonte funcional revisada: `7d4a494bc6e2dd7b74e8c8c46ced764b9689d64d`.

A issue #6 estava sem prova ESP32→LXC, reserva de endpoint registrada e reconstrução separada. A sessão de 09/10 preserva o percurso TCP com nonce pareado, um negativo de origem divergente, a confirmação humana da reserva DHCP e a reconstrução autorizada no CT 109. SSH e persistência após reboot passaram; o CT 109 ficou parado e o CT 107 permaneceu em execução. O firmware original da placa foi restaurado e verificado.

O checklist E0–E7 passa a rastrear requisitos, dependências e limites de aceite. A preparação de E1 corrige buffer/alinhamento/limpeza do runner, valida configurações e proveniência de artefatos e exige `-O3` efetivo. A carga nativa executou 30 chamadas com checksum correto no ESP32-S3. Essa prova é local de A: toolchain no LXC, Wasm/AOT, serviços e campanhas continuam pendentes.

Validação: 68 testes locais sem skips, builds ESP-IDF nativos, verificadores offline contra logs físicos, 67 blobs dos inventários conferidos contra seus SHA-256 e revisão adversarial Luna. O [recibo rastreado](../docs/evidence/execucao-2026-10-09/revisao-adversarial.md) cobre a fonte funcional; o [recibo final documental](revisao-metadados-final.md) cobre o pin de publicação. Os achados de concorrência de diagnóstico, flags de otimização, relógio de captura, preservação dos derivados e ambiguidade JSON foram corrigidos. A primeira captura inconclusiva e as divergências do instrumento/ifupdown2 foram preservadas. CI não está configurada; este registro não constitui publicação, integração ou aceite formal do rastreador.

Após integração e aceite, atualizar a issue #6 e o mapa #1 somente com critérios respaldados por estas evidências. Não encerrar #7, #9 ou os tickets posteriores.


## Operação a autorizar

Publicar as branches, abrir as duas PRs, validar a árvore exata de cada integração
em ordem e integrar na `main`. Depois, aplicar os critérios comprovados da #6,
comentário de resolução e mapa #1. A #10 já está fechada; atualizar seu vínculo
de integração. Não encerrar #7, #9 ou campanhas sem suas provas próprias.

Os rascunhos de rastreador estão em `issue-6-proposta.md`,
`issue-6-resolucao.md` e `issue-1-proposta.md` nesta pasta. Seus marcadores de
PR/merge serão preenchidos com os resultados reais antes de enviar ao GitHub.

A bancada foi reconfirmada por leitura em 10/10: CT107 running, CT109 stopped.
A coleta física de 09/10 restaurou e verificou o app original do ESP32-S3.

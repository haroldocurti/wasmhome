# Revisão final de metadados da #6

Data: 10/10/2026. Revisão somente leitura dos documentos versionados do pin `8c88dd5f8c2d7d0990563a2aef6378e8f8f6c625`, tree `69e00eabe2397b019dfef01d03564eb5635fa3a6`. O delta desde `7d4a494bc6e2dd7b74e8c8c46ced764b9689d64d` contém apenas `docs/checklist-execucao.md` e três recibos/documentos em `docs/evidence/execucao-2026-10-09/`. `git diff --check` passou.

## Conferência

Comparei a matriz com os sete critérios e o escopo de testes atualmente visíveis na issue #6 via `gh issue view 6` somente leitura. A issue está aberta; os quatro primeiros critérios continuam marcados, e os três restantes eram os caminhos de rede, endpoint estável e reconstrução. A matriz aponta evidência apropriada para cada um: captura PC↔ESP32 e positivo ESP32→CT107 com nonce e fluxo UDP no mesmo boot; confirmação humana da reserva DHCP, separada da observação IP/MAC e da ausência de inspeção do painel; CT109 separado, com identidade, reboot/persistência e estado parado. O negativo está descrito com o limite correto: origem divergente recusada, sem nonce que correlacione a transação nos dois lados. A primeira captura permanece inconclusiva. Não encontrei afirmações de HTTPS/COSE, serviço final, aceitação da issue, integração ou implementação das issues #11–#24.

O recibo adversarial reproduz os pins/escopos históricos e corrige a referência para a árvore #10 corrigida; distingue as 30 verificações na árvore isolada do teste original do export. O recibo #10 está claramente apresentado como revisão histórica do delta `9d332d6..810b8be`, com o acompanhamento posterior das duas questões P3 registrado no recibo de encerramento. A checklist preserva a separação entre revisão técnica, publicação, integração e aceite formal. A consulta da issue #10 confirmou que ela está fechada, o que não altera a conclusão de que o commit revisado ainda requer integração na `main`.

Verifiquei todos os links Markdown locais nos quatro documentos modificados: 52 destinos relativos, todos resolvidos; 59 links totais contando sete links externos. O diff não inclui mudança de código, configuração ou evidência bruta. A validação local citada permanece 68 testes sem skips; não a reexecutei porque este delta contém só documentação. A consulta SSH read-only de 10/10, informada e referenciada pelo registro, reconfirmou CT107 `running` e CT109 `stopped`; esta revisão não repetiu acesso remoto. A confirmação DHCP permanece humana, sem inspeção do painel.

## Achado P3

A abertura de `docs/evidence/execucao-2026-10-09/aceite-tecnico-6.md` afirma “Todos têm prova técnica local”. Isso é amplo demais para o critério do endpoint: a própria matriz corretamente registra que a reserva DHCP decorre de confirmação humana e que o painel não foi inspecionado. A observação de IP/MAC efetivos é prova técnica local, mas a reserva é evidência confirmada pelo usuário. Sugestão: “Todos têm evidência documentada para revisão técnica; a reserva DHCP é confirmação humana, com IP/MAC efetivos observados.”

O ponto é de precisão de linguagem e não elimina a evidência do critério, desde que a confirmação humana seja aceita conforme a alternativa de reserva DHCP da issue. Não encontrei outro bloqueador documental. A linha que mantém publicação, integração e aceite pendentes é fiel; este recibo não é aceite formal nem autorização Git.

## Rechecagem final do delta documental

No commit `569b6a3dee873e21ad57a6d020187f7be53cf6e4`, tree `56a6f161277d16ba725a536b76230aecf1e120d6`, o único delta desde `8c88dd5f8c2d7d0990563a2aef6378e8f8f6c625` é a abertura de `aceite-tecnico-6.md`. A frase agora distingue evidência documentada, confirmação humana da reserva DHCP e observação técnica de IP/MAC, além de manter publicação, integração e aceite pendentes. Isso resolve o P3 acima. `git diff --check` passou. Não encontrei achado residual neste delta; não repeti a revisão funcional nem reexecutei testes, conforme o escopo estrito da rechecagem.

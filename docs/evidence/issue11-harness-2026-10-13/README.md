# Instrumento de ensaio nativo — 13/10/2026

O utilitário `tools/issue11_harness.py` e seus testes foram preparados e revisados
por agentes Luna xhigh, com sete testes de transporte falso. A cópia integrada
teve revisão independente e a suíte completa executou 89 casos: 88 aprovados e
uma exclusão específica de junction Windows. Isso não é execução física.

O instrumento registra datagramas brutos e verifica telemetria real/fresca,
GPIO/loopback, limiares inclusivos, retenção na banda, replay, conflito, boot
incorreto e recusa BUSY. A execução requer argumentos e `--execute` explícito.
O coordenador deve garantir reset recente e correlacionar boot/firmware/log
serial; procurar um ID no arquivo não prova recência por si só. Os comandos
atuam deliberadamente GPIO; não solicitam gravação flash/OTA. Resposta/silêncio
usam defaults 300ms no ensaio. O intervalo de telemetria rejeita valores não
finitos e acima de 120s antes de qualquer saída/socket.

Relatórios anteriores preservam os achados e correções dos metadados. O relatório
r4 descreve a integração final; a documentação operacional está em
`docs/issue11-harness.md`.

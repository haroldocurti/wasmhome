# Revisão final do plano de agentes — 13/10/2026

Revisei integralmente `docs/plano-proximos-passos-agentes.md` e aceitei-o como plano documental. As dependências estão explícitas: P0 precede o fechamento #7 e bloqueia #9; P4 (#9) exige #7/#8; P5 (#11) segue independente de #9, condicionado a #8/#10; #15 aguarda #4/#7 e endpoint #6. A tabela posterior liga #12–#24 aos gates e mantém executor/revisor distintos, no limite de três agentes simultâneos além do coordenador. A decisão de histerese antecede #11; piloto e campanha preservam a escolha pendente entre 90 slots e 90 updates aceitos, com denominadores e reposições definidos antes de #20/#21.

O roteiro complementar de C concretiza a descoberta Linux da bancada: `/dev/serial/by-id/`, confirmação VID/PID/tty e interface/rota IPv4 ativas; referências COM/índices Windows ficam como histórico. Também detalha o instrumento A/B/C e SRAM/PSRAM, onde o coletor atual não exige conjunto completo nem valida checksum e o runner não mede PSRAM por fase.

Este aceite limita-se à organização e suficiência documental. P0 continua pendente de reconciliação, a suíte P1 registrada segue com 56/60 aprovados, uma falha ambiental e três skips, e o plano não constitui execução, revisão de firmware/binário ou resultado físico de #7/#9/#11. Não identifiquei outra lacuna estrutural que impeça usar o documento para coordenar as próximas frentes.

# Revisão B independente do harness da issue #11 — 13/10/2026

Revisão somente leitura do harness UDP em `/tmp/wasmhome-issue11-harness-20261013`. Não executei o modo `--execute`, não abri socket UDP físico nem serial. Rodei apenas os seis testes com dispositivo fake, usando `python3 -B`; todos passaram.

O modo padrão declara que não abre socket/não transmite e o teste correspondente substitui `socket.socket` por uma falha, além de confirmar que a pasta de saída não é criada. O modo físico exige `--execute`, IPs explícitos, uma origem de boot mutuamente exclusiva e entradas ELF/BIN existentes; evidência serial explícita precisa conter o `boot_observed` pedido. O boot por telemetria é descoberto antes do primeiro estímulo. O script fixa o peer IPv4, registra datagramas brutos e pacotes decodificados, confere boot/attempt/sequence/correlação, rejeita overwrite e cria diretório/arquivos com permissões privadas. Os vetores de histerese, a repetição exata, boot incorreto, conflito de sample ID, trigger `BUSY` e cinco IDs distintos DS18B20 válidos com idade máxima de 500 ms são exercitados nos testes fake e implementados no roteiro físico. Os campos `claims` deixam claro que o valor GPIO observado vem do loopback reportado pelo firmware e não de medição externa; o relatório não afirma callback não execução ou proveniência do firmware.

Um endurecimento pequeno é necessário antes de considerar as validações CLI completas: `--telemetry-wait-seconds` usa `float` e só testa `<= 0`; `nan` e `inf` passam essa condição. `nan` causa exceção ao converter prazo para nanossegundos depois dos estímulos de controle, e `inf` causa overflow nesse mesmo ponto. Recomendo rejeitar valores não finitos (por exemplo `math.isfinite`) antes de criar diretório/socket e impor teto compatível com a janela operacional. Não encontrei outro bloqueio no fluxo estático revisado. Este achado é de validação de entrada; não indica falha nos caminhos normais cobertos pelos seis testes.

Hashes revisados:

- Harness: `5a75c5951e82d6978b4aea569f993387c675e44269cd158207f69f6eb0d1257e`
- Testes fake: `4705f9ac6b046c3c83258469a2762691d25360d59b472c99629582ec0e825b36`
- Log declarado de A: `5e8292839c5618cb2f4b1e17ae4e7fdd54e7755af11078f68b7231755818fa47`
- Execução independente: 6 testes aprovados em 0,076 s.

Parecer: adequado para revisão de comportamento e preparado para ensaio opt-in após o pequeno gate de timeout não finito e o preflight físico definido no plano. Esta revisão não é autorização nem evidência de execução em placa.

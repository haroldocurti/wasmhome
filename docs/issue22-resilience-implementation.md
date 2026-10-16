# Implementação defensável da issue #22

Status em 2026-10-16: **integração parcial no firmware e testes focados de host**.
O build ESP-IDF, o ensaio preliminar e as 72 execuções da campanha não foram
realizados. Este registro não fecha a issue nem autoriza a campanha #23.

## Implementado no código

- `tools/resilience_harness.py` planeja exatamente 70 falhas e dois controles,
  valida pedidos fechados, correlaciona JSONL serial e observações UDP por
  `attempt_id`/`boot_id`, e preserva `unconfirmed` quando falta
  `fault_accepted`. Timeout ou silêncio não viram rejeição.
- `tools/generate_resilience_fixtures.py` gera nove conjuntos ENV-SIG/HASH/OLD
  a partir de artefato #15 verificado. A chave privada fica fora da saída; as
  fixtures não foram validadas no dispositivo.
- `main/resilience_test_protocol.c` aceita somente `event=fault_request`, UUID
  canônico `attempt_id` e `case_id` fechado. A linha não pode fornecer cenário,
  URL, caminho, chave ou opção extra. `fault_accepted` só sai após persistência
  NVS e leitura de volta, antes de enfileirar o mecanismo.
- O firmware opt-in oferece ENV-SIG/HASH/OLD para a atualização nativa. Os
  caminhos de manifesto e payload são literais por caso e cenário. O endpoint
  usa configurações próprias de IP, porta e nome TLS (`8445` como porta inicial),
  requeridos apenas no modo de ensaio. Os servidores de campanha existentes
  `8443` e `8444` não foram alterados nem reutilizados. O modo de ensaio vem
  desligado por padrão.
- ENV-SIG passa pelo parser/verificador de assinatura real e não inicia o sink
  se a assinatura for inválida. ENV-HASH usa manifesto assinado, prepara a
  tentativa no ledger e recebe o payload no slot OTA inativo; após divergência
  de hash, `sink_abort` apaga a partição OTA inativa inteira e lê todos os bytes
  de volta, exigindo `0xff`. O evento só registra rejeição aprovada se a leitura
  de estado antes/depois comprovar preservação do slot ativo, versão, piso e
  demais metadados ativos, além da invalidação.
- ENV-OLD ignora somente a comparação de sequência antecipada da política do
  manifesto. A assinatura/identidade ainda são validadas; a fixture então
  chega a `ledger_prepare`, que compara sua sequência com o piso NVS real e
  retorna `ESP_ERR_INVALID_VERSION`, reportado como `SEQUENCE_NOT_NEWER` no
  estágio `envelope_sequence`. Não há filtro antecipado do comando UDP nesse
  caminho.
- O callback modular `sink_abort` propaga erros. `wh_module_slot_store_abort`
  tenta apagar o slot candidato inteiro após qualquer tentativa de erase e lê
  de volta toda a partição; falha de erase, readback ou conteúdo não apagado
  retorna erro. A seleção do candidato continua sendo do caminho que reserva o
  slot inativo no ledger. Eventos diagnósticos distinguem falha de abort do
  sink e do ledger; chamadas de abort antes ignoradas agora reportam erro.
- NAT-ABORT mantém `attempt_id` em NVS, espera um controle UDP correlacionado,
  imprime marcador serial e chama `esp_system_abort`. Depois do reboot recupera
  o evento pendente e registra motivo de reset. A emissão local UDP é somente
  evidência de envio; o harness precisa confirmar recepção e medir a lacuna.
- O cabeçalho UDP v1 e seus tipos não foram alterados. Eventos de ensaio e
  `fault_accepted` são serial JSON; a tentativa pendente em NVS mantém a
  correlação após reboot.

## Evidência host desta revisão

Testes com shims executam código C real em `main/` para a integridade dos slots
modulares, o abort do ledger e o abort OTA:

- `tests.test_module_slot_store_host`: erase/readback do slot candidato e
  invariância do slot ativo; falhas de erase/readback não retornam sucesso.
- `tests.test_signed_update_ledger_c`: executa `validate_manifest_policy`,
  `ledger_prepare` e `wh_signed_update_abort`; ENV-OLD passa pela política sem
  sequência de comando e é rejeitado pelo piso NVS real do ledger. O abort
  preserva slot, versão e piso ativos tanto no caminho durável quanto quando o
  commit NVS falha.
- `tests.test_native_ota_sink_abort_c`: erase/readback da partição OTA inativa,
  slot ativo intocado, e propagação de falhas de `esp_ota_abort`, erase e
  readback.
- `tests.test_module_update_adapter_host`, `tests.test_native_ota_activation_c`,
  `tests.test_signed_update_parser_c` e os testes do harness/protocolo cobrem os
  adaptadores, validação do envelope, parser serial e correlação host.

Esses testes não compilam o projeto com ESP-IDF e não demonstram execução no
ESP32-S3.

## Estado por critério de aceitação

| Mecanismo | Código / teste host | Ainda necessário |
|---|---|---|
| ENV-SIG | Caminho nativo chama verificador real; parser e assinatura têm testes host independentes. | Compile opt-in, fixture HTTPS isolada e observação física da rejeição no estágio esperado. |
| ENV-HASH | Caminho nativo usa OTA sink real; teste host comprova erase/readback integral e proteção do slot ativo. O ledger conserva ativo/piso e tem teste host. | Compile opt-in e comprovação na placa do conteúdo gravado/inválido e estado ativo/piso. |
| ENV-OLD | Caminho real chega ao `ledger_prepare` com piso NVS; estágio `envelope_sequence` mapeado. Teste host executa a política sem filtro antecipado e a rejeição do ledger, preservando ativo/piso. | Fixture de bancada, compile e rejeição física com ledger previamente semeado. |
| ENV em B/C | O armazenamento modular e callback de abort estão integrados/testados em host. | Pedido serial e execução ENV-SIG/HASH/OLD não foram ligados aos perfis de módulo; provar cenários B/C ainda pendente. |
| WASM-ZERO/OOB/DIV0 | Planner e correlator host existem; os IDs passam pelo parser sintático fechado. | O dispatcher do firmware rejeita esses casos antes de `fault_accepted`; não há injetor, captura/classificação de exceção, aposentadoria do slot ou recuperação de controle conhecido. Não há prova com WAMR real nem telemetria pós-trap. |
| NAT-ABORT | Caminho de firmware e correlação após reboot implementados. | Build e prova física de pânico, novo boot e recuperação recebida por UDP. |
| Timeout funcional | `300 ms` permanece valor candidato, reportado como não congelado. | #20 ainda não congelou empiricamente o timeout. |
| Recuperação operacional | Harness calcula recuperação a partir das observações UDP. | Confirmar recebimento UDP e recuperação dentro de 30 s no ensaio físico. |
| Contagem | Planner produz 70 falhas + 2 controles. | Nenhuma tentativa aceita ou célula HIL foi executada; aceite de ensaio não equivale a aceite do artefato. |

## Preflight físico somente leitura (2026-10-16)

O coordenador executou `chip_id`, `flash_id` e uma leitura integral por esptool
em `/dev/ttyACM0`, sem gravar nem injetar falhas. A identidade foi confirmada
como ESP32-S3 rev0.2, MAC `dc:b4:d9:0b:22:e4`, com flash de 16 MiB. O dump vivo,
privado e ignorado pelo Git (SHA-256 com prefixo `d29c187d…`), difere do baseline
aprovado (`b66c24a3…`) somente em NVS: 7.920 bytes/138 sequências nos setores
`0xa000` (3.024 bytes), `0xb000` (3.944) e `0xe000` (952). As demais áreas,
inclusive firmware, `otadata`, PHY e slots OTA, coincidem. O dump bruto não foi
incluído neste branch.

Essa leitura confirma identidade e conteúdo de flash, mas não registrou
`boot_id` nem a versão que o firmware em execução reporta. Não houve build
ESP-IDF, reboot, injeção ou HIL. Portanto ela não comprova rejeição, trap,
aceitação de artefato nem recuperação funcional.

## Bloqueios concretos da prova

- O preflight read-only da placa foi possível. O canal serial de falhas e os
  mecanismos da #22 não foram executados; identidade da placa está confirmada,
  mas falta confirmar `boot_id`, firmware em execução e estado funcional pelo
  firmware antes de preparar o ensaio.
- O IDF 5.2.6 está disponível no CT107, mas não foi usado para preservar os
  serviços e artefatos compartilhados. `idf.py` não está no PATH desta worktree.
  Não há build desta revisão.
- O gerador de módulos exige WASI-SDK 27.0/clang 20.1.8 e `wamrc` 2.4.5
  verificados por recibo. Nesta sessão há clang do sistema 22.1.8 com alvo
  `wasm32`, mas ele não corresponde ao compilador fixado; `wamrc 2.4.5` foi
  encontrado apenas no diretório de toolchain preservado, fora do PATH. Não há
  runtime WAMR host verificado para observar exceções reais. O teste host de
  `module_manager` usa um shim que não gera trap real. Não foram gerados módulos
  Wasm/AOT de falha nem alterado o `module_manager.c`: sem esse runtime e os
  artefatos reproduzíveis, implementar recuperação agora dependeria de uma
  semântica de trap não verificada.
- Próximo passo seguro para WASM: preparar a toolchain pinned em staging
  isolado; gerar fixtures de OOB e divisão por zero para interpretador e AOT;
  executá-las em WAMR real e registrar `wasm_runtime_get_exception`; então
  integrar injeção opt-in no slot de controle inativo, emitir aceite antes da
  chamada, aposentar só o slot injetado, restaurar um controle bom conhecido e
  provar continuidade de telemetria em teste host real antes do HIL.
- Antes de HIL, preparar um **serviço HTTPS de fixture isolado**, com os nove
  conjuntos e CA/nome TLS correspondentes. As rotas `8443` e `8444` ativas não
  contêm essas fixtures e permanecem fora do escopo. Fixar as opções de endpoint
  no build opt-in; a serial nunca escolhe endereço nem caminho.
- Fazer o piloto #20 e congelar o timeout antes de atribuir limite funcional;
  `300 ms` não é ainda critério empírico aprovado.
- Os casos Wasm atualmente são rejeitados pelo dispatcher antes de
  `fault_accepted`; implementar e testar os traps B/C e suas invariantes antes de
  chamar a #22 de concluída ou iniciar as 70 injeções aceitas mais dois
  controles.

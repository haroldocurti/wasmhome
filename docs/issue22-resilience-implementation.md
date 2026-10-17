# Implementação defensável da issue #22

Status em 2026-10-17: **integração opt-in implementada e suíte host aprovada;
rebuild ESP-IDF desta revisão e HIL pendentes**. Este registro não fecha a issue
nem autoriza a campanha #23.

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
- Nos perfis modulares B/C (`module-interp`/`module-aot`), ENV-SIG/HASH/OLD
  passam pelo mesmo updater modular, validador COSE, ledger NVS e sink de slot
  modular; o cenário/artifact kind vem do perfil compilado. O seletor serial
  não escolhe cenário ou endpoint. ENV-HASH exige bytes escritos no slot
  inativo e invalidação confirmada; ENV-SIG/OLD exigem zero bytes e nenhum slot
  candidato.
- WASM-ZERO/OOB/DIV0 usam um módulo trap dedicado gerado de WAT por ferramentas
  fixadas. A seleção do módulo é compilada no firmware; a serial escolhe apenas
  um ID fechado. O worker de controle carrega o fixture no slot alternativo,
  persiste e lê de volta `fault_accepted` antes da chamada WAMR, verifica a
  exceção esperada, aposenta somente o fixture e executa health check do
  controle conhecido antes de emitir recuperação.
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

## Evidência automatizada desta revisão

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

Os 232 testes da suíte host passaram (5 ignorados). Também passaram
`tools/generate_service_modules.py --verify` e
`tools/generate_resilience_wasm.py --verify`. Os builds ESP-IDF existentes
foram feitos antes das correções finais do harness/ENV; serão refeitos a partir
do commit desta revisão antes de serem usados como evidência.

O rebuild local com ESP-IDF 5.2.6 de `module-interp` e `module-aot` para
ESP32-S3 está pendente para este commit. Nenhuma alteração foi gravada nem
executada na placa; compilação local não valida transporte HTTPS real ou
comportamento HIL.

## Estado por critério de aceitação

| Mecanismo | Código / teste host | Ainda necessário |
|---|---|---|
| ENV-SIG | Atualizador nativo e perfis modulares B/C usam validador COSE real; testes de parser/assinatura e suíte host passaram. | Rebuild dos perfis, fixture HTTPS isolada e observação física da rejeição no estágio esperado em A/B/C. |
| ENV-HASH | A/B/C escrevem somente no slot OTA/modular inativo; abort apaga e lê de volta a partição inteira; invariância do ativo tem testes host. | Rebuild dos perfis e comprovar na placa bytes gravados/invalidados e ativo/piso preservados em cada perfil. |
| ENV-OLD | A/B/C chegam ao ledger NVS real; estágio `envelope_sequence` mapeado; testes host cobrem piso e preservação de ativo/piso. | Rebuild dos perfis, semear ledger na bancada e observar rejeição física em A/B/C. |
| ENV em B/C | Pedido serial fechado e execução ENV-SIG/HASH/OLD estão ligados aos perfis `module-interp` e `module-aot`. | Rebuild e ensaios HIL B/C, incluindo todas as falhas e controles da matriz. |
| WASM-ZERO/OOB/DIV0 | Fixtures Wasm/AOT fixadas, compiladas no firmware por perfil; dispatcher, persistência antes da chamada, exceção WAMR, aposentadoria do slot e health check do controle estão integrados; cobertura host passou. | Rebuild e executar em ESP32-S3 em interp e AOT, observando trap, UDP e continuidade/recovery reais. |
| NAT-ABORT | Caminho de firmware e correlação após reboot implementados. | Builds e prova física de pânico, novo boot e recuperação recebida por UDP. |
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

## Pendências concretas da prova

- O preflight read-only da placa foi possível. O canal serial de falhas e os
  mecanismos da #22 não foram executados; identidade da placa está confirmada,
  mas falta confirmar `boot_id`, firmware em execução e estado funcional pelo
  firmware antes de preparar o ensaio.
- A integração e os builds locais foram feitos com toolchains preservadas fora
  do CT e da placa. O inventário do CT107 confirma IDF 5.2.6 disponível lá; seus
  serviços de campanha permaneceram intocados. A prova aqui não afirma que os
  processos ou a placa executaram esta revisão.
- A geração reproduzível dos fixtures Wasm/AOT e a compilação dos dois perfis
  passaram, mas a execução real do fixture dentro do WAMR na ESP32-S3 ainda
  precisa ser observada. Os testes host não substituem esse trap real.
- Antes de HIL, preparar um **serviço HTTPS de fixture isolado**, com os nove
  conjuntos e CA/nome TLS correspondentes. As rotas `8443` e `8444` ativas não
  contêm essas fixtures e permanecem fora do escopo. Fixar as opções de endpoint
  no build opt-in; a serial nunca escolhe endereço nem caminho.
- Fazer o piloto #20 e congelar o timeout antes de atribuir limite funcional;
  `300 ms` não é ainda critério empírico aprovado.
- Executar primeiro a prova HIL controlada em A/B/C, confirmar ambiente,
  `boot_id`, firmware, identidade da placa e estado inicial, e só então iniciar
  a matriz. Os builds locais não contam como tentativas aceitas nem como uma
  célula HIL; faltam o piloto #20, as 70 falhas aceitas, dois controles e a
  avaliação operacional para fechar a issue ou iniciar a campanha #23.

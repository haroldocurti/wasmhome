# Implementação defensável da issue #22

Status desta revisão: **planejamento e geração de fixtures validados no host;
parser serial C e integração nativa NAT-ABORT implementados; build ESP-IDF e
prova HIL ainda não executados**. Este registro não fecha a issue nem autoriza
as 72 células da campanha.

## O que está implementado

- O harness em tools/resilience_harness.py cria a agenda aprovada de 72 slots
  (70 falhas e dois controles), valida o formato fechado de pedido, lê capturas
  seriais brutas e o formato raw_base64 usado por update_campaign.py, e
  correlaciona eventos seriais e UDP por attempt_id e boot_id. Nas capturas
  serial compatíveis, associa host_monotonic_ns ao evento sem alterar o log de
  origem. Registros de pedido só semeiam uma tentativa quando indicam envio
  concluído e preservam os bytes efetivamente escritos. A falta de
  fault_accepted permanece unconfirmed, sem inferir rejeição.
- O gerador em tools/generate_resilience_fixtures.py cria nove pares de
  fixtures ENV-SIG/HASH/OLD a partir de um pacote #15 verificado. As chaves
  privadas são somente entradas e não são copiadas para a saída. O índice marca
  o resultado como gerado no host e ainda não validado no dispositivo.
- O parser C em main/resilience_test_protocol.c aceita somente JSON sem
  escapes, com exatamente event, attempt_id UUID canônico e case_id pertencente
  à matriz. Campos duplicados, desconhecidos, URLs, caminhos e valores fora da
  matriz são descartados.
- CONFIG_WASMHOME_RESILIENCE_TEST_MODE é desligado por padrão e exige
  serviços nativos e OTA assinado no cenário native. O firmware só aceita
  NAT-ABORT; os demais casos válidos da matriz recebem
  fault_request_rejected com case_not_supported_by_this_firmware.
- Para NAT-ABORT, o firmware persiste attempt_id, caso e boot_id de origem em
  NVS, faz commit e leitura de volta, e só então emite fault_accepted. Ele
  aguarda uma requisição de controle UDP cujo attempt_id corresponda ao ensaio;
  depois de enviar a resposta v1, imprime o marcador serial com UUID/boot/tempo
  e chama esp_system_abort("resilience:controlled_abort").
- Após reiniciar, o registro em NVS permite emitir boot_observed com
  esp_reset_reason(), versão ativa e piso aceito, seguido de
  control_recovery_started. Telemetrias de ensaio levam o mesmo attempt_id no
  campo existente do cabeçalho UDP v1. O firmware emite
  control_recovery_completed quando sua pilha UDP aceitou o envio de uma
  telemetria válida e o envio de um controle correto. Os campos
  firmware_udp_tx e control_udp_tx deixam explícito que isso comprova envio
  local; o observador ainda precisa confirmar a recepção UDP e calcular a
  lacuna.

## O que permanece pendente

| Mecanismo | Estado verificável nesta revisão |
|---|---|
| ENV-SIG | Fixture host gerada. Sem entrada de ensaio fixa para chamar o validador real. parse_envelope() é privado em main/signed_update.c; a API pública wh_signed_update_download() usa apenas os caminhos fixos v1/v2 e o pedido UDP nativo aplica seu próprio gate antes do download. |
| ENV-HASH | Fixture host gerada. Falta conectar a fixture ao sink real e comprovar gravação/invalidação do slot inativo, preservando o slot ativo e o piso. |
| ENV-OLD | Fixture corretamente assinada gerada no host. Ainda não percorre parse_envelope() e o piso NVS real no estágio envelope_sequence; o gate de sequência de wh_native_update_admit() é anterior ao download e não substitui esse teste. |
| WASM-ZERO/OOB/DIV0 | Agenda/correlação host implementadas. tools/generate_service_modules.py gera apenas os exports normais wh_abi_version, wh_telemetry_tick e wh_control_request; module_manager.c não tem injetor de trap. Exige uma fixture Wasm/AOT de ensaio e um caminho de recuperação que preserve telemetria e descarte somente o slot de controle. |
| NAT-ABORT | Caminho de firmware implementado, sem build nesta revisão e sem prova na placa. As observações de envio locais não demonstram recepção nem timeout funcional. |
| Timeout funcional | CONFIG_WASMHOME_RESILIENCE_FUNCTIONAL_TIMEOUT_MS começa em 300 ms, apenas como candidato. A #20 não congelou o valor empiricamente; o firmware não trata esse candidato como um critério aprovado. |
| Recuperação operacional | O limite de 30 s continua sendo verificado pelo observador a partir dos UDP recebidos. Ainda não foi medido. |
| Contagem da campanha | O planner produz 70 falhas + 2 controles, mas não há tentativas aceitas nem resultados HIL nesta revisão. |

## Dependências exatas para fechar a prova

1. Revisar e compilar a configuração opt-in com ESP-IDF 5.2.6. O build desta
   revisão não foi executado; idf.py não está no PATH do worktree local.
   O CT107 possui o toolchain em
   /home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962/esp-idf,
   mas fica preservado para a prioridade #20 e nenhuma placa/serial foi aberta.
   A tentativa de ping ao IP histórico da placa não recebeu resposta; sua
   identidade, boot_id e firmware atual não foram confirmados.
2. Integrar um caminho de fixture restrito ao código de atualização. Ele deve
   chamar o validador e o sink existentes, sem expor URL/caminho/chave pelo
   serial. Para ENV-OLD, registrar SEQUENCE_NOT_NEWER no estágio
   envelope_sequence; para ENV-HASH, verificar invalidação física do slot
   inativo e invariância do ativo/piso.
3. Gerar e validar os módulos Wasm/AOT de resiliência para a toolchain fixada.
   A integração deve chamar exports explícitos através da instância de
   controle e conter a exceção antes de executar qualquer destruição de
   runtime; a telemetria deve continuar.
4. Depois da prioridade #20 e da confirmação de identidade/estado da placa,
   restaurar/registrar a configuração de referência e executar apenas o
   ensaio preliminar autorizado. O endereço serial presente ou o ping sem
   resposta não identificam a placa nem comprovam o firmware atual.

## Validações host executadas

- python -m unittest tests.test_resilience_harness -v: 16 testes passaram.
- python -m unittest tests.test_resilience_fixtures -v: 3 testes passaram.
- python -m unittest tests.test_resilience_protocol_c -v: 1 teste compilou o
  parser C com -Wall -Wextra -Werror e passou, incluindo rejeição de
  duplicatas, campo extra/URL, UUID não canônico e caso fora da matriz.

Esses testes não compilam o firmware ESP-IDF e não são prova de comportamento
na placa.

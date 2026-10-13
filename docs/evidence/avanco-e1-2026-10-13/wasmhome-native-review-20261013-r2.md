# Revisão independente — serviços nativos #11 e instrumento E1 #9

Data: 13/10/2026. Revisão somente leitura do snapshot compartilhado em `/home/haroldocurti/Documents/wasmhome`, base `HEAD=0ba2faab16dc9671f89a663eea8e6d0d1f528997`, árvore-base `76cd12437c7bf7351880ee7ee03d969248ec5e51`. O worktree continha mudanças locais de P1/#9/#11 e documentação; nenhuma foi editada nesta revisão.

## Parecer

Os vetores implementados correspondem à decisão aprovada de histerese: estado inicial baixo, banda total de 1 °C, bordas inclusivas ±0,5 °C e retenção dentro da banda. A inicialização da plataforma coloca o GPIO baixo antes de instalar o handler; telemetria lê snapshot protegido do DS18B20, preserva o último valor válido/idade e publica a cada 100 ms; controle atualiza e consulta o loopback; codec rejeita pacotes inválidos. As guardas Kconfig/CMake/C recusam combinações incompatíveis. O runner E1 libera ambiente, instância, módulo e buffer em ordem inversa, inclusive nos caminhos de falha cobertos pelo harness.

A bateria host passou, mas não recomendo aceitar ainda o validador de dados #9 nem o comportamento de replay de `UPDATE_TRIGGER` #11. Há dois defeitos de consistência reproduzíveis abaixo. O código do adaptador FreeRTOS/ESP-IDF está fora do harness C host e ainda precisa de build ESP-IDF v1/v2 no snapshot final publicado; não ocorreu build de serviços nem sessão em placa nesta revisão.

## Achados que precisam de correção/revisão

### [P1] O validador E1 aceita sessão fora de ordem e leitura de memória impossível

Em `tools/validate_e1_device.py`, a função `_validate_memory_events` exige que `after_release` seja o último evento de memória, mas `validate_session` só exige que a linha final `E1 status=ok` venha depois da última linha CSV. Assim uma captura pode declarar o experimento concluído antes da liberação do runtime e ainda passar. Também só se exige que `heap_min` seja inteiro não negativo; o validador não o relaciona aos valores de heap livre/tamanho já capturados.

Reproduções em fixtures temporárias, com o hash do log e o manifest atualizados para refletir cada edição:

- Troquei de posição a linha final E1 e o evento `after_release`; resultado: `session_logs_and_manifest_consistent`.
- Mudei `heap_min` das 30 linhas de 900 para `999999`, mantendo `internal.total_bytes=2048` e `free_bytes=1000`; resultado: `session_logs_and_manifest_consistent`.

As provas estão em `wasmhome-native-review-r2/validator-consistency-repro.txt`. Sugestão: exigir `final_line > after_release_line`, validar `heap_min` contra todas as amostras de heap livre relacionadas e validar valores CSV contra capacidade/tamanhos aplicáveis. Acrescentar negativos para ambas as mutações. Isso não transforma o parser em atestado de hardware (ele corretamente declara escopo offline), mas fecha contradições internas do próprio conjunto.

### [P1] Replay conflitante de update deixa de ser detectado depois de 16 IDs

`main/native_services.h` limita a cache de update a 16 registros; `wh_native_update_reject` substitui a entrada mais antiga sem tombstone nem regra para sequências antigas. O contrato em `docs/protocolo-observacao.md` exige `ATTEMPT_CONFLICT` para o mesmo `attempt_id` com outra sequência/conteúdo. Após 16 tentativas distintas, uma requisição com o primeiro ID mas sequência alterada retorna `WH_NATIVE_UPDATE_NEW_REJECTION` e `BUSY`, não `ATTEMPT_CONFLICT`.

Confirmei com um harness C temporário compilado contra o `main/native_services.c` atual (`-std=c11 -Wall -Wextra -Werror -pedantic`): `first=1 aged_conflicting_same_attempt=1 reason=6 expected_conflict=3`. A saída e o fonte do harness estão em `wasmhome-native-review-r2/update-replay-eviction.{txt,c}`. Este firmware não tem OTA e, portanto, o caminho atual não causa instalação dupla; ainda assim a resposta viola o contrato de replay e produz motivo incorreto. Recomendo preservar identidade/tombstone para o escopo documentado da sessão ou fechar formalmente uma janela limitada no contrato e no observador, com negativos que cruzem o limite da cache. Também esclarecer/testar a aceitação de `UPDATE_TRIGGER` com sequência do cabeçalho regressiva ou repetida e `attempt_id` novo; hoje esse contador é somente armazenado para comparação por ID, não é validado como high-water.

## Ambiguidade de protocolo a resolver

Uma duplicata exata de `UPDATE_TRIGGER` reaproveita o motivo/body, mas `send_update_rejection()` chama `wh_observation_send()` novamente e recebe uma nova sequência TX. O observador atual a classifica como `duplicate_acceptance`, sem iniciar outra tentativa, então não vi efeito incorreto no fluxo atual. O texto do protocolo diz que a resposta é “repetida” e que repetição de datagrama conserva a sequência; deve ficar explícito se é retransmissão do mesmo datagrama de resposta (mesmo sequence/bytes) ou nova mensagem de resposta com sequence crescente. A nota de implementação em `/tmp/wasmhome-native-services-20261013.md` já registrou essa dúvida.

## Leitura dos contratos e áreas verificadas

- A decisão em `docs/plano-detalhado-coleta-analise.md` é implementada pelos vetores de fronteira v1/v2 e pelo vetor distintivo 23/32/27 °C em `tests/test_native_services.py`.
- Controle deduplica `sample_id` e corpo; IDs removidos da cache são bloqueados por `highest_sample_id`. GPIO é aplicado somente para pedido novo, resultado repetido preserva o valor/status e não chama o driver novamente. `sample_id` de controle é distinto da sequência do envelope.
- Telemetria usa relógio monotônico único (`esp_timer_get_time`) para aquisição e cálculo de idade, rejeita timestamp futuro, satura em 65534 e usa sentinela/65535 quando ainda não houve amostra válida. Os ramos sem/ com PSRAM são explícitos; picos ficam `null/not_instrumented`, sem chamar o mínimo global desde o boot de pico de fase.
- `main/main.c` monta cada JSON de memória num buffer delimitado e o emite com uma chamada `printf` por linha. O teste confirma uma chamada por evento e o parser rejeita JSON de memória quebrado por linha intercalada; isso é teste de emissão por chamada, não prova de UART atômica byte a byte. O relatório da implementação também explicita esse limite.
- Kconfig exclui diagnósticos/benchmark; CMake e `#error` restringem serviços ao cenário nativo. Fonte `native_services.c` só entra quando a opção está ligada. Nenhum teste host compila o adaptador ESP-IDF sob os headers/FreeRTOS reais; o harness host define `WASMHOME_NATIVE_SERVICES_HOST` e testa regra/codec. O build integrado real segue pendente.
- Pequena limpeza de erro: se `xTaskCreate(telemetry_task)` falhar, a função remove o handler, mas mantém `service_lock` alocado/não nulo, de modo que nova chamada retorna `ESP_ERR_INVALID_STATE`. O chamador atual usa `ESP_ERROR_CHECK` e aborta, então não bloqueia o fluxo de boot presente; liberar o mutex e zerar o ponteiro antes de retornar deixa a API recuperável.

## Verificação independente

Comando da suíte completa (executada sem escrita de `__pycache__`):

```sh
PATH=/home/haroldocurti/wasmhome-toolchain-e1-2.4.5-xtensa18-20261012/tools/cmake/bin:/home/haroldocurti/.local/toolchains/wasmhome-pwsh-7.6.6-20261013:/usr/bin:/bin \
WASMHOME_WIRE_LIBRARY=/tmp/wasmhome-p1-20261013-r2/libobservation_wire.so \
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest discover -s tests -v
```

Resultado: **76 testes, 75 aprovados e 1 skip**, exit code 0; skip é somente a negativa de junction Windows. A execução usou PowerShell 7.6.6 real, CMake 4.4.4, Ninja 1.13.2 e biblioteca compilada do codec C real SHA-256 `667985b4aee880ea755bc4abcd263646c44bcf0b6b4765ba935889b0ea6893b9`. Assim os testes de frontend PowerShell, gate CMake, runner compilado Linux, serviços nativos e negativos do validador rodaram; não se afirma prova de MSVC/junctions Windows.

`git diff --check` terminou sem diagnóstico. Log completo e versões: `wasmhome-native-review-r2/unittest-full.log` e `environment-and-tests.txt`.

## Identidade dos arquivos e do diff revistos

Diff consolidado de modificações tracked e novas contra o HEAD-base: `wasmhome-native-review-r2/working-tree-final.diff`, SHA-256 `39fa334d0a7f3000145df1c5c6c52a2cc74ae90f452d15e0389a67667c179947` (93.952 bytes). Estado/hashes completos: `wasmhome-native-review-r2/reviewed-files-and-status.txt`.

| Arquivo | SHA-256 |
|---|---|
| `main/native_services.c` | `0bcdf8522281014cbb596de9c6d6c64d675cc92e5a6a36c439220bf9d3857d97` |
| `main/native_services.h` | `9f9c25e7621678f75d0b7f61d54909df95f2ec35fb56b01e0c6f979f3ec6335b` |
| `main/main.c` | `bbdb1e79abb5eec3bd39af9382bdd186654cd87d420145632340d66a189024fd` |
| `main/CMakeLists.txt` | `bb9de99b44b037878e328a1a408fa5bdf73ac07d7a7c9f6d9362f1bc16a818d2` |
| `main/Kconfig.projbuild` | `fbf0e50664f39b3dfb7db57ea61cb2b452174e2038c9d9c0ba0db34d7140feb9` |
| `tests/test_native_services.py` | `942949874170a774f322a1ae08288f3c48f3f5ad2e76139e749d96d006607e6b` |
| `tests/test_e1_runner.py` | `ad35c03423850fbf3d8828d6f9ee4314978cf7e2ce2a2d0a188a7bb98d05e369` |
| `tools/validate_e1_device.py` | `c72b604e44531de86b43bc97b43686918fa14a5862810e7995f4cb215718ab4c` |
| `tests/test_validate_e1_device.py` | `f37aa1e9733140dbf04c8209c5abc95324318f30d2de1b0f64a4ea4b39a0ce83` |
| `docs/plano-detalhado-coleta-analise.md` | `9bc982d53ee7a7e69eeec6642d317bdbeae1ab8c41070de7cf80eaebfd248a00` |
| `docs/plano-proximos-passos-agentes.md` | `e1c80693e6ce82434d458919cd631335abafc89acf1490a1b94b1ba1b2163eac` |

Não fiz build ESP-IDF de serviços nativos, flash ou acesso à placa: o código ainda precisa receber as correções/revisão dos achados e, depois, ser congelado/publicado em uma revisão limpa antes de preparar snapshots v1/v2.

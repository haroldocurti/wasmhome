# Implementação defensável da issue #22

Para executar HIL físico sem root, consulte o
[orquestrador de usuário](issue22-hil-orchestrator.md). Ele substitui os
passos operacionais manuais de `systemd`, `root@Proxmox` e runner isolado
descritos nas revisões anteriores deste documento.

Status em 2026-10-18: **integração opt-in implementada, suíte host aprovada,
builds locais `module-interp`/`module-aot` aprovados e timeout funcional de
300 ms congelado após a conclusão do piloto #20**. A campanha preliminar da #22
foi encerrada após uma falha de configuração do coletor antes da injeção: uma
única linha foi reservada, sem `fault_request` ou pedido HTTPS de fixture. A
baseline integral foi restaurada e verificada. Os critérios de prova física da
#22 permanecem pendentes; veja o [relato sanitizado](evidence/issue22-hil-2026-10-18-preflight.md).

A issue #22 requer uma prova preliminar de 16 tentativas físicas. A matriz
completa de 70 falhas + 2 controles pertence à issue #23 e não é critério de
fechamento da #22.

## Implementado no código

- `tools/resilience_harness.py` gera a matriz completa da #23 (70 falhas e dois
  controles), valida pedidos fechados, correlaciona JSONL serial e observações UDP por
  `attempt_id`/`boot_id`, e preserva `unconfirmed` quando falta
  `fault_accepted`. Timeout ou silêncio não viram rejeição.
- `tools/resilience_hil_runner.py` executa uma linha explicitamente armada,
  grava serial bruta/UDP com timestamps host monotônicos, e mantém transporte
  HTTP em evidência separada. O runner e os testes novos foram validados sem
  abrir serial, gravar flash ou iniciar o servidor; isso não é uma tentativa HIL.
- `tools/generate_resilience_fixtures.py` gera nove conjuntos ENV-SIG/HASH/OLD
  a partir de artefato #15 verificado. A chave privada fica fora da saída; as
  fixtures não foram validadas no dispositivo. A identidade da fixture #22 é
  obrigatória e separada: o gerador exige a chave pública correspondente à
  chave privada isolada e um campaign ID distinto do pacote fonte #15. A
  verificação criptográfica do pacote fonte continua usando sua chave e seu
  campaign ID próprios.
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

Nesta revisão, a suíte host executou 237 testes: 5 foram ignorados e 231
passaram. Um teste falhou somente porque o executável PowerShell (`pwsh`) não
está disponível no ambiente (`test_powershell_frontend_requires_origin_proof_before_writes`).
Os sete testes específicos de geração/validação de fixtures passaram após as
mudanças, incluindo a recusa explícita das portas de campanha `8443`/`8444`
pelo CLI e a aceitação da porta temporária `18445`. A evidência sanitizada está
em [`docs/evidence/issue22-fixture-preflight-2026-10-17.json`](evidence/issue22-fixture-preflight-2026-10-17.json)
e as 18 respostas TLS verificadas individualmente em
[`docs/evidence/issue22-fixture-tls-smoke-2026-10-17.jsonl`](evidence/issue22-fixture-tls-smoke-2026-10-17.jsonl).
Também passaram `tools/generate_service_modules.py --verify` e
`tools/generate_resilience_wasm.py --verify` após gerar os artefatos a partir do
checkout limpo desta revisão. O manifesto dos módulos registra o commit, a
árvore e os hashes dos geradores; ambos os manifestos registram hashes dos
artefatos e toolchains usados.

Os builds locais ESP-IDF 5.2.6 para ESP32-S3 passaram com módulo de serviços,
atualização assinada e modo de ensaio opt-in. Estes builds usam explicitamente
o campaign `issues16-18-bench-20261016-observer13`, a chave pública dedicada
`e2b4d52e6f7f76af469ef90aa9d6f099c92e3da0d3fd46efec083d67d9137afd`, CA
`757b99b20a567a27041be3f204831b7aa2f6d0192504e7d13fd6261267f1a942` e endpoint
de fixtures `192.168.0.138:8445`. O perfil nativo foi compilado no commit
`42f34de`; os perfis modulares foram compilados a partir do checkout
`c8f1dd07c8bdb88d2b0b4fa919c8b6ced42ce6c6`, exigido pelo manifesto de
proveniência dos módulos. Os três perfis são opt-in e não foram gravados nem
executados na placa. Estes são hashes dos builds locais desta configuração:

| Perfil | Tamanho | SHA-256 do firmware |
|---|---:|---|
| `native` | 915.808 B | `e36cdfa37a875996db44f45393342d643c1fc55f282b49379e4498365af3747c` |
| `module-interp` | 1.022.320 B | `a4019449255b4ad228e3aca53e2f4198be8f64f61f68835b6a702d2d3fca5548` |
| `module-aot` | 1.010.032 B | `18767cf4f045ad24d1a7a3118bb6baf63e406f259dacd26183031f574cb57250` |

O hash local nativo `e36cdf…` identifica um build v1 da configuração #22; ele
não é a identidade do release OTA v2. O preflight e o runner usam separadamente
o protocolo nativo `control_version=1` e o SHA-256 do artefato v2 lido do ledger.
Se o release v2 assinado com os hooks finais da #22 mudar, seu hash e tamanho
precisam ser revisados e registrados antes do HIL.

O artefato Wasm e o manifesto de serviços registram WAMR port
`2.4.5-wasmhome.3`, `wamrc 2.4.5` SHA-256
`98ab5acb7bc451cc5bf9d28940d4e252e8c00c7dbc5b0fe6d097901818fe7824`, e WASI
SDK 27.0 SHA-256 de pacote
`b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2`. O fixture
trap usa WABT `1.0.36 (git~1.0.37)` com o SHA fixado no manifesto.

O vínculo entre esse `wamrc` e o runtime WAMR `.3` está registrado em
[`docs/evidence/issue22-wamrc-build-provenance.json`](evidence/issue22-wamrc-build-provenance.json).
O registro público conserva o SHA-256 do recibo bruto (`cf3af320…44ad0b`), o
digest canônico do JSON (`3d4ac129…c5d0d1`) e a identidade do runtime; paths
locais foram substituídos por aliases e o conteúdo dos snapshots grandes foi
omitido, mantendo seus hashes e tamanhos. O próprio registro tem SHA-256
`d894e72de14ef9ce9622d379e64ce586faca1f11b0a326b4ad1eb350d774fa6d`.
Os manifestos de serviço e fixture carregam os três digests e a identidade do
runtime, e os verificadores rejeitam divergências. Esse recibo local não é
assinado nem substitui uma recompilação independente.

Os builds foram feitos em diretórios isolados e não foram gravados nem
executados na placa. Compilação local não valida transporte HTTPS real ou
comportamento HIL.

## Regenerar a matriz de fixtures isolada

Execute em um checkout confiável, com variáveis apontando para entradas e
saídas locais privadas. Não coloque valores de caminho, conteúdo de chave ou
segredos na documentação ou no log compartilhado:

```sh
python3 tools/generate_resilience_fixtures.py \
  --package "$ISSUE15_VERIFIED_PACKAGE" \
  --public-key "$ISSUE15_PACKAGE_PUBLIC_KEY" \
  --expected-campaign-id issue15-bench-20261015 \
  --trusted-private-key "$ISSUE22_FIXTURE_PRIVATE_KEY" \
  --trusted-public-key "$ISSUE22_FIXTURE_PUBLIC_KEY" \
  --untrusted-private-key "$ISSUE22_UNTRUSTED_PRIVATE_KEY" \
  --untrusted-public-key "$ISSUE22_UNTRUSTED_PUBLIC_KEY" \
  --fixture-campaign-id issues16-18-bench-20261016-observer13 \
  --expected-aot-toolchain-id wamr-2.4.5-wasmhome.3 \
  --old-sequence 1 --next-sequence 3 --module-next-sequence 3 \
  --output "$ISSUE22_FIXTURE_OUTPUT"
```

O índice de saída registra as identidades de campaign fonte e fixture, ambos os
fingerprints de chave pública e o plano de sequências. ENV-SIG/HASH usam
sequência 3 e ENV-OLD usa sequência 1. Antes de HIL, leia o ledger NVS real de
cada cenário: ENV-HASH precisa de piso exatamente 2 para que a sequência 3 seja
a próxima; ENV-OLD precisa de piso pelo menos 1 para que sequência 1 seja
antiga. A saída inclui o catálogo fechado e os 18 objetos servidos por HTTPS,
mais os arquivos públicos de verificação usados pelo preflight do servidor. As
fixtures não determinam o piso atual do dispositivo.

O servidor fechado está em `tools/resilience_fixture_https_server.py` e valida
índice, fingerprints, assinatura, hashes, caminhos e sequência antes de abrir
um socket. O CLI recusa explicitamente as portas existentes `8443` e `8444` e
permite a porta temporária `18445`. Um smoke TLS local verificou 18 GETs com
bytes e hashes iguais às fixtures; isso não inicia o endpoint CT nem substitui
a validação HIL na placa. Para HIL, o orquestrador de usuário serve uma
allowlist combinada (nove fixtures inválidas e três releases v2/sequence-2)
em `192.168.0.138:18445`, somente durante a execução, sem systemd e por SSH
estrito como `wasmhome`. Os builds atuais ainda apontam para `:8445` e serão
recusados até endpoint, certificado, manifesto e pacote estarem alinhados em
um rebuild. O CA público tem SHA-256
`757b99b20a567a27041be3f204831b7aa2f6d0192504e7d13fd6261267f1a942`, a chave
pública dedicada da fixture `e2b4d52e6f7f76af469ef90aa9d6f099c92e3da0d3fd46efec083d67d9137afd`,
e o índice publicado `bf36fb7c2a2a65616b0651dbb5cdf69af569d249b46d99a6d840ce372c41ab0f`.

## Executor HIL de uma tentativa

[`tools/resilience_hil_runner.py`](../tools/resilience_hil_runner.py) adiciona
um comando explícito de uma tentativa por execução. `run` exige uma linha do
plano completo, UUID de confirmação igual ao `attempt_id`, caminho serial
explícito, IPs IPv4 literais, diretório de saída novo e preflight JSON. Antes
de abrir serial ou UDP, valida campanha, perfil, boot ID, ledger e floors. A
primeira telemetria UDP também precisa apresentar o mesmo `boot_id` do
preflight; em divergência ou ausência de boot, não envia `fault_request`.
Antes de enviar, também exige pelo menos 30 controles e 30 telemetrias válidos
somente no boot do preflight. Depois do evento serial que encerra a execução da
falha, exige outros 30 de cada tipo dentro da janela pós-falha, restritos ao
mesmo boot em ENV/Wasm e ao novo boot de recuperação em `NAT-ABORT`; amostras de
boots diferentes nunca se somam. Se a amostra não for suficiente, a correlação fica marcada
como `insufficient_observation_coverage` e não produz classificação. Janelas
menores que 30 períodos de controle são recusadas antes de abrir portas.
Serial, datagramas, registros de envio e saídas são mantidos em arquivos
privados (`0700` no diretório e `0600` nos arquivos), com serial bruta em
Base64 e timestamp host `monotonic_ns`. Os bytes recebidos nunca vão para
stdout. O runner fecha as portas ao terminar; não inicia nem para o serviço
HTTPS. `termios` e as linhas DTR/RTS são restauradas no fechamento e em falha
de inicialização; quando o host não consegue ler o estado original das linhas
modem, o runner não as altera.

O diagnóstico offline `tools/resilience_nvs_preflight.py` aceita somente um
arquivo local com os 24 KiB da partição NVS e o perfil em ensaio. Ele usa o
parser ESP-IDF com decodificação de valores desligada, valida páginas/CRCs e
monta apenas os blobs permitidos: `wh_update/ledger`,
`wh_resilience/pending` e `wh_mod_resil/pending`. A saída não contém chaves ou
valores de outras namespaces, UUIDs, blobs ou dados Wi-Fi/configuração. O
arquivo JSON novo é criado com permissão `0600`; o coletor não abre serial nem
grava flash/NVS. `--boot-id` deve vir do evento de boot da sessão atual. O
schema v4 separa `control_version` (versão do protocolo de controle/telemetria)
de `release_artifact_sha256` (hash do artefato v2 registrado no ledger NVS).
Para native, o protocolo continua v1; o artefato OTA ativo é v2/seq2. O hash do
artefato vem do ledger e precisa corresponder ao release assinado conhecido. A
partição NVS não prova qual imagem está executando nem substitui a verificação
do slot.
Os campos `reserved` explícitos do ledger e dos dois registros de recuperação
precisam ser zero. O preflight não interpreta bytes de padding implícito do C.

O exemplo mostra o schema v4 e o novo release nativo v2 do perfil A registrado
para #22 (916688 bytes). Essa identidade de entrada não declara validação HIL:

```json
{
  "schema": "wasmhome-issue22-readonly-preflight-v4",
  "campaign_id": "issues16-18-bench-20261016-observer13",
  "profile": "native",
  "record_identity": {"scenario": 1, "component": "firmware"},
  "boot_id": "0123456789abcdef",
  "control_version": 1,
  "release_artifact_sha256": "224e0c01ae4843828a6faad010fc60f20d9795816fee1ca483f11412ff8c67cf",
  "native_state": {
    "ledger_present": true,
    "accepted_floor": 2,
    "ledger_active_target_version": 2,
    "ledger_active_sequence": 2,
    "active_slot": 131072,
    "active_payload_size": 916688,
    "active_payload_sha256": "224e0c01ae4843828a6faad010fc60f20d9795816fee1ca483f11412ff8c67cf",
    "active_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "pending": false
  },
  "module_state": null,
  "recovery_state": {"previous_attempt_present": true, "recovery_complete": true}
}
```

Para qualquer cenário, o runner exige o registro do campaign/profile correto,
sem update pendente, floor 2, target version 2, sequência 2, slot OTA/module
válido e hashes esperados de payload/manifest. `release_artifact_sha256` deve
coincidir com o hash ativo no ledger e com o release v2 assinado registrado nos
validadores `resilience_hil_runner` e `resilience_nvs_preflight`. A evidência
[`issue16-native-ota-2026-10-16.json`](evidence/issue16-native-ota-2026-10-16.json)
identifica um release anterior e não comprova a execução do novo perfil A.
O controle nativo continua usando `control_version=1`; essa versão de protocolo
não é comparada com target version/sequence do ledger. O dump NVS não recalcula
os bytes do slot nem prova qual imagem está executando.

Não é necessário existir tentativa de recuperação anterior: namespace `pending`
ausente representa o primeiro ensaio limpo. Se existir registro, ele precisa ser
válido e completo. No registro nativo, os dois bits de recuperação e um
`recovery_boot` não nulo indicam completude; `accepted_acknowledged` é validado
como booleano, mas o valor zero não bloqueia nova tentativa depois de uma
recuperação válida. No registro modular, são necessários `accepted=1`,
`slot_recovered=1`, os dois bits de serviço e `recovery_boot` não nulo;
`recovery_reported` é validado como booleano, mas não é condição de recuperação.
Registro incompleto no perfil selecionado ou no outro perfil, e o bit global de
update pendente, bloqueiam o ensaio.
Isso torna `ENV-OLD` um teste da rejeição da fixture seq. 1 contra o estado
ativo seq. 2. O baseline aprovado
`00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5` tem CRCs
de páginas NVS válidos, mas não contém o ledger da campanha; o preflight deve
reportar o ledger ausente e o runner recusará a saída. A evidência de OTA física
#16 comprova que o payload nativo v2 SHA-256
`8df016fe253d58eb0b9073037e38cf6568162a0964c03aa2aa384d9185f44137` foi
executado e reconciliado com floor 2. Uma captura NVS pós-OTA satisfaz os gates
de release somente se também contiver o registro do campaign/profile com target
version 2, sequência 2, slot/hash correspondentes e `pending=false`; a evidência
publicada não inclui um dump NVS sanitizado que confirme esses campos sozinha.
O provisionamento deve usar OTA assinada; não se deve escrever o ledger NVS
diretamente. O preflight não atesta sozinho o hash da imagem atualmente em
execução.

Exemplo de geração da saída nativa (a captura NVS é fornecida localmente por
um procedimento separado):

```sh
umask 077
python3 tools/resilience_nvs_preflight.py \
  --nvs-dump "$HIL_PRIVATE/nvs.bin" --profile native \
  --boot-id "$BOOT_ID" --output "$HIL_PRIVATE/preflight.json"
```

## Plano preliminar fechado da #22

A #22 usa exatamente 16 disparos e uma repetição por caso aplicável, na ordem
abaixo. ENV cobre os três perfis; os controles e traps Wasm cobrem `interp` e
`aot`; NAT-ABORT cobre somente `native`. A matriz completa de 72 linhas,
incluindo cinco repetições por célula, é exclusiva da #23.

| Linhas | Caso | Cenário(s) | Critério observado |
|---|---|---|---|
| 1–3 | ENV-SIG | native, interp, aot | `SIGNATURE_INVALID` em `envelope_signature`, sem gravação do payload; ativo, versão e sequência preservados; controle e telemetria recuperados. |
| 4–6 | ENV-HASH | native, interp, aot | `HASH_MISMATCH` em `payload_hash`; somente slot inativo escrito e depois invalidado; nenhum payload ativo; versão e sequência preservadas. |
| 7–9 | ENV-OLD | native, interp, aot | `SEQUENCE_NOT_NEWER` em `envelope_sequence` usando o validador real e piso NVS 2; slot, versão e sequência ativos preservados. |
| 10–11 | WASM-ZERO | interp, aot | chamada bem-sucedida de `probe_zero`, retorno `0x12345678`; controle positivo necessário para interpretar os traps. |
| 12–13 | WASM-OOB | interp, aot | chamada WAMR falha com exceção de acesso fora dos limites, sem reboot; telemetria contínua e controle recuperado em até 30 s. |
| 14–15 | WASM-DIV0 | interp, aot | chamada WAMR falha com exceção de divisão inteira por zero, sem reboot; telemetria contínua e controle recuperado em até 30 s. |
| 16 | NAT-ABORT | native | marcador e pânico controlados, novo `boot_id`, `ESP_RST_PANIC`, interrupção observada dos dois serviços e recuperação em até 30 s. |

Cada disparo só entra como tentativa aceita após `fault_accepted` correlacionado.
Classifique a linha com `tools/resilience_harness.py correlate`; não infira
rejeição pelo silêncio. Sem confirmação, preserve como `unconfirmed`. Registre
todos os UUIDs e resultados individuais. Para aprovar o mecanismo, a linha deve
ser `passed` após o timeout funcional estar congelado em #20; antes disso,
`candidate_passed` é apenas evidência candidata e não satisfaz o critério.

Use o orquestrador em [`docs/issue22-hil-orchestrator.md`](issue22-hil-orchestrator.md)
para executar a matriz de 16 linhas. Ele verifica o timeout congelado e a
baseline aceita na #20, compara a flash completa, administra o listener HTTPS
como processo de usuário no CT e restaura/verifica a baseline após cada linha.
O fluxo antigo com `systemctl`, `root@100.89.142.40`, `pct exec` e disparo
manual de cada linha foi removido. O runner abaixo continua sendo o executor
interno de uma linha por vez, sem classificar silêncio como rejeição nem inferir
`fault_accepted` a partir da janela HTTP.

## Estado por critério de aceitação

| Mecanismo | Código / teste host | Ainda necessário |
|---|---|---|
| ENV-SIG | Atualizador nativo e perfis modulares B/C usam validador COSE real; testes de parser/assinatura, suíte host, builds ESP-IDF e staging da fixture HTTPS passaram. | Após #20 liberar a bancada, observar rejeição física no estágio esperado em A/B/C. |
| ENV-HASH | A/B/C escrevem somente no slot OTA/modular inativo; abort apaga e lê de volta a partição inteira; invariância do ativo tem testes host; builds e staging HTTPS passaram. | Comprovar na placa bytes gravados/invalidados e ativo/piso preservados em cada perfil. |
| ENV-OLD | A/B/C chegam ao ledger NVS real; estágio `envelope_sequence` mapeado; testes host cobrem piso e preservação de ativo/piso; builds e staging HTTPS passaram. | Confirmar piso real maior que zero, depois observar rejeição física em A/B/C após #20 liberar a bancada. |
| ENV em B/C | Pedido serial fechado e execução ENV-SIG/HASH/OLD estão ligados aos perfis `module-interp` e `module-aot`; builds locais passaram. | Uma linha preliminar aprovada de cada caso nos dois perfis modulares. |
| WASM-ZERO/OOB/DIV0 | Fixtures Wasm/AOT fixadas, compiladas no firmware por perfil; dispatcher, persistência antes da chamada, exceção WAMR, aposentadoria do slot e health check do controle estão integrados; builds e cobertura host passaram. | Executar em ESP32-S3 em interp e AOT e observar trap, UDP e continuidade/recovery reais. |
| NAT-ABORT | Caminho de firmware e correlação após reboot implementados. | Builds e prova física de pânico, novo boot e recuperação recebida por UDP. |
| Timeout funcional | `300 ms` permanece valor candidato, reportado como não congelado. | #20 ainda não congelou empiricamente o timeout. |
| Recuperação operacional | Harness calcula recuperação a partir das observações UDP. | Confirmar recebimento UDP e recuperação dentro de 30 s no ensaio físico. |
| Contagem / escopo | O planner gera 16 linhas fechadas para #22 e mantém as 70 falhas + 2 controles da #23. | Executar e revisar as 16 linhas preliminares; reservar as 72 linhas para #23. |

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
- O serviço HTTPS combinado começa e termina junto de cada invocação do
  orquestrador, como processo de `wasmhome` em porta alta `18445`. Nenhuma unit
  de sistema, linger, `sudo` ou acesso `root@Proxmox` é necessário. Os listeners
  `8443`, `8444` e `8445` ficam intocados; porta ocupada bloqueia o ensaio.
- Retomar a prova preliminar da #22 com campanha, agenda e IDs novos, depois de
  confirmar o caminho de observação CT→máquina local. A linha encerrada não deve
  ser repetida nem retomada. Confirmar ambiente, `boot_id`, release, identidade
  da placa e estado inicial antes de cada execução. O runner exige
  `--plan-scope preliminary` para o arquivo de 16 linhas e mantém `full` como
  escopo padrão para as 72 linhas da #23. Só depois da revisão do resultado
  preliminar pode começar a matriz #23. Builds locais não contam como tentativas
  aceitas nem como células HIL.

## Bootloader compatibility gate (2026-10-17)

The accepted full-flash baseline has app rollback disabled in its bootloader.
The #22 coordinator now requires profile schema v2 with same-build app,
matching bootloader, sdkconfig and bootloader generated config, all
hashed and snapshotted. Native requires rollback enabled; interp/AOT may keep
it disabled when both configs and marker agree. The CMake V2 marker binds the
app to its sdkconfig.
Each row temporarily overlays the selected bootloader and verifies its bytes
before reset, then restores/verifies the entire accepted 16 MiB image in the
existing finally/recovery path. `ESP_OTA_IMG_NEW` remains rejected by the
signed-update reconciler. Earlier V1 app images and manifests must be rebuilt;
see `issue22-hil-orchestrator.md` for the private generation sequence.

The compile-time and V2 profile gates also require normal signed provisioning
and fixture HTTPS endpoints to be identical on port 18445, with the same IP,
TLS name and embedded CA. Both endpoints are embedded in the app marker and
recorded in the manifest, so a stale normal-update port 8445 is rejected even
when the fixture endpoint is already correct.

The bootstrap gate additionally parses the complete pinned IDF bootloader
image with bounded standard-library code, checking complete segments, checksum,
appended SHA-256, exact EOF, IRAM entrypoint and the IDF bootloader descriptor.
Generated-header parsing ignores block/line comments and rejects conditional
or ambiguous definitions, so an inactive rollback macro cannot pass the gate.
These checks require no serial API, privileged operation or CT service.

## Provisioning release identity and control policy

Native resilience uses control policy version 1 in both firmware releases v1
and v2. Its signed sequence-2 OTA must receive timely acceptance; the shared
observer consequently reports `accepted_without_v2`, with no policy-version-2
activation timestamp. The coordinator accepts that intermediate observation
only and still requires a new post-esptool boot with fresh telemetry/control,
then validates the real non-pending sequence-2 NVS ledger and registered active
payload hash/size/slot before any fault request. Interp/AOT instead update the
control module to version 2 and must report ordinary observed activation.
The #20 observer behavior is unchanged.

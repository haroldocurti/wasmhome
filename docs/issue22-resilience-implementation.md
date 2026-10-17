# Implementação defensável da issue #22

Status em 2026-10-17: **integração opt-in implementada, suíte host aprovada e
builds locais `module-interp`/`module-aot` aprovados; HIL ainda pendente**. Este
registro não fecha a issue nem autoriza a campanha #23.

## Implementado no código

- `tools/resilience_harness.py` planeja exatamente 70 falhas e dois controles,
  valida pedidos fechados, correlaciona JSONL serial e observações UDP por
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
executados na placa. Os hashes de firmware desta matriz são:

| Perfil | Tamanho | SHA-256 do firmware |
|---|---:|---|
| `native` | 915.808 B | `e36cdfa37a875996db44f45393342d643c1fc55f282b49379e4498365af3747c` |
| `module-interp` | 1.022.320 B | `a4019449255b4ad228e3aca53e2f4198be8f64f61f68835b6a702d2d3fca5548` |
| `module-aot` | 1.010.032 B | `18767cf4f045ad24d1a7a3118bb6baf63e406f259dacd26183031f574cb57250` |

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
um socket. O CLI também recusa `8443` e `8444` explicitamente, inclusive com
`--check`; a regressão cobre ambas as portas e permite o smoke temporário em
`18445`. A unit não secreta e revisável está versionada em
[`deploy/wasmhome-issue22-hil-fixtures-campaign16.service`](../deploy/wasmhome-issue22-hil-fixtures-campaign16.service).
No CT107 `wasmhome-bench`, ela usa somente `192.168.0.138:8445`, o campaign
acima e a raiz isolada de fixtures. `systemd-analyze verify` passou e o
preflight `--check`, executado como `wasmhome-http`, validou as nove duplas
manifesto/payload sem abrir socket. A unit permanece `static`/inativa; não há
listener em `8445`. As campanhas existentes `8443` e `8444` continuaram ativas
nos PIDs observados `266369` e `268013`. Um smoke TLS local usando o mesmo
servidor atualizado, em loopback `127.0.0.1:18445`, verificou 18 GETs, todos
HTTP 200 com bytes e hashes iguais às fixtures; isso não inicia o endpoint CT
em `8445` nem substitui a validação HIL na placa.

Depois da liberação da bancada #20 e de confirmar o piso NVS real do cenário,
controle o endpoint dedicado manualmente com `systemctl start
wasmhome-issue22-hil-fixtures-campaign16.service` e finalize com `systemctl stop
wasmhome-issue22-hil-fixtures-campaign16.service`. A URL base fica em
`https://192.168.0.138:8445/issues16-18-bench-20261016-observer13/resilience/`
e o nome TLS é `wasmhome-issue22.test`. O CA público tem SHA-256
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
no boot do preflight. Depois do evento serial que encerra a execução da falha,
exige outros 30 de cada tipo dentro da janela pós-falha; se a amostra não for suficiente, a correlação fica marcada
como `insufficient_observation_coverage` e não produz classificação. Janelas
menores que 30 períodos de controle são recusadas antes de abrir portas.
Serial, datagramas, registros de envio e saídas são mantidos em arquivos
privados (`0700` no diretório e `0600` nos arquivos), com serial bruta em
Base64 e timestamp host `monotonic_ns`. Os bytes recebidos nunca vão para
stdout. O runner fecha as portas ao terminar; não inicia nem para o serviço
HTTPS.

O preflight é pequeno e não aceita campos de Wi-Fi, MAC ou credenciais. Este
exemplo mostra o schema e valores ilustrativos; deve ser preenchido com leituras
reais, nunca copiado como evidência:

```json
{
  "schema": "wasmhome-issue22-readonly-preflight-v1",
  "campaign_id": "issues16-18-bench-20261016-observer13",
  "profile": "native",
  "boot_id": "0123456789abcdef",
  "firmware_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "native_state": {"ledger_present": true, "accepted_floor": 2, "pending": false},
  "module_state": null
}
```

Para `ENV-SIG` e `ENV-HASH`, o preflight nativo exige piso exatamente 2; para
`ENV-OLD`, exige piso pelo menos 1. `NAT-ABORT` exige estado não pendente, sem
um piso mínimo de update. Em `module-interp` e `module-aot`, todos os casos
exigem ledger modular presente, sem tentativa pendente, piso 2, módulo ativo
v2 e sequência ativa 2. O baseline aprovado `00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`
não contém esses ledgers; floor 0 não satisfaz estes gates. É necessário
provisionar e confirmar os estados exigidos depois da restauração da baseline
e antes do HIL. O runner confere o SHA informado contra o artefato registrado
para o perfil, mas esse SHA continua sendo uma atestação do operador: o
firmware atual não reporta o hash da imagem em execução. O módulo também não
emite hoje seu floor/versão ativo no evento de boot; a leitura modular precisa
vir de diagnóstico NVS somente leitura revisado, sem extrair ou publicar
campos alheios ao ledger.

Com a bancada #20 liberada, perfil correto gravado e preflight medido, mantenha
host e ESP32 na mesma LAN: `--device-ip` é o IPv4 atual da placa e `--bind-ip`
é o IPv4 da interface LAN do host que receberá UDP. Os três builds apontam ao
fixture CT107 em `192.168.0.138:8445`, TLS `wasmhome-issue22.test`. Gere o plano
offline e execute somente a linha desejada. O UUID passado em
`--arm-attempt-id` deve ser o dessa mesma linha:

```sh
python3 tools/resilience_harness.py plan --seed issue22-run-01 \
  --output "$HIL_PRIVATE/plan.jsonl"
python3 tools/resilience_hil_runner.py run \
  --plan "$HIL_PRIVATE/plan.jsonl" --slot "$SLOT" \
  --preflight "$HIL_PRIVATE/preflight.json" \
  --arm-attempt-id "$ATTEMPT_ID" \
  --serial-port /dev/ttyACM0 --baud 115200 \
  --device-ip "$ESP32_LAN_IP" --bind-ip "$HOST_LAN_IP" \
  --udp-port 3333 --device-port 3333 \
  --output "$HIL_PRIVATE/attempt-$SLOT"
```

`--timeout-ms 300` continua candidato, não limite de aceite congelado. O
runner mede UDP no relógio monotônico do host e não subtrai esse relógio do
`monotonic_us` do MCU. Só registra o envio do pedido; apenas um evento serial
`fault_accepted` pode provar que o firmware aceitou a injeção. Ausência desse
evento permanece `unconfirmed`, nunca vira rejeição por silêncio.

Se o log HTTPS do CT não estiver montado em leitura, capture seu prefixo antes
e depois da tentativa pela rota aprovada via Proxmox/Tailscale e `pct exec`
(sem SSH direto ao CT), mantendo os snapshots privados. O caminho de log da
unit é `/var/log/wasmhome/issue22-hil-20261017/campaign16-http.jsonl`:

```sh
umask 077
set -o noclobber
snapshot_http() {
  ssh -i "$PROXMOX_IDENTITY" -o IdentitiesOnly=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$PROXMOX_KNOWN_HOSTS" \
    root@100.89.142.40 \
    'pct exec 107 -- sh -c "test ! -f /var/log/wasmhome/issue22-hil-20261017/campaign16-http.jsonl || cat /var/log/wasmhome/issue22-hil-20261017/campaign16-http.jsonl"' \
    > "$1"
}
snapshot_http "$HIL_PRIVATE/http-before.jsonl"
# execute exatamente uma tentativa, sem requisições concorrentes ao fixture server
snapshot_http "$HIL_PRIVATE/http-after.jsonl"
python3 tools/resilience_hil_runner.py correlate-http \
  --run-dir "$HIL_PRIVATE/attempt-$SLOT" \
  --before "$HIL_PRIVATE/http-before.jsonl" \
  --after "$HIL_PRIVATE/http-after.jsonl"
```

O correlator exige que o segundo snapshot seja uma extensão byte a byte do
primeiro, extrai só as linhas acrescentadas e verifica as rotas HTTP esperadas.
Como o servidor não registra `attempt_id`, essa ligação é uma janela de log
para uma única tentativa operacional, não prova causal isolada. O relatório
HTTP fica separado e `fault_accepted_inferred` permanece `false`; somente a
serial e a correlação UDP classificam os eventos da tentativa. Não pode haver
requisições concorrentes à unit durante essa janela. Os snapshots guardam o IP
do peer e também devem permanecer no diretório privado da bancada.

## Estado por critério de aceitação

| Mecanismo | Código / teste host | Ainda necessário |
|---|---|---|
| ENV-SIG | Atualizador nativo e perfis modulares B/C usam validador COSE real; testes de parser/assinatura, suíte host, builds ESP-IDF e staging da fixture HTTPS passaram. | Após #20 liberar a bancada, observar rejeição física no estágio esperado em A/B/C. |
| ENV-HASH | A/B/C escrevem somente no slot OTA/modular inativo; abort apaga e lê de volta a partição inteira; invariância do ativo tem testes host; builds e staging HTTPS passaram. | Comprovar na placa bytes gravados/invalidados e ativo/piso preservados em cada perfil. |
| ENV-OLD | A/B/C chegam ao ledger NVS real; estágio `envelope_sequence` mapeado; testes host cobrem piso e preservação de ativo/piso; builds e staging HTTPS passaram. | Confirmar piso real maior que zero, depois observar rejeição física em A/B/C após #20 liberar a bancada. |
| ENV em B/C | Pedido serial fechado e execução ENV-SIG/HASH/OLD estão ligados aos perfis `module-interp` e `module-aot`; builds locais passaram. | Ensaios HIL B/C, incluindo todas as falhas e controles da matriz. |
| WASM-ZERO/OOB/DIV0 | Fixtures Wasm/AOT fixadas, compiladas no firmware por perfil; dispatcher, persistência antes da chamada, exceção WAMR, aposentadoria do slot e health check do controle estão integrados; builds e cobertura host passaram. | Executar em ESP32-S3 em interp e AOT e observar trap, UDP e continuidade/recovery reais. |
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
- O serviço HTTPS de fixtures isolado está preparado no CT107 com os nove
  conjuntos e CA/nome TLS correspondentes, e a unit permanece parada. Só iniciar
  `wasmhome-issue22-hil-fixtures-campaign16.service` depois que #20 liberar a
  bancada e os pisos NVS reais forem confirmados. As rotas `8443` e `8444`
  existentes permanecem fora do escopo; a serial nunca escolhe endereço nem
  caminho.
- Fazer o piloto #20 e congelar o timeout antes de atribuir limite funcional;
  `300 ms` não é ainda critério empírico aprovado.
- Executar primeiro a prova HIL controlada em A/B/C, confirmar ambiente,
  `boot_id`, firmware, identidade da placa e estado inicial, e só então iniciar
  a matriz. Os builds locais não contam como tentativas aceitas nem como uma
  célula HIL; faltam o piloto #20, as 70 falhas aceitas, dois controles e a
  avaliação operacional para fechar a issue ou iniciar a campanha #23.

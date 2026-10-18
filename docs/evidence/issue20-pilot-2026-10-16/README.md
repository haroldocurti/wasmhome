# Issue #20 — preparo e estado do piloto físico

## Resultado final atualizado da campanha r8 (18/10/2026)

A campanha r8 concluiu 18/18 slots, com seis atualizações aceitas, completas e
com instrumentação válida em cada cenário (native, interp e AOT). O primeiro
v2 correto chegou em até 16,870 s em todos os cenários; os aceites ficaram
abaixo do timeout congelado de 300 ms. A baseline de 16 MiB foi restaurada e
verificada após cada slot. Consulte o [relatório final](../issue20-pilot-2026-10-18/r8-hil-results.md)
e a [agenda r8](../issue20-pilot-2026-10-18/r8-pilot-schedule.json).

## Histórico: campanha diagnóstica r5 (17/10/2026)

A campanha concluiu 18/18 slots, com seis tentativas por cenário. O cenário
native teve 0/6 ativações V2; interp e AOT tiveram 6/6 cada, com medianas de
6,533 s e 7,401 s. Não houve abort instrumental, os mínimos de telemetria e
controle estáveis foram cumpridos, e a baseline foi restaurada/verificada após
cada slot. A rodada usou perfis legados e é diagnóstica; seus aceites não
qualificam o perfil nativo completo. Consulte os [resultados HIL r5](r5-hil-results-2026-10-17.md).

## Histórico: tentativa inicial anterior à campanha r5

O usuário aprovou a baseline física e autorizou os ensaios. A leitura integral
pré-escrita de 16 MiB coincidiu byte a byte com a baseline aceita. O fingerprint
da imagem permanece no recibo privado.

Uma campanha piloto anterior iniciou a qualificação de 1 dos 18 slots planejados:
`B0001-native`. A janela pré-disparo não recebeu
telemetria ou respostas de controle válidas nem observou um `boot_id`; o
observador registrou 601 eventos `awaiting_boot` e enviou zero pedidos de
controle. O registro de despacho ficou com `dispatch_status=not_sent` e
`attempt_id=null`; a sessão do observador tem seu próprio ID de execução.
Nenhum `UPDATE_TRIGGER` ou pedido HTTPS foi observado, e nenhum aceite foi
obtido. O progresso da meta mínima continua em 0 de 5 aceites completos por
cenário: A `0/5`, B `0/5`, C `0/5`.

Durante a preparação do slot, foram gravados somente o firmware de aplicação
e a tabela de partições; o log do esptool confirmou os hashes gravados. Ao
final, a imagem integral aceita foi restaurada no endereço zero e a verificação
automática do esptool terminou com `Hash of data verified.` O dump, logs brutos,
capturas seriais e registros completos da campanha permanecem em armazenamento
privado; esta atualização não acrescenta endereços de rede, MAC ou conteúdo
de flash.
O resumo sanitizado está em [tentativa HIL de 17/10](hil-attempt-2026-10-17.md).

Essa tentativa terminou antes do disparo e foi substituída pela campanha r5,
resumida no início deste arquivo. A placa respondeu a ping, mas isso não prova
o caminho UDP nem identifica por que faltaram datagramas naquela tentativa. A
causa não foi atribuída a firewall, roteamento ou firmware.

## Histórico da sessão de 16/10/2026

Naquela sessão, não houve disparos ou resultados físicos. A leitura integral
divergiu da imagem-base histórica então selecionada, deixando a gravação
bloqueada. Esse
estado foi substituído pela aprovação explícita da baseline atual em 17/10 e
pela comparação byte a byte registrada acima.

A revisão congelada em [protocolo-campanha-issue20-v1.0.md](../../protocolo-campanha-issue20-v1.0.md)
e a [agenda candidata](pilot-schedule.json) são documentos de preparação; não
devem ser interpretados como resultado de hardware. A calibração #19 examinada
contém uma atualização física aceita por cenário e não substitui as cinco
tentativas do piloto.

## Registro técnico da sessão de 16/10 (histórico)

### Checagem de acesso e estado da bancada

- `/dev/ttyACM0` existia para um adaptador USB QinHeng (`1A86:55D3`, driver
  `cdc_acm`). O identificador confirma o adaptador serial conhecido
  historicamente, mas não identifica o ESP32, não informa seu firmware/boot
  atual e não confirma sensor ou loopback.
- O acesso Tailscale ao host Proxmox passou com chave de host estrita. Leitura
  via `pct` confirmou CT107 `wasmhome-bench`, Debian 13, em execução, e o
  toolchain dedicado com ESP-IDF 5.2.6.
- Os servidores existentes de distribuição estavam ativos em TCP 8443 (#15) e
  8444 (#16–#18). Os diretórios de pacote e os logs existentes foram
  inspecionados somente para leitura; os IDs/pacotes e logs ativos não foram
  reutilizados, alterados ou reiniciados.
- O ping feito do CT e do host Proxmox ao endereço histórico do ESP32 na LAN
  não recebeu resposta; o neighbor ARP permaneceu `INCOMPLETE`. Esse endereço é
  apenas histórico e não confirma presença ou identidade da placa.
- Localmente, o adaptador USB serial conhecido aparece em `/dev/ttyACM0` com
  VID:PID `1a86:55d3`. Na checagem inicial, nenhum processo segurava o device.
  Depois, o operador usou esptool 4.12.0 somente com `chip_id` e `flash_id`:
  confirmou ESP32-S3 QFN56 rev. 0.2 e flash de 16 MiB. O identificador de rede
  permanece no recibo privado. Esses comandos acionaram hard reset; não houve
  gravação.
- O operador leu a flash integralmente, sem gravar, em
  `build-hardware/issue20-live-preflight-20261016.bin` (arquivo local ignorado,
  não incluído no repositório): 16 MiB. A comparação somente de leitura
  identificou uma diferença em armazenamento NVS; o conteúdo e os detalhes
  permanecem privados, e a causa não foi determinada. Consulte a
  [auditoria de baseline](backup-audit.md).
- Os artefatos e capturas integrais de #16–#19 ficam em armazenamento privado.
  O repositório contém resumos/hashes sanitizados, não credenciais ou dumps de
  flash. Os pacotes ativos continuam presos aos campaign IDs de #15/#16–#18 e
  não podem ser atribuídos ao piloto #20.

O parágrafo acima registra o bloqueio observado em 16/10. Em 17/10 a baseline
foi aprovada; uma tentativa inicial terminou antes do disparo por falta de UDP,
e a campanha r5 posterior concluiu os 18 slots. Os resultados atuais estão no
início deste arquivo. `boot_id`, firmware/boot ativos, sensor, GPIO5→GPIO6 e
janela viva qualificada não foram confirmados na tentativa inicial.

### Trabalho de preparação concluído antes do HIL

- A proveniência da imagem-base foi auditada somente por leitura. Capturas de
  sessões distintas não foram tratadas como equivalentes; os fingerprints e
  recibos completos permanecem privados. A cadeia e seus limites estão na
  [auditoria da imagem-base](backup-audit.md).
- O preflight vivo identificou uma ESP32-S3 QFN56 rev. 0.2 com flash de 16 MiB
  e produziu uma leitura integral. A comparação revelou uma diferença em
  armazenamento NVS; conteúdo e detalhes permanecem privados, e a causa não foi
  determinada. Em 17/10 o usuário aprovou a captura integral como nova baseline.
  A campanha r5 posterior e seus resultados estão registrados no início deste
  arquivo.
- Revisados `CONTEXT.md`, checklist, plano de execução, plano detalhado,
  protocolo UDP e resultados/calibração das issues #16–#19.
- Congelados limites, janelas, preparação, classificação de resultados e
  critérios de invalidação antes de qualquer dado de piloto nesta sessão.
- Mantida a campanha normal em `N=30` blocos/90 slots, com balanceamento
  exato das seis ordens. A justificativa é exploratória e operacional; não se
  afirma poder estatístico.
- O runner do observador foi alinhado ao protocolo: em `issue20-v1.0` ele exige
  60 s antes do disparo, valida 30 respostas corretas e 30 telemetrias válidas
  sob um único boot, fecha a medição 30 s após o primeiro v2 correto ou 30 s
  após o prazo de 60 s, classifica recuperação tardia e conserva a drenagem de
  respostas pendentes. Sem qualificação prévia, não envia `UPDATE_TRIGGER`.
- Preparada uma agenda piloto inicial de seis blocos, uma ocorrência de cada
  ordem, sem registros de tentativas. A agenda contém 18 slots únicos, passou
  pelo validador existente e tem SHA-256
  `37d823c0c076ac8b119aa25ed80447f80a3cbb77f0e7396a78d892035742f692`.
  Após cada grupo de seis blocos, se algum cenário ainda tiver menos de cinco
  atualizações aceitas completas, a próxima extensão deve ser um grupo inteiro
  com ID e agenda próprios antes de executá-lo.
- A suíte de planejamento/análise do campaign runner e do observador passou
  após incluir verificações do prazo, janela pós-sucesso e qualificação
  pré-disparo. `git diff --check` também passou. Essas verificações são locais e
  não representam teste físico.

## Estado da campanha r5 (histórico)

A campanha r5 foi diagnóstica e usou perfis legados; seus resultados não
qualificam o perfil nativo completo nem contam para os aceites da issue. A
próxima rodada de aceitação deve usar o perfil schema 2, com bootloader,
configuração gerada e app do mesmo build, artefatos assinados e restauração
verificada após cada slot. Não atribuir à #20 os dados singulares de #16–#18
nem as chamadas de microbenchmark da #19.

## Estado atual

A campanha r8 acima usou perfis compatíveis e foi adjudicada como concluída:
6/6 atualizações completas por cenário, 18/18 slots válidos e restaurações
integrais verificadas. O protocolo `issue20-v1.0` permanece congelado; consulte
o [relatório final](../issue20-pilot-2026-10-18/r8-hil-results.md). A etapa
seguinte é a campanha definitiva N=30, separada deste piloto.

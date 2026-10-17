# Issue #20 — preparo e estado do piloto físico

## Resultado da sessão de 17/10/2026

O usuário aprovou a baseline física e autorizou os ensaios. A leitura integral
pré-escrita de 16 MiB coincidiu byte a byte com a baseline aceita, SHA-256
`00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`.

A campanha `164c919d-d1d9-401b-9451-84c39ed930b9` iniciou a qualificação de
1 dos 18 slots planejados: `B0001-native`. A janela pré-disparo não recebeu
telemetria ou respostas de controle válidas nem observou um `boot_id`; o
observador registrou 601 amostras `awaiting_boot` e enviou zero pedidos de
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

O bloqueio atual é a ausência de telemetria/controle UDP recebidos pelo
observador durante a janela pré-disparo. A placa respondeu a ping, mas isso não
prova o caminho UDP nem explica a ausência dos datagramas. Não se atribui a
causa a firewall, roteamento ou firmware sem evidência adicional. Não continuar
os slots desta campanha; após corrigir e confirmar a recepção UDP, criar uma
campanha nova com identificador próprio.

## Histórico da sessão de 16/10/2026

Naquela sessão, não houve disparos ou resultados físicos. A leitura integral
divergiu da imagem-base histórica `b66…`, deixando a gravação bloqueada. Esse
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
  não incluído no repositório): 16.777.216 bytes, SHA-256
  `d29c187d0c691c5b874d1ad3a1cda8632144a70e8613a9c9148e76f8c3aa8c78`.
  A comparação local byte a byte, somente para leitura e sem copiar/publicar
  os dumps, contra a baseline privada `b66…` encontrou 7.920 bytes diferentes
  em 138 sequências contíguas, todos na NVS. Por setor: `0xa000`: 3.024 bytes;
  `0xb000`: 3.944; `0xe000`: 952. Não houve diferenças em bootloader, tabela
  de partições, setores NVS `0x9000`, `0xc000` e `0xd000`, `otadata`, PHY-init,
  slots OTA nem áreas sem região nomeada no recibo. O hash integral não
  coincide com a imagem-base então selecionada `b66…`; a causa da diferença ainda não
  está determinada. A inspeção semântica offline isolou uma mudança lógica em
  um registro de calibração PHY; os 31 registros Wi-Fi permaneceram iguais,
  sem itens adicionados/removidos. Há rotação de página NVS. Não foi encontrado
  o ledger antirretorno persistente da aplicação nos dois snapshots; o estado
  eFuse e a causa da atualização da calibração não foram verificados.
  A hipótese de outra WLAN/ausência de associação é compatível com a falta de
  resposta no IP histórico, mas não explica por si só a mudança de calibração;
  consulte a [auditoria de baseline](backup-audit.md).
- Os artefatos e capturas integrais de #16–#19 ficam em armazenamento privado.
  O repositório contém resumos/hashes sanitizados, não credenciais ou dumps de
  flash. Os pacotes ativos continuam presos aos campaign IDs de #15/#16–#18 e
  não podem ser atribuídos ao piloto #20.

O parágrafo acima registra o bloqueio observado em 16/10. Em 17/10 a baseline
foi aprovada e a primeira qualificação de slot foi iniciada, mas terminou antes
do disparo por falta de UDP; consulte o resultado atualizado no início deste
arquivo. `boot_id`, firmware/boot ativos, sensor, GPIO5→GPIO6 e janela viva
qualificada continuam sem confirmação para esta campanha.

### Trabalho de preparação concluído antes do HIL

- A origem de `b66c24a3…` foi auditada somente por leitura: o arquivo privado de
  16 MiB coincide com seu recibo; a leitura pré-HIL e a restauração final da
  sessão #16 repetem o hash `b66…`, com oito regiões verificadas. Isso não é a
  leitura viva da #20 em 16/10, cujo hash `d29…` divergiu de `b66…` em 7.920
  bytes de NVS. `4fcc…` pertence a um dump integral de outra sessão; `b00…` é
  histórico de um backup de aplicação de 1 MiB. A cadeia e os limites estão em
  [auditoria da imagem-base](backup-audit.md).
- O preflight vivo identificou uma ESP32-S3 QFN56 rev. 0.2 com flash de 16 MiB
  e produziu uma leitura integral; a comparação com `b66…` divergiu em 7.920
  bytes de NVS. A triagem de metadados atribuiu a divergência lógica a um único
  registro de calibração PHY, sem alteração dos registros Wi-Fi, boot ID
  persistente ou ledger antirretorno da aplicação nos snapshots. Esse era o
  limite de conhecimento em 16/10; em 17/10 o usuário aprovou a leitura integral
  de hash `00aa…` como nova baseline. O bloqueio atual é a janela UDP, descrita
  no resultado da sessão de 17/10.
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

## Próximo requisito de evidência

A baseline de 17/10 foi aprovada e restaurada após o slot sem disparo. Antes de
retomar, confirmar a causa da falta de UDP, o `boot_id`, firmware/boot ativos,
sensor, GPIO5→GPIO6 e uma janela viva válida. Só então criar campanha `pilot`
com ID novo, artefatos assinados que correspondam ao mesmo campaign ID, logs
próprios e restauração verificada antes de cada slot. Não atribuir à #20 os
dados singulares de #16–#18 nem as chamadas de microbenchmark da #19.

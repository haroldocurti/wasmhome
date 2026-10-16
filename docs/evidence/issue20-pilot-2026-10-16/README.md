# Issue #20 — preparo e bloqueio do piloto físico

## Resultado desta sessão

**Nenhuma atualização física foi executada nesta sessão.** Não há disparos,
aceites, capturas UART/UDP nem resultados físicos de piloto. Portanto, o
requisito de cinco atualizações completas aceitas em cada cenário permanece
sem evidência: A `0/5`, B `0/5`, C `0/5`.

A revisão congelada em [protocolo-campanha-issue20-v1.0.md](../../protocolo-campanha-issue20-v1.0.md)
e a [agenda candidata](pilot-schedule.json) são documentos de preparação; não
devem ser interpretados como resultado de hardware. A calibração #19 examinada
contém uma atualização física aceita por cenário e não substitui as cinco
tentativas do piloto.

## Checagem de acesso e estado da bancada

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
  VID:PID `1a86:55d3`; nenhum processo segurava o device na checagem. Isso
  identifica o adaptador, não o ESP32, seu `boot_id`, versão ou conteúdo da
  flash. A porta não foi aberta para preservar o estado atual.
- Os artefatos e capturas integrais de #16–#19 ficam em armazenamento privado.
  O repositório contém resumos/hashes sanitizados, não credenciais ou dumps de
  flash. Os pacotes ativos continuam presos aos campaign IDs de #15/#16–#18 e
  não podem ser atribuídos ao piloto #20.

Não foi aberta a porta serial: a identidade e o estado da placa não podiam ser
confirmados, e a abertura pode alterar linhas de controle/reiniciar a placa.
Também não houve flash, reset intencional, disparo UDP ou alteração do servidor.
O CT107, toolchain e servidores estão acessíveis, mas não há conexão confirmada
com o ESP32 pela rede e o adaptador local não fornece identidade/estado sem
abrir a serial. Esse bloqueio físico deixa o piloto em A `0/5`, B `0/5`, C `0/5`.

## Trabalho possível concluído

- A origem de `b66c24a3…` foi auditada somente por leitura: o arquivo privado de
  16 MiB coincide com seu recibo; a leitura pré-HIL e a restauração final da #16
  repetem o hash, com oito regiões verificadas. `4fcc…` pertence a um dump
  integral de outra sessão; `b00…` é histórico de um backup de aplicação de
  1 MiB. A cadeia e os limites estão em [auditoria da imagem-base](backup-audit.md).
  Isso não identifica a placa atualmente conectada: qualquer escrita continua
  bloqueada até o preflight vivo confirmar ID, tamanho e leitura integral igual
  a `b66…`.
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

Quando a placa estiver novamente acessível, confirmar sua identidade e estado
sem pressupor que o IP histórico ainda vale. Só depois do preflight e da
verificação do backup integral de 16 MiB, executar uma campanha `pilot` com ID
novo, artefatos assinados que correspondam ao mesmo campaign ID, logs próprios e
restauração verificada antes de cada slot. Não atribuir à #20 os dados singulares
de #16–#18 nem as chamadas de microbenchmark da #19.

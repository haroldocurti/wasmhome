# Checklist de execução do plano

Atualização em 12/10/2026; snapshots históricos de 09/10 e 11/10 preservados abaixo. Este guia segue as etapas E0–E7 de [plano-de-execucao.md](plano-de-execucao.md), as decisões em [CONTEXT.md](../CONTEXT.md), o protocolo UDP, as decisões de envelope e resiliência, e as issues #1–#24 de `haroldocurti/wasmhome`. As dependências listadas por issue refletem a consulta GraphQL preservada no snapshot de 11/10.

## Como usar

- `[x]` indica que a evidência indicada foi examinada e atende ao item descrito neste checklist.
- `[~]` indica evidência parcial, integração pendente ou divergência entre artefato e estado do rastreador.
- `[ ]` indica trabalho ainda sem evidência suficiente.
- Issue fechada não encerra automaticamente um item. Conferir critérios, artefato, revisão e estado de integração antes de atualizar o mapa #1.
- Antes de uma sessão física, reconfirmar placa, porta serial, rede, endereços, alimentação e ambiente. Valores de inventário podem mudar.

As caixas refletem somente o snapshot indicado. Registros posteriores podem alterar o estado.

### Avanço local em 10/10

- O observador da #10 recebeu proteção exclusiva dos resumos no commit local `7b8e470`. Seus 30 testes e duas recalibrações host passaram; os [registros novos](evidence/observer-revalidation-2026-10-10/README.md) preservam a fonte e os limites.
- A árvore conjunta E0/preparo E1 passou por 68 testes locais sem skips e pelos verificadores offline de transporte e cálculo. A [revisão adversarial Luna xhigh](evidence/execucao-2026-10-09/revisao-adversarial.md) concluiu sem bloqueadores técnicos remanescentes; publicação, integração e aceite do rastreador permanecem pendentes.
- A #7 tem [receita candidata](proposta-toolchain.md) e [decisões de preparação](proposta-toolchain-decisoes.md). Não houve instalação nem geração Wasm/AOT. A combinação runtime/compilador 2.4.5 exige port explícito, novos pins, revisão e prova E1; não misturar AOT v5 com runtime 2.4.0.

### Integração confirmada em 10/10

- A #10 foi integrada pela [PR #28](https://github.com/haroldocurti/wasmhome/pull/28), merge `6c94ab505672e4056c1a87932403853fe9aa06e7`. A [PR #29](https://github.com/haroldocurti/wasmhome/pull/29), merge `8b372147e739e46c8952ab8c7907b6c76a76345b`, integrou E0/preparo E1. A árvore `main` resultante é `56a6f161277d16ba725a536b76230aecf1e120d6`.
- A #6 foi encerrada com seus sete critérios marcados e `ready-for-agent` removido; os vínculos da #10 e o mapa #1 foram atualizados e relidos. Isso supera a pendência formal descrita no snapshot de 09/10, sem ampliar a prova física para os serviços ou campanhas posteriores.
- A #7 recebeu um [port candidato local 2.4.5](runtime-port-candidato.md), com patches, fontes por SHA e identidade própria. Naquele snapshot, instalação, origem do compilador binário, builds B/C, execução física e aceite de #7/#9 continuavam pendentes. Os snapshots e inventários abaixo preservam o histórico anterior.

### Retomada em 11/10 pelo Linux

- [x] PRs #28/#29 integradas, árvores finais verificadas e #6 encerrada com evidências.
- [x] Port candidato 2.4.5 corrigido e revisado, 60 testes locais sem skips.
- [x] CT107 recebeu dependências exatas, venv congelado, ESP-IDF 5.2.6, WASI-SDK 27 e PowerShell 7.6.6 em diretório dedicado.
- [x] Build nativo no CT107 concluído; lock/componente externo, configuração, hashes e disassembly conferidos. Não foi gravado na placa.
- [x] Proposta doadora CT110 e revisão R3 preservadas: 28 testes locais, sem execução de infraestrutura ou LLVM.
- [x] Plano detalhado, código candidato e recibos locais incorporados nesta publicação autorizada na `main`.
- [x] Preparar o Linux físico, compilar LLVM/wamrc real e transferir/conferir R5 no CT107; o executável host e as consultas de target/CPU passaram. [Registro e recibos de 12/10](evidence/retomada-linux-2026-10-12/README.md).
- [x] Gerar os artefatos Wasm/AOT duas vezes no CT107, em R6/R7; as cinco saídas têm hashes idênticos.
- [x] Compilar firmware A/B/C no mesmo snapshot de código, em diretórios e `sdkconfig` separados; validar configuração efetiva, símbolos, runtime e limites do AOT. Inspecionar objeto AOT Xtensa e confirmar o laço no código.
- [ ] Executar checksum A/B/C no ESP32-S3 e obter atestado/reconstrução independente do recibo `wamrc`. #7/#9 permanecem abertas.

O [complemento E1 de 12/10](evidence/retomada-linux-2026-10-12/e1-scenarios/README.md) contém hashes de ELF/BIN e configurações efetivas, recibos de validação, manifests, logs e disassembly. Os ELF/BIN completos permanecem nos diretórios dedicados do CT107. Nenhum firmware foi gravado, nenhum checksum foi executado na placa e nenhuma suíte de testes foi executada nesta retomada.

O [registro de retomada](retomada-linux.md) reúne comandos, caminhos, SHAs, revisões, falhas preservadas e limites. CT110 fica arquivado como alternativa substituída; nenhum guest foi desligado. Os itens históricos abaixo não anulam esta atualização.

## Decisões que orientam todas as etapas

- FreeRTOS é comum aos cenários A, B e C. A variável comparada é a estratégia de execução e atualização: imagem da aplicação C nativa por OTA com reboot; módulo Wasm no interpretador clássico WAMR; módulo AOT no WAMR para ESP32-S3.
- A bancada mantém dois serviços. O módulo de telemetria permanece ativo e publica amostras numeradas do DS18B20 por binding nativo. O módulo de controle executa a regra do GPIO nas versões v1/v2 e é o único módulo substituído em B e C. A campanha nativa substitui a imagem OTA da aplicação.
- O driver opera o DS18B20 em três fios, 10 bits, com aquisição a cada 500 ms. A telemetria é observada a cada 100 ms. A regra de controle usa histerese de 1 °C, limiar de 25 °C em v1 e 30 °C em v2. O loopback físico é GPIO5→GPIO6. O observador envia estímulos conhecidos para verificar o resultado de controle e mede separadamente a telemetria do sensor.
- UDP binário v1 é o canal de observação; HTTPS entrega artefatos; JSONL preserva os registros brutos. Heartbeat, compilação e diagnóstico não comprovam disponibilidade funcional.
- A campanha normal tem como referência 30 blocos, uma tentativa completa de cada cenário por bloco, total de 90 tentativas. A campanha de resiliência é separada e tem 70 tentativas de falha mais dois controles. O piloto pode justificar outra amostra normal, mas qualquer ajuste precisa de análise e justificativa registradas antes da coleta definitiva.

### Conflito documental encontrado

O conteúdo original do parágrafo de delimitação em [plano-de-execucao.md](plano-de-execucao.md) dizia que sensores físicos e múltiplos módulos ficavam fora da primeira campanha. Isso conflitava com as decisões em `CONTEXT.md`, na issue #1 e nas issues de execução: telemetria DS18B20 estável e controle GPIO substituível. Em 09/10, o plano foi ajustado na árvore de trabalho para registrar os dois serviços aprovados. Este checklist preserva a decisão e registra a origem do conflito. A correção de escopo foi integrada na PR #29; o detalhamento de coleta é incorporado nesta publicação.

## Rastreador atualizado em 11/10 e limites da evidência

| Issues | Estado reconfirmado em 11/10/2026 | Evidência conferida e limite |
|---|---|---|
| #1 | Aberta | Mapa pai contém as 23 subtarefas. Deve receber atualização de evidência quando cada critério realmente terminar. |
| #2 | Fechada, com divergência | `docs/bancada-fisica.md` e comentário de resolução registram os itens de inventário. O corpo atual da issue ainda mostra os três critérios desmarcados e conserva o rótulo `ready-for-agent`. A montagem foi confirmada por fotos e pelo usuário; teste físico de sensor e loopback apareceu depois na evidência da #8. Reconciliar caixas e rótulo no rastreador antes de tratar a issue como aceite formal. |
| #3 | Fechada | Protocolo v1, vetores e codec de host documentados; o comentário registra 10 testes locais e ausência de checks de CI. O transporte embarcado e sua prova física foram feitos na #8, não pela #3. |
| #4–#5 | Fechadas | Decisões de envelope e matriz de resiliência documentadas. Isso conclui pesquisa, não implementação nem execução na placa. |
| #6 | Fechada e integrada | PR #29, sete critérios com evidências e rótulo `ready-for-agent` removido. CT107 ativo, reserva DHCP confirmada, reconstrução/reboot do CT109 demonstrados e CT109 parado. Diagnósticos PC↔MCU e ESP32→LXC de 09/10 preservados. Toolchain completa ainda pertence à #7. |
| #7, #9, #11–#24 | Abertas | Critérios seguem pendentes conforme os corpos atuais das issues. |
| #8 | Fechada e integrada na `main` | PR #27, 17 testes locais, build ESP-IDF nativo e evidência física de plataforma. O diagnóstico local adicional de 09/10 registrou sensor em 11/11 leituras (24,250 °C), GPIO nos dois níveis, 87 datagramas UDP, probe válida e rejeição de boot incorreto/datagrama truncado. O resumo foi integrado pela PR #29. Isso prova diagnóstico de hardware/transporte, não o serviço funcional da #11, OTA, WAMR/AOT, módulos ou campanhas. CI não estava configurada na PR. |
| #10 | Fechada e integrada | PR #28, merge `6c94ab5`, 30 testes locais e recalibrações host de 10/10; calibrações históricas de placa preservadas. Integração e árvore final verificadas. Isso não prova os serviços definitivos nem atualização real. |

## E0. Inventário e contrato

- [~] **#2, inventário físico.** Evidência: [bancada-fisica.md](bancada-fisica.md), [evidência ESP32-S3](evidence/esp32s3-hardware-2026-10-02.md) e comentário de resolução da issue. Registrar ESP32-S3-N16R8, flash/PSRAM, porta, DS18B20/GPIO4, GPIO5→GPIO6, alimentação, rede, host e condições iniciais. A identificação da placa portadora e do ponto de acesso ainda falta; reconfirmar COM/IP/ambiente por sessão. O corpo atual mantém os critérios desmarcados e o rótulo `ready-for-agent`; alinhar o rastreador após conferência.
- [x] **Reconfirmação de bancada em 09/10.** Evidência integrada pela PR #29: [resumo de diagnóstico da plataforma](evidence/execucao-2026-10-09/platform/summary.json). COM12 foi identificada como adaptador CH343 (VID `1A86`, PID `55D3`); Ethernet2 estava em `192.168.0.190`, índice 11. O diagnóstico leu o sensor 11/11 vezes a 24,250 °C, verificou GPIO nos dois níveis, recebeu 87 datagramas UDP e rejeitou probe de boot incorreto e datagrama truncado. Esses dados atualizam o inventário da sessão e a prova de plataforma; não concluem #11, que exige telemetria e controle funcionais no firmware da campanha.
- [x] **#3, contrato UDP v1.** Evidência: [protocolo-observacao.md](protocolo-observacao.md), vetores e testes do codec de referência registrados na issue. O contrato cobre cabeçalho de 48 bytes, tipos, campos, ordem de bytes, tamanhos, rejeições e vetores de telemetria, controle, disparo e aceite/recusa. Limite: a validação de host, por si só, não demonstra transporte no ESP32.
- [x] **#4, envelope e antirretorno, decisão.** Evidência: [envelope-assinado-antirretorno.md](research/envelope-assinado-antirretorno.md). COSE_Sign1, CBOR determinístico, ES256/P-256, SHA-256, TLS com CA local e piso uint64 em NVS por campanha/cenário/componente são o contrato de implementação. Não é prova de código executado.
- [x] **#5, desenho da resiliência.** Evidência: [ensaios-resiliencia.md](research/ensaios-resiliencia.md). Endereço linear zero é válido em Wasm; `WASM-ZERO` é controle. OOB e divisão por zero são traps Wasm; `NAT-ABORT` mede aborto nativo e não é equivalente a esses traps. Não é prova dos mecanismos no dispositivo.
- [x] **#6, sistema-base LXC.** Dependência: #2. Evidência: [provisionamento-lxc.md](provisionamento-lxc.md), [registro do CT 107](evidence/lxc-107-2026-10-01.txt) e [registro da sessão](evidence/execucao-2026-10-09/README.md). O CT 107 `wasmhome-bench` está `running`, com Debian 13.1, IP DHCP `192.168.0.138`, acesso por chave e persistência demonstrados. A reconstrução separada e os caminhos diagnósticos PC↔MCU e ESP32→LXC estão comprovados localmente. A revisão adversarial e a integração pela PR #29 foram concluídas; o aceite da #6 está registrado no rastreador. Critérios técnicos:
  - [x] Preservar o resultado de diagnóstico UDP observador→ESP32 e ESP32→observador com origem, direção e logs. Essa parte foi observada em 09/10.
  - [x] Testar o acesso iniciado pelo ESP32-S3 a um endpoint temporário no LXC, com protocolo, endereço, direção e resultado registrados. [Captura positiva](evidence/execucao-2026-10-09/lxc-positive-r2/summary.json) e [log LXC](evidence/execucao-2026-10-09/lxc-positive-r2-server.jsonl): TCP/HTTP em `192.168.0.138:18080`, origem `192.168.0.145`, mesmo nonce e continuidade UDP no mesmo boot. A origem divergente foi recusada no ensaio negativo; este não é um ensaio de envelope nem de campanha.
  - [x] Registrar a confirmação humana de 09/10 de que `192.168.0.138` está reservado à MAC `BC:24:11:B6:36:F7` do CT 107. A sessão verificou IP/MAC efetivos; não leu o painel DHCP. Reconfirmar o endpoint no início da configuração HTTPS da #15.
  - [x] Reconstrução autorizada pelo usuário em 09/10. CT 109 criado com hostname `wasmhome-bench-rebuild`, MAC `BC:24:11:73:7F:32`, IP DHCP `192.168.0.171`, 1 CPU, 1.024 MiB RAM, swap 0 e 24 GiB em DevServer. [Criação](evidence/execucao-2026-10-09/lxc-rebuild-create.txt), [identidade](evidence/execucao-2026-10-09/lxc-rebuild-identity.txt), [reboot gracioso](evidence/execucao-2026-10-09/lxc-rebuild-after-reboot.txt) e [SSH/persistência](evidence/execucao-2026-10-09/lxc-rebuild-ssh-after-reboot.txt) passaram. O CT 109 foi parado ao final; o CT 107 permanece em execução. Divergências do instrumento e do ifupdown2 estão no [registro da reconstrução](proposta-reconstrucao-lxc.md). O CT 108 pertence ao Paperclip e não foi usado.
  - [x] Capacidade, template e ID foram reconfirmados antes da criação, com resultados no registro acima. A amostra do host não comprova capacidade para compilar a toolchain.
- [ ] Fechar entradas/saídas de referência para v1/v2, parâmetros, timeout candidato, cadência e janelas de observação. Reconfirmar condições da bancada antes do primeiro ensaio de cada sessão. Timeout inicial: 300 ms, sujeito ao piloto.

## E1. Viabilidade dos três cenários

- [~] **#7, toolchain reproduzível no LXC.** Dependência: #6. Em 12/10, LLVM/wamrc real, geração e repetição dos artefatos Wasm/AOT e builds de firmware A/B/C foram conferidos no CT107, com versões fixadas, hashes e `sdkconfig` separados. As cinco saídas de R6/R7 coincidem. Permanecem pendentes revisão/aceite e atestado independente que associe o recibo unsigned ao binário `wamrc`.
- [x] **#8, plataforma comum.** Dependências satisfeitas: #2 e #3. Evidência integrada na `main` pela PR #27: build nativo ESP-IDF 5.2.6, 17 testes locais e logs físicos de DS18B20, GPIO, PSRAM, Wi-Fi/UDP e watchdog. O firmware diagnóstico não implementa os serviços da campanha. A reconexão de Wi-Fi no mesmo boot, OTA, WAMR/AOT e módulos continuam sem prova.
- [~] **#9, WAMR interpretado e AOT no MCU.** Dependências: #7 e #8. Os firmware A/B/C foram compilados e validados no CT107; B usa intérprete clássico, C usa AOT v5, validator AOT ligado e bounds checks explícitos. O objeto Xtensa do AOT preserva o laço. Ainda falta executar a mesma função nos dois modos no ESP32-S3, confirmar checksum e registrar SRAM/PSRAM, erros e limites físicos. Até lá, não há prova de execução no MCU.
- [~] **Carga mínima nativa da etapa E1.** Dependências: #7 e #8. Em 09/10, o build local com `-O3` efetivo executou cinco aquecimentos verificados pelo runner e 30 chamadas no ESP32-S3 com checksum `0xDBB2F0D4`, em uma única sessão de carga. [Log serial](evidence/execucao-2026-10-09/native/serial.log) e [derivação](evidence/execucao-2026-10-09/native/derived-native.json) preservam a prova preparatória de A. Em 12/10, A/B/C foram compilados na mesma revisão e validados no CT107; falta repetir a execução controlada no MCU e confirmar o checksum. O ensaio de 09/10 não demonstrou transporte funcional e continua limitado àquela única sessão. A prova diagnóstica da #8 não substitui esta carga nem os dois serviços da #11.

## E2. Observação e serviços de referência

- [x] **#10, observador externo.** Dependência formal: #3. Evidência de ferramenta e calibração consta em [observador-externo.md](observador-externo.md) e nos registros citados pela issue. Os ensaios históricos estão registrados no commit `810b8be`; a correção passou por 30 testes locais e a PR #28 integrou a entrega na `main`, com revisão da árvore final. A fixture calibra a medição, mas não conclui E2: ainda é preciso demonstrar os serviços funcionais de #11, #13 e #14. Concluir E2 somente após essas demonstrações e a integração exigida antes de avançar para atualização experimental.
- [ ] **#11, referência nativa de telemetria e GPIO.** Dependências: #8 e #10. Demonstrar os dois serviços concorrentes no firmware monolítico; validar as regras v1/v2 e a histerese aprovada; enviar estímulos conhecidos e conferir resultado, versão, ID, boot, GPIO comandado e GPIO lido pelo loopback. Preservar JSONL bruto e evidência da placa. A telemetria deve refletir o DS18B20 real, não fixture de diagnóstico.
- [ ] **#12, firmware-base, bindings e gerenciador.** Dependências: #8 e #9. Expor somente os bindings aprovados para temperatura, GPIO, tempo e publicação. Manter instâncias e ciclo de vida próprios para telemetria e controle; trocar controle em ponto seguro entre requisições sem parar telemetria. Medir runtime, instâncias, pilhas e buffers antes/durante/depois da liberação. Proteger a memória acessada pelos bindings e registrar a configuração WAMR.

- [ ] **#13, dois módulos no interpretador clássico.** Dependências: #10 e #12. Rodar simultaneamente telemetria e controle; verificar cadências, entradas/resultados, versões e loopback físico. Guardar a configuração que prova interpretador clássico, artefatos e hashes.
- [ ] **#14, dois módulos AOT.** Dependências: #10 e #12. Demonstrar os mesmos serviços e critérios da #13 em AOT. Guardar configuração exclusiva AOT, ferramenta/versão de `wamrc`, artefatos e hashes. Comprovar `WAMR_BUILD_AOT_VALIDATOR=1` e bounds checks ativos.

## E3. OTA nativo e distribuição autenticada

Depois que cada bloqueio próprio for atendido, não é necessário serializar a trilha OTA e a trilha modular. A #15 é dependência comum; a #19 espera as três atualizações observáveis.

- [ ] **#15, artefatos HTTPS.** Dependências: #4 e #7; requer endpoint estável de #6. Gerar v1/v2 para A/B/C com manifesto comum, assinatura COSE e payload ligado por tamanho e SHA-256. Servir por HTTPS ancorado à CA local, com validação do nome. Demonstrar sequência crescente, rejeição de assinatura/hash/versão inválida e logs de build/servidor/download. Registrar fingerprints públicos; nunca publicar chaves privadas.
- [ ] **#16, atualização OTA observável.** Dependências: #10, #11 e #15. Atualizar a imagem da aplicação v1→v2 pelo slot OTA, não a imagem integral da flash. Guardar `t_envio` do disparo e `t_aceite` de `update_accepted` (ou sua ausência), mais etapas de download/validação/ativação, reboot, boots, lacunas, perdas e primeira resposta funcional correta de v2. Em tentativa aceita, `update_total` vai de `t_envio` à primeira resposta correta de v2; sem aceite ou ativação, preservar o estado e usar `null` quando o protocolo indicar. Avançar o piso NVS somente após teste de saúde e confirmação da imagem; preservar rollback para a imagem anterior se a confirmação falhar. Guardar tamanho/hash do payload, memória aplicável e JSONL da janela anterior/durante/depois.

## E4. Troca modular e resiliência implementada

- [ ] **#17, troca observável interpretada.** Dependências: #10, #13 e #15. Baixar e validar o controle interpretado v2 enquanto v1 permanece disponível; testar o novo módulo, persistir slot e piso e trocar em ponto seguro entre requisições. Provar que telemetria não parou e permaneceu na mesma instância e no mesmo `boot_id`. Registrar memória, payload, etapas, lacunas e recuperação.
- [ ] **#18, troca observável AOT.** Dependências: #10, #14 e #15. Aplicar os critérios da #17 ao controle AOT, comprovando continuidade da telemetria no mesmo boot.
- [ ] **#22, mecanismos de injeção e recuperação.** Dependências: #5, #12 e #15. Antes da campanha, demonstrar em ensaios preliminares:
  - [ ] Canal serial de fixture fixa com `fault_request`, `attempt_id` e `case_id`; `fault_accepted` antecede a injeção e significa aceite do ensaio, não do artefato. Não aceitar URLs, caminhos ou chaves arbitrárias pelo canal.
  - [ ] Correlacionar eventos seriais e disponibilidade UDP por IDs/boots, preservando relógios de cada fonte e JSONL bruto, inclusive após reboot.
  - [ ] `ENV-OLD` percorre o validador real do envelope e o piso NVS real. Rejeição antecipada de `UPDATE_TRIGGER` é ensaio distinto e não substitui essa célula.
  - [ ] `ENV-HASH` pode gravar apenas no slot inativo, invalida o payload divergente e preserva slot ativo, versão e piso. Nunca instancia nem ativa o conteúdo divergente.
  - [ ] Preservar bounds checks em interpretador e AOT; demonstrar traps Wasm isolados na instância de controle e `NAT-ABORT` com pânico/reboot. No trap, não chamar `wasm_runtime_destroy()`; descartar somente a instância e os recursos de controle. A telemetria deve permanecer dentro do timeout funcional.
  - [ ] Recuperar o último controle válido; validar estado/piso antirretorno e correlação após reboot. Registrar ferramentas, fixtures, hashes, configurações e logs.

## E5. Métricas, piloto e congelamento

- [ ] **Conciliar amostra e agenda antes de #20/#21.** A referência vigente é 30 tentativas por cenário; a agenda proposta de 90 slots pode gerar menos de 90 tentativas aceitas. Decidir e registrar o alvo de slots versus atualizações aceitas, com contagem, agenda/reposição e denominadores coerentes. Os planos registram a proposta e a pendência, sem substituir a referência silenciosamente. Preservar todos os disparos, recusas, timeouts e falhas.

- [ ] **#19, métricas comparáveis.** Dependências: #16, #17 e #18. Cobrir payload, tempo total e etapas, SRAM/PSRAM antes/durante/depois, custo do runtime e da chamada, processamento, lacunas e perdas. Usar relógio monotônico do observador para intervalos que cruzam reboot; não subtrair relógios de dispositivos. Representar campo inaplicável como `null`/`not_applicable`, nunca zero. Verificar a equivalência funcional da carga e o checksum/resultados.
- [ ] **#20, piloto e protocolo congelado.** Dependência: #19. Executar cinco atualizações completas por cenário, 15 tentativas rastreáveis fora do conjunto final. Uma tentativa normal só começa após `update_accepted` correlacionado. Guardar `t_envio` e `t_aceite` (null se não recebido); para tentativa aceita, `update_total` começa em `t_envio`. Disparos sem aceite ficam em registro separado e não recebem aceite ou resultado de atualização inventado. Revisar dispersão, timeout, cadência, janelas, aquecimento, condições iniciais, sequência, critérios de invalidação e matriz de logs. O aquecimento deve ocorrer antes do disparo medido. Congelar o timeout funcional, inicialmente 300 ms, e aplicar o mesmo valor à resiliência. Preservar falhas após aceite e justificar antes da campanha qualquer alteração da referência de 30 tentativas por cenário.

## E6. Campanhas definitivas

- [ ] **#21, campanha normal balanceada.** Dependência: #20. Executar o tamanho amostral justificado e congelado na #20. A referência é 30 blocos, com A/B/C uma vez por bloco na mesma placa, total de 90 tentativas e cada uma das seis ordens cinco vezes. Se houver ajuste documentado antes da coleta, redefinir o balanceamento das ordens de forma coerente com a amostra aprovada. Registrar embaralhamento e semente. Restaurar v1 com envelope autenticado e sequência nova; usar para v2 uma sequência ainda maior no mesmo namespace. Aguardar estabilidade e excluir o preparo da janela. Uma tentativa só passa a integrar a campanha após `update_accepted` correlacionado; guardar `t_envio` e `t_aceite` (null se não recebido), iniciar `update_total` em `t_envio` para tentativas aceitas e manter falhas/timeouts ocorridos depois do aceite no denominador. Disparos sem aceite ficam em registros separados, sem contagem como tentativa aceita nem aceite inferido. Só invalidar falha comprovada do instrumento, com motivo e reposição identificada; nunca repetir seletivamente resultado desfavorável.
- [ ] **#23, campanha de resiliência.** Dependências: #20 e #22. Executar a matriz aprovada separadamente da campanha normal, cinco vezes por célula aplicável:

  | Caso | Cenários | Tentativas | Resultado mínimo esperado |
  |---|---|---:|---|
  | `ENV-SIG` | A, B e C | 15 | Assinatura bem formada da próxima sequência com chave P-256 não confiável; `SIGNATURE_INVALID` antes de gravar o payload. |
  | `ENV-HASH` | A, B e C | 15 | Alterar um bit após assinar; `HASH_MISMATCH`, invalidar o slot inativo e preservar slot ativo, versão e piso. |
  | `ENV-OLD` | A, B e C | 15 | Envelope válido com sequência igual/menor; `SEQUENCE_NOT_NEWER` pelo validador e piso NVS reais. |
  | `WASM-OOB` | B e C | 10 | `i32.load` de 4 bytes iniciado 3 bytes antes do fim da memória de uma página; chamada retorna falha com trap OOB. |
  | `WASM-DIV0` | B e C | 10 | Divisor zero recebido em execução; chamada retorna falha com trap de divisão inteira. |
  | `NAT-ABORT` | A | 5 | `esp_system_abort` controlado, pânico, novo `boot_id` e motivo `ESP_RST_PANIC`. |
  | `WASM-ZERO` (controle antes da bateria) | B e C, uma vez por modo | 2 controles | Leitura válida no endereço zero retorna `0x12345678` sem exceção. |

  Total: 70 falhas e dois controles. Confirmar pelo menos 30 respostas válidas de cada serviço aplicável antes e depois da injeção. Após `fault_accepted`, manter rejeições, falhas, resets e timeouts no denominador. Sem confirmação do disparo, registrar e repor com outro ID; não classificar como rejeição do envelope. Usar o timeout funcional congelado no piloto e 30 s como limite operacional de recuperação, preservando o tempo efetivo. No trap Wasm, exigir mesmo boot, telemetria contínua e recuperação do controle; no `NAT-ABORT`, registrar novo boot e recuperação de ambos os serviços. Não somar os grupos como falhas semanticamente equivalentes.

## E7. Análise e pacote reproduzível

- [ ] **#24, resultados.** Dependências: #21 e #23. Analisar ambas as campanhas, em grupos separados e com seus limites de equivalência explícitos. Gerar tabelas/gráficos por scripts versionados a partir dos JSONL brutos. Reportar taxa de sucesso, distribuição, mediana, intervalo interquartil, diferenças pareadas por bloco na campanha normal e intervalo de confiança de 95% por bootstrap dos blocos, com método e semente. Publicar tempos de sucesso acompanhados de contagens de falhas/timeouts; não usar zero nem excluir falha sem explicação.
- [ ] Entregar pacote com dados brutos e derivados, código e revisão, toolchain/configurações, hashes, manifestos sem segredos, ordem/seed dos blocos, logs do observador/dispositivo/servidor, scripts, instruções de reprodução e limites. Identificar campanha, bloco, tentativa, cenário, boot e versão. Restringir conclusões à placa e rede testadas; chamadas repetidas de um microbenchmark não são tentativas independentes.

## Gates de revisão e de aceite

Aplicar estes gates a cada entrega antes de declarar critério cumprido, publicar integração ou fechar issue. O registro de revisão deve apontar issue, revisão exata da árvore e evidências examinadas.

- [ ] Para execução, usar **Luna em esforço xhigh**; para tarefas de alta complexidade, usar **Sol em esforço high**. Em todos os casos, cada entrega também passa por revisão adversarial independente de **Luna em esforço xhigh**. A escolha do modelo executor não substitui a revisão adversarial.
- [ ] Revisores confrontam cada critério da issue com código/documento e evidência; procuram caminhos negativos, requisitos omitidos, discrepância entre contrato e execução, dados ausentes, denominador incorreto e alegações além da prova. Corrigir achados e repetir as verificações afetadas na árvore final. Registrar achado não resolvido com motivo e impacto; não chamar a entrega de aprovada enquanto houver bloqueador.
- [ ] Separar os registros de implementação, teste local, compilação, execução na placa, CI, publicação na branch, integração na `main` e aceite do ticket. Cada marco tem sua própria evidência. Se não houver CI configurada, registrar isso; não substituir por testes locais.
- [ ] Para ensaio físico, guardar logs originais, data, placa, porta, firmware/artefato e hash. Repetir após mudança no firmware, no instrumento, no contrato ou na árvore validada.
- [ ] Antes de fechar uma issue, conferir item a item os critérios aceitos, linkar evidências reproduzíveis, registrar achados/resolução, alinhar as caixas e rótulos da issue e atualizar o mapa #1. Issue fechada sem essa conferência não altera o estado deste checklist.

## Fontes usadas

- [Plano de execução](plano-de-execucao.md)
- [Glossário e decisões de contexto](../CONTEXT.md)
- [Protocolo binário de observação](protocolo-observacao.md)
- [Envelope assinado e política antirretorno](research/envelope-assinado-antirretorno.md)
- [Ensaios de resiliência](research/ensaios-resiliencia.md)
- [Provisionamento do LXC](provisionamento-lxc.md)
- [Plataforma comum](plataforma-comum.md)
- [Observador externo](observador-externo.md)
- [Issues #1–#24](https://github.com/haroldocurti/wasmhome/issues/1)

# Próximos passos distribuídos entre agentes

## Estado atual — 14/10/2026

A #7 permanece concluída. A #11 teve aceite físico no ensaio
`services-run03`, fonte `a91ff5e`: v1 e v2 passaram 45 critérios cada,
com telemetria real DS18B20, histerese aprovada e GPIO5 lido pelo loopback em GPIO6.
A restauração integral coincidiu com o backup antes e depois do boot de
observação, e a captura final confirmou o firmware original executando.
Essa passagem por duas imagens via serial não demonstra OTA.

A #9 teve aceite físico no ensaio `abc-6317fa9-run01`, fonte `6317fa9`,
com runtime `2.4.5-wasmhome.3`: nativo, interpretado clássico e AOT completaram
cinco aquecimentos, 30 chamadas medidas, checksum `DBB2F0D4`, 67 eventos de
memória e uma inicialização por cenário. Builds, configurações efetivas,
compatibilidade do compilador e logs foram revisados por Luna xhigh.
A suíte local teve 96 casos, 95 aprovados e um skip exclusivo de Windows.

As três leituras integrais após os cenários mostraram zero alterações fora de
`ota_0`. A restauração coincidiu integralmente com o backup antes do boot do
firmware original. A observação após esse boot registrou 1.961 bytes alterados
somente em NVS, preservados com linhagem `4fcc…` → `bcbd…`; a imagem original
continuou idêntica. A captura final confirmou sua execução, sem leitura integral
posterior. Uma sessão de 30 chamadas não representa 30 atualizações independentes.
As falhas anteriores permanecem documentadas; o aceite é de viabilidade mínima.

Próxima etapa: #12 → #13/#14. A distribuição Luna xhigh da #12 separa implementação
do gerenciador, testes de contrato/ciclo de vida e revisão de memória/configuração.
O coordenador integra, publica e opera a placa com exclusividade.
Seguir #15 após #4/#7; #16/#17/#18 segundo suas dependências; #19 → piloto #20 →
campanha #21; #22 → #23 → análise #24. Tamanho amostral e timeout continuam
condicionados ao piloto.

[Aceite físico A/B/C](evidence/e1-physical-2026-10-14/README.md)
· [Tentativa anterior](evidence/aot-run-8b3bddd-2026-10-14/README.md)
· [Evidência dos serviços](evidence/native-services-2026-10-14/README.md).


## Próxima entrega — #12

O código atual executa um único benchmark e inicializa/libera WAMR por execução.
Os serviços funcionais pertencem à imagem nativa. Ainda falta um gerenciador
persistente com duas instâncias, bindings e troca independente do controle.
O aceite físico da #9 remove a dependência de viabilidade; não entrega a #12.

| Frente Luna xhigh | Responsabilidade | Entrega e revisão |
|---|---|---|
| C — implementação | ABI e gerenciador em arquivos próprios | Contrato revisado por B antes da implementação; lifecycle revisado por A |
| A — testes | Contrato, falhas, concorrência e liberação | Testes em arquivos separados após congelar ABI; revisão por B |
| B — revisão | Memória, configuração e proveniência | Parecer sobre a revisão exata, pins de builds e métricas; confronto por C |
| Coordenador | Integração e operação física | Publicação e sessão exclusiva após gates; nenhum agente acessa a placa |

1. Fixar ABI/versionamento, dados com limites, códigos de erro e permissões por
   papel. Validar offset/comprimento do guest pelo WAMR, incluindo overflow;
   copiar dados e nunca reter ponteiros do guest.
2. Inicializar WAMR uma vez e registrar bindings de temperatura, GPIO, tempo e
   publicação. Criar slots separados de telemetria/controle, cada qual dono do
   artefato, módulo, instância, ambiente de execução, versão e quotas.
3. Serializar chamadas e troca de controle em ponto seguro entre requisições.
   Preparar e validar o candidato isoladamente; liberar o antigo só após cessarem
   suas chamadas. Falha mantém o controle anterior e a telemetria ativa.
4. Medir baseline, runtime, cada módulo, sobreposição do candidato, operação e
   liberação. Separar heap interno/PSRAM (total/livre/maior bloco), contadores do
   allocator WAMR, artefatos/buffers e pilhas; reportar ELF estático à parte.
5. Verificar ponteiro inválido/overflow, erro de host, export ausente, trap/OOM
   em cada fase, concorrência, troca com chamada em voo e retorno das alocações
   ao baseline após liberação. Fixar revisão e conferir configuração dos modos.
6. Compilar, revisar os pins e realizar prova funcional mínima com restauração
   da bancada. Publicar critérios e limites. Serviços completos #13/#14,
   atualização remota e campanha permanecem nas suas entregas próprias.

### Estado de execução — 14/10/2026

O contrato ABI v1, o gerenciador persistente e os módulos de serviço foram
implementados no checkout. Os testes de host para helpers do ABI e para o
gerenciador com shims ESP/WAMR passam (2 testes); eles validam o contrato e as
decisões do manager, não o runtime ESP-IDF/WAMR real. Foram adicionadas linhas
`WASMHOME_MODULE_MEMORY` para registrar heap interno/PSRAM, contadores WAMR,
capacidade de payload das filas, stacks solicitadas, watchdogs persistentes e
sobreposição/liberação. A revisão local do ciclo do watchdog encontrou que o
ESP-IDF 5.2.6 agenda a exclusão do timer de forma assíncrona; os timers/gates
agora permanecem no slot estático e deadlines evitam que callbacks atrasados
atinjam outra chamada. Um caso host exercita esse caminho.

A revisão independente final, o build com os pins em ESP-IDF, a auditoria das
configurações efetivas e a prova funcional na placa com restauração continuam
pendentes. Esta máquina tem PowerShell 7.6.6 e um `wamrc 2.4.5` cujo SHA-256 e
recibo correspondem ao runtime fixado `2.4.5-wasmhome.3`; o R5 disponível no
pacote de toolchain E1 corresponde ao runtime `.1` e não serve para esta geração.
Os probes do binário `.3` listam `xtensa` como alvo e `esp32s3` como CPU
reconhecida; as flags AOT do projeto continuam sem override de CPU até a revisão
do AOT gerado.
Não encontrei ESP-IDF 5.2.6/`idf.py`, Xtensa GCC, nem o pacote ou instalação
WASI-SDK 27.0 nos caminhos locais examinados. A geração/build com proveniência
continua indisponível, além de exigir checkout limpo. Há uma porta serial
enumerada, mas a placa/firmware nela não foi identificada nem acessada. Portanto,
a implementação local não equivale ao aceite da #12 e não amplia a prova E1.


Preparado em 13/10/2026 por solicitação do usuário. Modelo de todos os agentes:
**Luna (`gpt-6-luna`), esforço `xhigh`**. Base da preparação:
`9771e30a3edb667303f441076df77571f616ba18`. Este plano detalha o trabalho;
a distribuição não significa que as issues ou ensaios já foram concluídos.

## Equipe e regras de execução

| Agente | Frente inicial | Entrega inicial | Revisor da frente |
|---|---|---|---|
| A — `luna_validacao_local` | Testes Linux com codec C real | Comandos, log, contagens, revisão e falhas/skips | C |
| B — `luna_toolchain` | Evidências e fechamento da #7 | Matriz de critérios, lacunas e procedimento de atestado | A |
| C — `luna_bancada_servicos` | Preparação #9/#11 e dependências posteriores | Roteiro físico, requisitos do instrumento e sequência | B |

O coordenador integra os resultados, controla dependências e confronta as
alegações com a evidência. Há três agentes simultâneos, além do coordenador.
O revisor examina uma entrega feita por outro agente, na revisão exata de código
e evidência. Rever o plano não atesta automaticamente um binário nem aprova uma
implementação. Achados bloqueadores precisam ser corrigidos antes do aceite.

Nesta sessão, A executa testes locais; B e C fazem análise e preparação.
Rebuild de toolchain, mudança de firmware e ensaio físico são etapas futuras.
Cada frente trabalha em saída exclusiva e o coordenador altera os planos comuns.

## Onda 1 — validar o estado e preparar o fechamento da #7

### P0. Reconciliar a proveniência R6/R7 — executor B, revisor A

A revisão desta sessão encontrou inconsistências que impedem afirmar uma cadeia
coerente de geração até os builds sem esclarecimento:

- `generate-r6.log` e `generate-r7.log` declaram commit `b657a19b…` e árvore
  `1a166c0c…`; o manifest R6 e os recibos A/B/C declaram `801f9381…`/`c728d9ed…`.
- O log R6 registra hash de manifest `48bfdf9b…`, enquanto o arquivo arquivado
  e a comparação R6/R7 registram `f31158a3…`.
- A revisão histórica `801f9381…` não está disponível no checkout raso atual;
  isso não prova ausência no CT107. Recuperar o bundle/árvore de origem.

Preservar registros originais. Recuperar manifest R7 completo, metadados e árvore
exata do CT107/doador; confrontar fontes, geradores e timestamps; explicar as
diferenças com prova rastreável. Se não for possível reconciliar, gerar um novo
pacote em revisão disponível, com logs/manifests/recibos correspondentes, sem
reescrever a evidência antiga. A revisa a ligação geração → manifest → builds.
Não fechar #7 nem iniciar seu ensaio dependente #9 com essa divergência aberta.

### P1. Validar a revisão local — executor A, revisor C

1. Registrar HEAD/tree, estado da árvore, Python e compilador C efetivos.
2. Criar diretório exclusivo; compilar `main/observation_wire.c` como biblioteca
   compartilhada Linux, com C11, avisos e erros de compilação visíveis.
3. Definir `WASMHOME_WIRE_LIBRARY` para essa biblioteca real e executar a suíte
   `unittest discover -s tests -v`. Preservar saída completa e código de retorno.
4. Relatar testes executados, falhas, erros e skips. Conferir que os testes do
   codec C realmente rodaram; um resultado com skip não substitui essa prova.
5. Se falhar, classificar primeiro como ambiente, código ou especificação;
   vincular cada falha ao contrato, corrigir na frente apropriada e
   repetir os checks afetados. Atualização de código exige nova revisão.
6. C revisa comando, log, biblioteca, revisão testada e limites da conclusão.

Pendência concreta desta sessão: obter runner com PowerShell real (o CT107 tem
registro de versão/binário; falta verificá-lo nesta execução) ou disponibilizar
um pacote portátil verificado em diretório dedicado, e repetir o teste do frontend.
Não usar stub para fingir a execução. Para os três skips, conferir o gate CMake e
o runner C em ambiente suportado ou adaptar sua validação ao Linux com revisão;
a junção Windows exige prova nessa plataforma quando aplicável. Relatar
explicitamente quais critérios foram executados e quais continuam sem prova.

Saída: prova local reproduzível ligada a uma revisão. Isso não demonstra build
ESP-IDF, compatibilidade física WAMR, OTA, troca modular ou campanha.

### P2. Concluir o pacote de configurações — executor B, revisor A

1. Mapear cada critério da #7 às evidências de 12/10 e separar geração,
   repetição dos artefatos, builds de firmware e aceite.
2. Recuperar dos diretórios de build do CT107, para preservação privada, os
   `sdkconfig` completos A/B/C e
   os metadados necessários à conferência, sem usar configurações de outra árvore.
3. Conferir os hashes contra os recibos já preservados. Registrar origem,
   cenário, revisão e caminho. Diferença de hash exige investigação; não
   substituir o snapshot antigo para fazer o novo arquivo parecer correspondente.
4. Examinar valores sensíveis antes de publicar configurações. Se houver,
   preservar o original fora do pacote público e documentar separadamente a
   cópia sem segredos, seu hash e as alterações feitas.
5. A confronta cada configuração com cenário, clock, otimização, runtime,
   intérprete clássico, AOT validator e bounds checks exigidos.

Saída: configurações efetivas verificáveis, inventário e hashes. O pacote atual
já guarda hashes e resultados de validação, mas não os `sdkconfig` completos.
A preservação dos originais completos é privada; publicação deve respeitar os
limites registrados em `viabilidade-wamr.md`, inclusive para credenciais.

### P3. Resolver a confiança no compilador — executor B, revisor A

1. Conferir o recibo R5, hash do binário, pins de fontes/LLVM, flags, bibliotecas,
   comandos e compatibilidade host. Preservar R4 como tentativa incompatível.
2. Definir a evidência independente que ligará o binário R5 ao build declarado.
   Uma assinatura de um recibo não examinado e uma revisão de strings de versão
   não bastam. O responsável pelo atestado registra identidade, método, insumos,
   hashes, observações e limitações.
3. Usar os insumos reais verificáveis quando disponíveis; se a ligação continuar
   impossível de demonstrar, documentar a lacuna e preparar rebuild independente
   em diretório limpo, com capacidade e fontes revalidadas.
4. Na rota de reprodução exata, comparar o SHA-256 do `wamrc` reconstruído com
   o R5 realmente usado no CT107 e preservar comandos, ambiente e relatório do
   revisor. Hash diferente não comprova a proveniência exata do R5: investigar
   a diferença ou explicitar a conclusão alternativa (por exemplo, confiança em
   um novo compilador verificado, que exige regenerar/revalidar os artefatos).
   Outro método independente precisa demonstrar sua ligação aos bytes originais;
   mera revisão do recibo unsigned não a demonstra.
   Não transformar duas compilações LLVM em requisito novo obrigatório. A
   repetição já documentada R6/R7 é dos artefatos Wasm/AOT. Um rebuild independente,
   se necessário para a confiança, tem objetivo e recibo próprios.
5. A examina a associação declarada e sua evidência; divergências impedem aceite.
6. Só após P0/P1/P2/P3, conferir item a item o aceite #7, registrar resolução,
   sincronizar caixas/rótulos/mapa e liberar dependentes.

Saída: atestado independente suficiente ou bloqueio documentado; depois, decisão
explícita de aceite #7. Esta preparação documental não é esse atestado.

## Onda 2 — prova física e referência nativa

### P4. Comprovar A/B/C no ESP32-S3 (#9) — executor C, revisor B

Dependências: aceite #7 e plataforma #8. Participação humana na bancada.

1. Confirmar placa/revisão, flash, PSRAM, alimentação, sensor e loopback. Descobrir
   porta Linux e endpoints atuais; não reutilizar COM12 ou IP histórico sem consulta.
2. Identificar firmware atual e plano de preservação/restauração. Recuperar os
   ELF/BIN correspondentes aos hashes; registrar revisão, configuração e comandos.
3. Verificar previamente o instrumento: exige A/B/C presentes, checksum correto,
   sessões identificadas e registros de memória aplicáveis. Validar o parser
   contra logs de referência com checksum errado, cenário ausente, warmup inválido
   e trap; cada caso precisa ser identificado sem aceite inventado. O coletor
   CSV atual não é suficiente sozinho para afirmar esses critérios.
4. Executar A, B e C na mesma placa, com configurações isoladas. Guardar logs
   seriais originais completos desde o boot e erros de inicialização/carga/chamada.
5. Conferir cinco aquecimentos e 30 chamadas medidas, checksum `0xDBB2F0D4`,
   repetições 1–30, cenário e status. Essas chamadas pertencem a uma sessão de
   carga; não são 30 atualizações experimentais independentes.
6. Registrar bytes de heap interno livre e PSRAM livre antes do runtime, após
   inicialização, carga, instanciação, execução e liberação. Identificar API e
   capacidade consultada (`heap_caps_get_free_size` com capacidades explícitas),
   unidade, fase, cenário e firmware/hash; separar estimativas de custo, mínimo
   global de heap e pico por fase. Registrar memória estática do ELF separadamente.
   Pico sem instrumento ou campo inaplicável é `null` com motivo; heap livre
   interno não representa sozinho SRAM total ou PSRAM. Registrar os erros/limites.
   O CSV atual mede heap interno em algumas fases e não fecha sozinho a prova
   de PSRAM. Se for preciso instrumentar, revisar/testar e recompilar a árvore
   modificada antes de atribuir logs aos binários novos.
7. B revisa artefatos, configurações e registros físicos. Se AOT falhar, preservar
   a tentativa e bloquear dependentes #12/#14/#18; não substituir o cenário.
8. Restaurar o estado de bancada previsto e registrar seu resultado. Fechar #9
   somente com critérios físicos examinados e resolução ligada às evidências.

### P5. Demonstrar serviços nativos (#11) — executor A, revisor C

Dependências #8/#10 satisfeitas. Pode avançar em trilha independente da #9,
respeitando o acesso exclusivo à placa durante os ensaios.

1. Fazer preflight próprio da #11: placa, porta, rede, alimentação, firmware/hash,
   sensor real e loopback. Registrar plano de backup e restauração da sessão.
   Preparar oráculo e vetores com a decisão humana de histerese aprovada em 13/10.
   Decisão aprovada pelo usuário em 13/10/2026: banda total de 1 °C, limites
   ±0,5 °C, manter estado dentro da banda e estado inicial baixo.
2. Implementar telemetria DS18B20 e controle GPIO concorrentes no firmware
   monolítico, com versões v1/v2 e IDs/boots conforme o UDP v1. O aceite exige
   firmware dos serviços reais; fixture diagnóstica, microbenchmark E1 e
   `service_not_installed` não demonstram #11.
3. Validar entradas de fronteira, manutenção de estado na banda, limiares de
   25/30 °C e vetor que distingue v1/v2. Sob a decisão aprovada em 13/10, usar
   23/32/27 °C → v1 `0,1,1`, v2 `0,1,0`; fronteiras v1
   24/25,5/25/24,5 °C e v2 29/30,5/30/29,5 °C → `0,1,1,0`.
   Conferir os vetores contra a decisão aprovada. Conferir IDs/boots, versão, níveis GPIO
   solicitado/lido, estado e idade da leitura real. Republicar uma amostra não
   autoriza alterar sua temperatura sob o mesmo ID.
4. Preservar aquisição 500 ms e observação/estímulos 100 ms; timeout candidato
   300 ms até congelamento. Não inferir serviço funcional de heartbeat.
5. Guardar JSONL bruto, logs seriais, versão, config e hash do firmware.
6. C verifica os resultados contra o oráculo, nível solicitado/lido e continuidade
   dos dois serviços. Restaurar a bancada e registrar resultado. Resolver achados
   antes do aceite #11.
7. Na implementação sem OTA, manter no máximo 16 IDs de `UPDATE_TRIGGER` por
   boot para replay/conflito exatos; não substituir registros antigos. Quando o
   pool estiver cheio, UUID desconhecido não recebe `UPDATE_ACCEPTANCE`: o
   firmware registra `native_update_replay_capacity_full` no canal serial e
   descarta o pedido. UUID já registrado continua respondendo do cache ou com
   `ATTEMPT_CONFLICT`. Saturação é controle de admissão, não sucesso de OTA nem
   tentativa aceita; preservar evento serial, ausência de resposta e timeout no
   registro bruto sem inventar `ATTEMPT_CONFLICT` para UUID não admitido.

## Ondas posteriores — distribuição condicionada aos gates

As atribuições abaixo são a agenda; não são execução já iniciada. Reutilizar os
agentes após liberar suas frentes. Não executar mais de três trabalhos de agente
simultâneos nem permitir duas sessões disputando a mesma placa.

| Ordem | Issues | Executor | Revisor | Dependências | Saída que libera a próxima fase |
|---|---|---|---|---|---|
| 3A | #12 | C | B | #8/#9 | Bindings aprovados, instâncias separadas, memória e ciclo de vida |
| 3B | #15 | B | A | #4/#7; endpoint #6 | HTTPS/CA, envelopes assinados, hashes e rejeições reais |
| 4A | #13/#14 | C | B | #10/#12 | Mesmos dois serviços no intérprete clássico e AOT, provados na placa |
| 4B | #16 | A | C | #10/#11/#15 | OTA v1→v2, reboot, rollback e primeira resposta correta observados |
| 5A | #17/#18 | C | A | #10/#13 ou #14/#15 | Controle v2 ativado, telemetria contínua, mesma instância e boot |
| 5B | #22 | B | C | #5/#12/#15 | Injeção serial correlacionada ao UDP, rejeições/traps e recuperação |
| 6 | #19 | A | B | #16/#17/#18 | Métricas, relógios, memória, payload e denominadores validados |
| 7 | #20 | C | A | #19 | Cinco tentativas aceitas por cenário fora da amostra final; protocolo congelado |
| 8A | #21 | A | B | #20 | Campanha normal balanceada com falhas e timeouts preservados |
| 8B | #23 | C | B | #20/#22 | 70 falhas e dois controles, separados da campanha normal |
| 9 | #24 | B | A | #21/#23 | Análise reproduzível, tabelas/gráficos e pacote de dados/código |

Executor e revisor de uma entrega não atuam simultaneamente sobre seus arquivos.
As linhas compartilham agentes e placa: o coordenador agenda os turnos, e não
interpreta a tabela como autorização de executar tudo ao mesmo tempo.

## Decisões necessárias antes do piloto e da campanha

- Histerese aprovada pelo usuário em 13/10: banda total de 1 °C, limites ±0,5 °C,
  manter estado dentro da banda e estado inicial baixo. Validar os vetores na #11.
- Pré-registrar regras para timeout comum, prazo de atualização, janelas,
  aquecimento e invalidação instrumental antes de usar os dados do piloto.
- Resolver alvo de 90 oportunidades programadas versus 90 tentativas aceitas,
  com agenda/reposição e denominadores coerentes antes de #20/#21.
- Congelar protocolo, ordem/seed, tamanho final e critérios depois do piloto.
- Preservar recusas e ausência de aceite como resultados de disparo; UUID sozinho
  não inicia tentativa. Manter falhas/timeouts posteriores ao aceite no denominador.
- Medir intervalos que atravessam reboot pelo relógio monotônico do observador;
  campo inaplicável é `null`/`not_applicable`, não zero.

## Evidências desta preparação

Os relatórios de agentes e as revisões independentes estão preservados em
[evidence/proximos-passos-2026-10-13](evidence/proximos-passos-2026-10-13/).
O resultado inicial foi superado pelas execuções e revisões registradas no
[avanço de 13/10](evidence/avanco-e1-2026-10-13/README.md). PowerShell portátil
foi recuperado e verificado, o codec C real foi compilado e a suíte passou.
Os `sdkconfig` completos foram preservados fora do repositório. P0 reconciliou
os snapshots históricos, preservou suas lacunas e repetiu geração e build B
em diretórios novos. P3 concluiu a [reconstrução comparativa revisada](evidence/compiler-rebuild-2026-10-13/README.md): binário R5 reproduzido após normalização explícita de caminho, 98 archives LLVM e AOT de referência iguais.
A #11 recebeu implementação nativa e testes de host; build ESP-IDF e prova
na placa continuam necessários. A histerese e a fiação foram confirmadas pelo
usuário, que também preencheu a configuração privada de rede.
O plano detalhado de coleta/análise e o checklist E0–E7 continuam sendo as fontes
para os contratos completos; esta distribuição não altera seus critérios.

O [fix do adapter nativo](evidence/native-adapter-fix-2026-10-13/README.md) corrige uma chamada incompleta detectada no build ESP-IDF real; o novo teste compila o ramo ESP_PLATFORM inteiro. Serviços v1/v2 precisam de builds corrigidos antes da placa.

## Gate #7 concluído em 13/10

A/B/C foram compilados/validados em 91f11f7. A repetição de geração registrou
argv antes da execução e reproduziu os cinco payloads; disassembly nativo/AOT
e configurações foram conferidos. A [revisão independente](evidence/gate7-builds-2026-10-13/independent-review.md) aceitou o gate de toolchain.
Os serviços v1/v2 também foram compilados em 7b00b15, após corrigir o adapter.
As provas físicas #9/#11 continuam necessárias; os status dos recibos
conservam `build_checked_not_device_validated`.

# Planejamento detalhado para coleta e análise

Status: plano operacional complementar ao [plano de execução](plano-de-execucao.md).
Redigido em 09/10/2026; estado de execução atualizado em 11/10/2026. Este documento especifica trabalho futuro, sem comprovar campanha ou análise executada.

O plano de execução e o glossário CONTEXT.md definem o escopo científico. Este
documento organiza o trabalho restante, os critérios para iniciar cada fase e
os dados que precisam existir para que a coleta possa ser refeita e analisada.
Se uma especificação deste documento conflitar com o protocolo v1, o plano de
execução ou uma decisão aprovada no mapa Wayfinder, a decisão aprovada prevalece.

## Resultado esperado

Entregar uma comparação reproduzível de três atualizações da mesma aplicação no
mesmo ESP32-S3:

| Cenário | Execução | Atualização medida |
|---|---|---|
| A | C nativo sobre ESP-IDF e FreeRTOS | Imagem da aplicação por OTA e reinicialização |
| B | Interpretador clássico WAMR | Download e troca do módulo de controle |
| C | WAMR AOT para Xtensa | Download e troca do módulo de controle AOT |

O serviço de telemetria DS18B20 existe nos três cenários; em B/C permanece ativo durante a troca do controle. Em A, a OTA reinicia a aplicação e interrompe os dois serviços. O módulo de
controle recebe os mesmos estímulos, aplica as versões v1 e v2 e publica
resultados comparáveis. Cada bloco contém um slot planejado para cada cenário.
Uma atualização normal começa somente quando o observador recebe `ACCEPTED`
correlacionado ao `UPDATE_TRIGGER`, conforme o contrato UDP v1.

Este documento não adiciona sensores além do DS18B20, dispositivos, workloads ou
métricas à campanha aprovada. Atualizações de firmware, resultados de execução,
calibração do observador e resultados de campanha são evidências distintas.

## Unidades e denominadores

### Decisão de amostragem ainda pendente

A referência vigente do checklist é 30 tentativas por cenário, 90 no total;
o contrato inicia uma tentativa normal somente após `ACCEPTED` correlacionado.
A agenda de 30 blocos/90 slots descrita neste detalhamento é uma proposta
operacional e pode produzir menos de 90 tentativas aceitas. Ela não substitui
automaticamente aquela referência. Antes de congelar #20, decidir e registrar
se o alvo definitivo será de slots ou de tentativas aceitas, com regra de
agenda/reposição coerente e denominadores separados. Não iniciar #21 com essa
decisão aberta. Recusas, timeouts e falhas continuam preservados em qualquer
alternativa; nenhum disparo ambíguo recebe retransmissão automática.

Até essa decisão, as definições por slot abaixo servem para o desenho do
instrumento, não como autorização de alterar o tamanho da campanha.

`slot_id` identifica uma oportunidade planejada por combinação de `block_id` e
cenário. A agenda tem um slot por cenário em cada bloco. O observador atribui um
`attempt_id` UUID ao `UPDATE_TRIGGER` antes de enviá-lo, pois o protocolo v1 exige
essa correlação desde o primeiro datagrama. A existência desse UUID não inicia,
por si só, uma tentativa normal. Ela começa apenas com o aceite correlacionado
recebido dentro do timeout de aceite.

Relatar separadamente:

- **Conclusão por slot planejado:** slots que receberam aceite pontual e chegaram
  à primeira resposta v2 correta dentro do prazo total, divididos pelo total de
  slots planejados. Uma recusa ou falta de aceite impede a conclusão do slot, mas
  não é chamada de falha de uma atualização aceita.
- **Sucesso por atualização aceita:** atualizações com primeira resposta v2
  correta, divididas pelas tentativas aceitas com instrumentação válida.
- **Resultado do disparo:** aceites, recusas, ausência de aceite e disparos não
  iniciados, contados por slot. Uma ativação observada depois de um aceite
  tardio fica registrada, mas não passa a ser uma tentativa normal v1.

O primeiro denominador preserva o resultado operacional dos 3N slots
planejados. O segundo mede o caminho da atualização depois do aceite. Nenhum
deles substitui o outro. Reposição instrumental não cria slot adicional; fica
ligada ao slot original e não apaga o primeiro registro.

## Posição atual e dependências

Estado reconfirmado em 11/10/2026 no [mapa Wayfinder #1](https://github.com/haroldocurti/wasmhome/issues/1) e nas issues:

- #8 e #10 estão integradas na `main`; a #10 entrou pela PR #28.
- #6 foi encerrada após a PR #29, com reconstrução no CT109, rede e reserva DHCP registradas.
- #7 permanece aberta. CT107 tem SDKs/venv preparados e build nativo real; LLVM/wamrc e geração reproduzida A/B/C ainda faltam.
- #9 permanece aberta e exige participação humana na prova B/C no ESP32-S3.
- #11–#24 continuam abertas. O serviço nativo #11 pode avançar pelas dependências #8/#10 cumpridas; não precisa aguardar #9.
- O usuário optou por iniciar o PC pelo Linux para o doador. CT110 não foi criado e nenhum guest foi desligado. O [registro de retomada](retomada-linux.md) contém as provas e os próximos passos.

### Ordem crítica

Integração #10 concluída → serviço nativo #11.

LXC #6 concluído → concluir toolchain #7 → prova interpretada e AOT #9 → firmware modular
#12 → demonstrações #13 e #14.

Envelope #4 e toolchain #7 → distribuição HTTPS #15.

#11 e #15 → OTA nativo #16. #13 e #15 → troca interpretada #17.
#14 e #15 → troca AOT #18.

Concluir #16, #17 e #18 → métricas e validador #19 → piloto e congelamento #20
→ campanha normal #21.

Firmware modular #12 e distribuição #15 → mecanismos de resiliência #22.
Piloto #20 e #22 → campanha complementar #23.
Campanhas #21 e #23 → análise reproduzível #24.

As trilhas A, B e C podem avançar separadamente depois das dependências indicadas.
O risco AOT precisa ser resolvido cedo: se a #9 não passar, interromper a
construção que depende de AOT, registrar as tentativas e levar a decisão ao grupo
e ao orientador. Não trocar o cenário silenciosamente.

### Pacotes de trabalho

| Pacote | Issues | Entrega para liberar a etapa seguinte |
|---|---|---|
| G0. Integrar o observador | [#10](https://github.com/haroldocurti/wasmhome/issues/10) | PR revisado e árvore integrada à main; preservar os registros de calibração e documentar os limites |
| G1. Fechar a bancada de build | [#6](https://github.com/haroldocurti/wasmhome/issues/6), [#7](https://github.com/haroldocurti/wasmhome/issues/7) | Rotas LXC, ESP32 e observador provadas; endpoint estável; CT reconstruído; três artefatos reproduzidos com versões, configurações e hashes |
| G2. Reduzir o risco WAMR | [#9](https://github.com/haroldocurti/wasmhome/issues/9) | Mesmo workload passa no interpretador clássico e no AOT da placa, com checksum, configuração e compatibilidade registrados |
| G3. Fechar o serviço nativo | [#11](https://github.com/haroldocurti/wasmhome/issues/11) | Telemetria e controle concorrentes; vetor funcional completo; GPIO físico verificado pelo loopback |
| G4. Fechar execução modular | [#12](https://github.com/haroldocurti/wasmhome/issues/12), [#13](https://github.com/haroldocurti/wasmhome/issues/13), [#14](https://github.com/haroldocurti/wasmhome/issues/14) | Telemetria estável; módulos de controle interpretado e AOT executam separadamente; memória e ciclo de vida registrados |
| G5. Distribuir artefatos | [#15](https://github.com/haroldocurti/wasmhome/issues/15) | Manifestos autenticados e artefatos servidos por HTTPS; aceitação e rejeições verificadas |
| G6. Fazer as atualizações medidas | [#16](https://github.com/haroldocurti/wasmhome/issues/16), [#17](https://github.com/haroldocurti/wasmhome/issues/17), [#18](https://github.com/haroldocurti/wasmhome/issues/18) | Uma atualização completa e observável por cenário, incluindo falhas, boot, primeira resposta correta e payload |
| G7. Fechar medidas e coleta | [#19](https://github.com/haroldocurti/wasmhome/issues/19), [#20](https://github.com/haroldocurti/wasmhome/issues/20) | Métricas validadas; piloto executado; protocolo, ordem, janelas, timeouts e tamanho final congelados |
| G8. Produzir evidência final | [#21](https://github.com/haroldocurti/wasmhome/issues/21), [#22](https://github.com/haroldocurti/wasmhome/issues/22), [#23](https://github.com/haroldocurti/wasmhome/issues/23), [#24](https://github.com/haroldocurti/wasmhome/issues/24) | Campanhas normal e complementar separadas, seguidas de tabelas, gráficos e pacote reproduzível |

Na execução por uma pessoa, iniciar cedo a cadeia #6 → #7 e agendar desde já a
participação humana da #9. Com a #10 integrada, em uma sessão própria a #11
pode avançar assim que a integração e a decisão de histerese estiverem resolvidas,
sem esperar a cadeia LXC. Depois de #7 e #4, a #15 pode avançar sem esperar #9,
#12, #13 ou #14. A #16 começa quando #11 e #15 estiverem concluídas.

Seguir a rota Wayfinder: assumir uma issue antes de começar e manter uma issue
decisória em foco por sessão. Ao concluir, publicar evidência ligada aos critérios
de aceite, registrar a resolução, encerrar a issue e atualizar o mapa pai antes de
iniciar uma dependente. Se uma espera externa bloquear a issue, registrar o
bloqueio e encerrar essa sessão. Uma issue independente pode começar em outra
sessão; não iniciar dependentes antes de cumprir os critérios de aceite.

## Decisões a congelar antes da campanha

Registrar cada valor decidido em uma revisão identificada do protocolo de
campanha. O piloto serve para resolver estes parâmetros, não para produzir os
resultados definitivos.

| Decisão | Estado | Critério de resolução |
|---|---|---|
| Semântica exata da histerese | Pendente | Definir limiar de ligar, limiar de desligar, estado inicial e resultados esperados para cada entrada |
| Regra de escolha do timeout funcional | Pendente antes do piloto | Fixar antes de ver os resultados do piloto como a latência de baseline e a taxa máxima de falsos timeouts determinam o valor comum; não escolher um timeout por cenário |
| Timeout de resposta funcional | Candidato de 300 ms | Aplicar a regra pré-registrada aos dados do piloto; congelar um valor para as campanhas normal e de resiliência |
| Prazo total da atualização | Pendente | Medir os caminhos nativo, interpretado e AOT no piloto; fixar um prazo operacional comum e classificar sua expiração como falha |
| Janela de estabilização e observação | Pendente | Fixar duração de aquecimento, número mínimo de respostas válidas antes da atualização e janela posterior |
| Regra para tamanho amostral | Pendente antes do piloto | Registrar diferença mínima de interesse, variância assumida e método antes do piloto; cinco observações por cenário não sustentam cálculo preciso de poder |
| Tamanho amostral definitivo | Referência de 30 tentativas por cenário; agenda por slots proposta | Resolver primeiro o alvo de slots versus tentativas aceitas. Justificar e congelar tamanho/agenda antes da campanha; sem base independente, não alegar poder ou equivalência a partir do piloto |
| Alinhamento das issues #20/#21 | Pendente antes do congelamento | O piloto precisa de cinco tentativas normais aceitas por cenário; a campanha definitiva agenda N slots por cenário. Preservar recusas e timeouts pré-aceite como resultados de slot, sem chamá-los de tentativas aceitas |
| Ordem da campanha | Referência congelável | Gerar uma lista explícita de blocos, balancear as seis permutações e preservar algoritmo, semente e arquivo final |
| Critério de invalidação instrumental | Pendente | Definir causas observáveis e reproduzíveis antes da coleta; nunca invalidar por resultado desfavorável |

### Regra de controle proposta para fechar a histerese

O glossário fixa limiares de 25 °C para v1 e 30 °C para v2, com histerese de
1 °C, mas ainda não define a largura exata da banda. Proposta para confirmação
antes da issue #11: interpretar 1 °C como a largura total da banda, ligar em
limiar + 0,5 °C, desligar em limiar - 0,5 °C e manter o estado anterior dentro
da banda. Inicializar GPIO em nível baixo. A implementação e o oráculo de
referência devem usar a mesma definição.

Com essa interpretação e estado inicial baixo, o vetor de fronteira para v1 é
24 °C, 25,5 °C, 25 °C, 24,5 °C, com estados esperados 0, 1, 1, 0. Para v2,
29 °C, 30,5 °C, 30 °C, 29,5 °C deve produzir 0, 1, 1, 0. Incluir ambos os
vetores nos testes da #11. O estímulo periódico de 23 °C, 32 °C e 27 °C
continua útil na coleta. Ele confirma o estado baixo, o estado alto e
diferencia v1 de v2 em 27 °C: sob estas regras, v1 produz 0, 1, 1 e v2 produz
0, 1, 0. O estímulo não substitui os testes de fronteira.

## Especificação da coleta normal

Cada `UPDATE_TRIGGER` usa um `attempt_id` UUID novo. O observador cria o
identificador e o registra antes do envio; o ESP32 não o cria nem altera. O
`slot_id` permanece estável quando uma reposição instrumental comprovada exige
outro disparo com novo `attempt_id`.

### Pré-condições

Antes de admitir uma tentativa:

1. Confirmar placa, alimentação, porta serial, rede, endpoint do observador e
   endpoint HTTPS. Registrar mudanças desde a sessão anterior.
2. Registrar revisão Git, versões de toolchain, sdkconfig de cada cenário,
   mapa de partições e hashes dos binários e módulos.
3. Confirmar que o artefato v1 correto está ativo e que a sequência antirretorno
   está registrada. Preparar v1 com sequência válida nova; nunca zerar o piso
   NVS nem desativar a verificação.
4. Confirmar boot_id atual, amostras válidas do DS18B20 e estado do GPIO pelo
   loopback.
5. Iniciar observação funcional a cada 100 ms. A aquisição DS18B20 permanece
   a cada 500 ms. O limite inicial para idade de amostra é 1000 ms.
6. Completar a estabilização definida no protocolo congelado e recolher a
   linha de base. Exigir pelo menos 30 respostas corretas do controle e 30
   mensagens de telemetria válidas antes do disparo.

As 30 respostas são uma verificação de prontidão, não 30 tentativas
experimentais. Aquecimento ocorre antes do disparo medido. Uma espera depois de
uma atualização automática ao boot não aquece o serviço que já foi medido.

### Sequência de execução

1. Gerar e validar a agenda congelada. Restaurar v1 e aguardar a estabilidade
   sem incluir esse preparo na janela medida.
2. Registrar attempt_id, block_id, sessão, cenário, ordem no bloco, boot_id,
   sequência atual e hashes dos artefatos esperados.
3. Enviar um único UPDATE_TRIGGER para v2. Guardar o instante de envio no
   relógio monotônico do observador e o datagrama bruto.
4. Registrar `UPDATE_ACCEPTANCE` como um marco separado e medir envio–aceite.
   `ACCEPTED` recebido dentro do prazo inicia a tentativa normal. `REJECTED` e
   ausência de aceite são resultados do disparo do slot, não resultados de uma
   atualização aceita. Classificar causa instrumental somente com evidência
   prevista no protocolo. Um aceite tardio fica registrado e não inicia uma
   tentativa normal. Não reenviar automaticamente nem enquanto o estado do
   dispositivo estiver ambíguo.
5. Continuar estímulos de controle e captura de telemetria durante download,
   validação, troca ou reboot. Não preencher perdas com retransmissões.
6. Encerrar o prazo total na primeira resposta de controle v2 correta, recebida
   dentro do timeout funcional e do prazo total congelado. Guardar envio, aceite,
   ativação observada e primeira resposta correta. Calcular tanto envio→resposta
   quanto aceite→resposta; a primeira métrica inclui o despacho e a segunda mede
   a atualização aceita.
7. Continuar pela janela posterior congelada. Exigir respostas corretas dos
   serviços, verificar boot esperado, versão, amostra e GPIO solicitado e
   observado.
8. Salvar todos os fluxos brutos, resumo derivado, motivo terminal e hashes.
   Preparar v1 para o próximo slot com uma sequência antirretorno ainda maior.

O timeout de 300 ms é o limite inicial por resposta funcional e por mensagem de
aceite. Ele não é um prazo total da atualização. A campanha normal precisa de
um prazo total congelado após o piloto. O limite operacional de 30 segundos
documentado para recuperação pertence à bateria de resiliência e não deve ser
reutilizado automaticamente para a atualização normal.

### Resultado do slot e da tentativa aceita

Cada slot preserva despacho, validade instrumental, resultado da tentativa
aceita e resultado da janela posterior em campos separados. `slot_success` é um
campo derivado. Isso evita forçar resultados diferentes em uma única enumeração.

| Campo | Valores e regra |
|---|---|
| `dispatch_status` | Por disparo: `not_sent`, `accepted`, `rejected` ou `acceptance_timeout`; aceitar exige resposta correlacionada dentro do timeout congelado |
| `accepted_attempt_status` | `not_applicable` sem aceite pontual; `not_evaluable` se a instrumentação da tentativa aceita for invalidada; nos demais casos, exatamente um entre `success`, `recovery_failure`, `incorrect_result` e `update_timeout` |
| `instrument_status` | Por disparo/tentativa: `valid`, `invalid` ou `unresolved`, com motivo e evidência; ausência de resposta, sozinha, não comprova falha do instrumento |
| `post_window_status` | `not_applicable`, `stable`, `service_failure` ou `incomplete`, medido após o primeiro sucesso; não altera o resultado terminal da atualização |

Classificar `accepted_attempt_status` ao fim do prazo total, nesta ordem:

1. `success` se chegar resposta v2 correta dentro do prazo total e do timeout
   funcional, com boot e GPIO esperados;
2. `recovery_failure` se evidência do dispositivo mostrar que ele não voltou ao
   estado operacional exigido até o prazo total;
3. `incorrect_result` se o serviço respondeu, permaneceu operacional, mas as
   respostas correlacionadas não satisfizeram versão, resultado ou GPIO;
4. `update_timeout` nos demais casos sem resposta v2 correta até o prazo.

`slot_success` é verdadeiro quando um disparo válido foi aceito pontualmente e a
tentativa normal instrumentalmente válida terminou em `success`. Se uma tentativa
for invalidada por falha instrumental comprovada, ela permanece arquivada. Uma
reposição válida, ligada ao mesmo slot, determina `slot_success`. A taxa por
slot usa todos os slots planejados. A taxa por atualização aceita usa somente
tentativas aceitas com instrumentação válida; as tentativas instrumentais
inválidas permanecem no registro e na contagem de invalidações. Se não houver
reposição válida, o slot não conclui; a causa instrumental fica identificada e
não é atribuída ao firmware. Recusa válida,
timeout de aceite e falha de atualização continuam visíveis nos seus
denominadores correspondentes.
Reposições só substituem slots
com `instrument_status=invalid` ou `not_sent` por causa externa comprovada; ficam
ligadas ao `slot_id` original e não aumentam N. Não repor falha válida do
dispositivo nem repetir seletivamente resultados desfavoráveis.

## Desenho da campanha

### Piloto

Executar pelo menos cinco tentativas normais aceitas por cenário, total mínimo
de 15. Cada uma começa com `ACCEPTED` pontual e termina com estado terminal
registrado, seja sucesso ou falha após aceite. O piloto testa o procedimento, a
estabilidade do equipamento, a coleta de eventos, os timeouts, o esforço de
restauração e a completude do pacote. Recusas, timeouts e disparos sem aceite
ficam registrados à parte e não contam entre as cinco tentativas. Só iniciar
outro slot de piloto depois de consultar o estado do dispositivo e comprovar que
é seguro. Guardar o piloto em campanha distinta e não combinar seus dados com os
da campanha definitiva.

Aplicar as regras de timeout e tamanho amostral registradas antes do piloto.
Congelar N, a agenda e os limites antes de iniciar a campanha normal. N=30 é a
referência por viabilidade, não uma conclusão de poder. Se a evidência não
sustentar mudança, manter 30 blocos e tratar os intervalos como estimativas
descritivas, sem alegar poder confirmatório.

Cinco tentativas aceitas por cenário podem revelar falhas de procedimento, mas
não sustentam uma estimativa precisa de poder. O cálculo de N deve declarar a
diferença mínima de interesse, as hipóteses de variância e as limitações das
cinco observações de piloto. Slots adicionais podem ser necessários para obter
as cinco tentativas aceitas se algum dispatch não receber aceite.

### Campanha normal

Com N=30, executar 30 blocos na mesma placa. Cada bloco tem um slot A, um B e um
C, totalizando 90 slots planejados. Cada uma das seis ordens aparece cinco vezes.
Se N mudar, gerar 3N slots e distribuir as seis ordens com contagens que diferem
no máximo em uma. Embaralhar a lista com semente registrada e preservar a agenda
gerada. A lista congelada, não a semente isolada, é a fonte da ordem executada.

Manter todos os slots no denominador de conclusão por slot. Informar recusas e
timeouts de aceite como resultados de despacho, e falhas e timeouts após aceite
como resultados de tentativas normais. Não repetir seletivamente sucesso ou
falha. Reposição instrumental usa novo `attempt_id`, liga ao `slot_id` original
e não altera a quantidade planejada.

### Campanha de resiliência

Manter campanha, IDs, resumos e análise separados da campanha normal. A matriz
aprovada contém 70 tentativas de falha e dois controles. Cada injeção deve ser
precedida e seguida por pelo menos 30 respostas válidas de cada serviço
aplicável. A issue #22 implementa injeção e recuperação; a #23 executa a matriz
após o protocolo de timeout ser congelado na #20. Uma falha após `fault_accepted`
permanece no denominador da célula. Se faltar `fault_accepted`, marcar o disparo
como `unconfirmed`, preservar o pedido e não inferir rejeição nem falha do
dispositivo só pelo silêncio. Só iniciar reposição depois de consultar estado e
provar que é seguro; a tentativa sem confirmação permanece em um registro de
disparos não confirmados, separada das 70 injeções aceitas.

O limite de recuperação de 30 segundos começa no timestamp monotônico local do
observador ao receber `fault_accepted`. Termina na primeira resposta correta de
todos os serviços exigidos pelo caso. Para `WASM-OOB` e `WASM-DIV0`, o controle
deve voltar e a telemetria deve permanecer disponível. Para `NAT-ABORT`, os
serviços de telemetria e controle precisam responder depois do novo boot. Usar
somente timestamps de recepção do observador para esse intervalo; tempo do
dispositivo fica em campo separado. Sem `fault_accepted`, não há início de
cronômetro de recuperação.

## Contrato dos dados

### Automação mínima antes do piloto

A issue #19 deve entregar, ou apontar claramente para, quatro operações
reproduzíveis:

1. plan: validar metadados, gerar schedule.json e mostrar o balanceamento sem
   conectar ou enviar comando ao ESP32;
2. preflight: conferir revisão, hashes, porta serial, endpoints, boot, versão
   ativa, conectividade e espaço disponível para logs; falhar antes de medir se
   algum item divergir;
3. run-slot: executar um slot, criar um attempt_id novo por disparo e registrar
   eventos UDP, serial e servidor; nunca reenviar automaticamente um disparo
   ambíguo; exigir resultado de despacho e, se houver aceite, estado terminal da
   atualização;
4. analyze: validar o pacote arquivado e reconstruir arquivos processados e
   gráficos sem modificar dados brutos.

Plan e preflight precisam de modo sem hardware. Run-slot nunca reenvia um
UPDATE_TRIGGER automaticamente depois de timeout ou resultado ambíguo. O
operador consulta o estado do dispositivo e decide como registrar e preparar
uma reposição. Resume só continua depois de classificar o disparo e, se houve
aceite, a tentativa anterior; exige também comprovação da preparação e do estado
atual do dispositivo.

Executar cada bloco A/B/C dentro da mesma sessão física sempre que possível.
Isso reduz deriva de temperatura, rede e alimentação dentro do bloco. Manter a
ordem balanceada para distribuir a deriva entre cenários. Se uma sessão parar,
registrar session_id e motivo; não reiniciar ou reordenar silenciosamente os
slots restantes.

### Pacote por campanha

Guardar os arquivos com esta separação:

| Caminho | Conteúdo |
|---|---|
| campaign.json | ID, versão do esquema, revisão Git, protocolo congelado, N, bancada, toolchains, configurações, cadências, timeouts e hashes |
| schedule.json | N, seed, método de geração, seis permutações e exatamente 3N slots planejados em ordem |
| artifacts.json | Cenário, versão, tamanho, SHA-256, assinatura, sequência, toolchain e referência de armazenamento |
| raw/slots/<slot_id>/slot.json | Slot, sessão, tentativas de disparo associadas, estado de despacho, validade instrumental, atualização aceita, resultado do slot e reposições |
| raw/slots/<slot_id>/observations.jsonl | Eventos do observador e datagramas brutos para o slot, com timestamps locais, slot_id, attempt_id quando aplicável e decodificação |
| raw/slots/<slot_id>/device.jsonl | Linhas seriais brutas do firmware para o slot, preservadas sem reescrita |
| raw/slots/<slot_id>/server.jsonl | Eventos do servidor de artefatos pertinentes ao slot |
| raw/attempts/<attempt_id>/attempt.json | Uma atualização normal aceita, com métricas, resultado da atualização e estado da janela posterior |
| processed/dispatches.csv | Uma linha por UPDATE_TRIGGER, com slot_id, attempt_id, dispatch_status e instrument_status |
| processed/slots.csv | Uma linha por slot planejado, com slot_success, tentativas vinculadas e reposição |
| processed/attempts.csv | Uma linha por tentativa normal aceita, com accepted_attempt_status e métricas disponíveis |
| processed/controls.csv | Uma linha por estímulo de controle, incluindo timeout, atraso, resultado e versão |
| processed/blocks.csv | Resultados pareados por bloco e cenário |
| analysis.json | Estatísticas, versões de scripts, parâmetros, seeds e hashes dos insumos |
| figures/ | Gráficos gerados pelos scripts a partir dos arquivos processados |
| SHA256SUMS | Hashes dos dados brutos, agenda, scripts e saídas derivadas |

O pacote não contém chaves privadas, senhas, tokens, configurações secretas ou
material de acesso. Endpoints sensíveis usam apelidos; publicar apenas os
detalhes necessários para reproduzir o teste.

### Campos mínimos

campaign.json registra:

- schema_version, campaign_id, protocol_revision, git_commit, N e data UTC;
- códigos de cenário do protocolo: 1=native, 2=interp e 3=aot;
- modelo/revisão da placa, flash, PSRAM, alimentação, porta serial por apelido
  e versão do firmware de cada cenário;
- sistema do observador, versão do Python, porta/rede por apelido e condições
  ambientais registradas;
- ESP-IDF, WAMR, WASI-SDK, wamrc, compiladores, flags, arquivos sdkconfig e
  respectivos hashes;
- período de observação, timeout funcional, timeout total congelado, idade
  máxima do sensor, estabilização e janelas;
- caminho e hash do schedule.json e artifacts.json.

`slot.json` registra:

- campaign_id, slot_id, block_id, cenário, ordem no bloco e session_id;
- uma lista ordenada `dispatches[]`, cada item com `attempt_id`,
  `dispatch_status`, `late_acceptance_observed` e `instrument_status`, a lista de
  `accepted_attempt_ids`, o `valid_attempt_id` usado para calcular o resultado do slot,
  `slot_success` e ligação com eventual reposição;
- evidência e motivo para `not_sent` ou invalidação instrumental.

`attempt.json` registra somente uma atualização normal iniciada por aceite:

- campaign_id, slot_id, block_id, attempt_id, cenário, posição no bloco e sessão;
- versão ativa inicial, versão alvo, sequence antirretorno, hashes v1/v2 e
  boot_id inicial;
- instantes de envio e aceite, primeira resposta v2, fim da janela, envio→resposta
  e aceite→resposta;
- accepted_attempt_status, instrument_status, reason e resultado da janela
  posterior.

`slot.json` conserva pedidos rejeitados ou sem aceite, sem chamá-los de tentativas
de atualização. O resultado da janela posterior permanece separado: uma falha
depois da primeira resposta v2 correta não apaga o sucesso da atualização nem
esconde a perda de serviço posterior.

Cada linha do JSONL do observador registra schema_version, campaign_id,
slot_id, block_id, attempt_id quando existente, kind e host_monotonic_ns. Guardar
elapsed_ns desde o envio do gatilho e, após aceite,
elapsed_since_acceptance_ns. Eventos de rede preservam direção, peer por
apelido, pacote hexadecimal integral e conteúdo decodificado. Quando houver
timestamp do dispositivo, registrar device_monotonic_us e boot_id em campos
separados.
Capturas seriais preservam a linha original além de qualquer evento normalizado.

Não subtrair o relógio do ESP32 do relógio do observador. Para tempos que cruzam
reboot, usar exclusivamente o relógio monotônico local do observador. Métricas
locais do ESP32 usam apenas pares de timestamps do mesmo boot.

## Métricas e análise

### Métricas primárias

| Métrica | Cálculo | Como reportar |
|---|---|---|
| Conclusão por slot | Slot aceito pontualmente e com primeira resposta v2 correta no prazo total | Numerador sobre slots planejados; reportar despacho não aceito em separado |
| Sucesso após aceite | Primeira resposta v2 correta no prazo por tentativa normal aceita e instrumentalmente válida | Numerador e denominador de tentativas aceitas válidas; contar invalidações em separado |
| Tempo envio→resposta | Primeira resposta v2 correta menos envio do UPDATE_TRIGGER | Mediana, IQR e distribuição entre slots concluídos; inclui o despacho |
| Duração após aceite | Primeira resposta v2 correta menos recebimento de UPDATE_ACCEPTANCE | Mediana, IQR e distribuição entre atualizações aceitas que concluíram |
| Lacuna entre respostas | Maior intervalo entre respostas de controle corretas na janela | Reportar junto com controle_tail_censored_ns; separar lacuna de telemetria |
| Cauda censurada | Tempo da última resposta correta até o fim da janela | Limite inferior de interrupção ainda aberta; com zero respostas, registrar null e a janela sem alegar duração física |
| Indisponibilidade estimada | max(0, maior lacuna entre respostas - período nominal) | Estimativa limitada à cadência de 100 ms; não inclui sozinha a cauda censurada |
| Perda de controle | IDs enviados sem resposta correta até o timeout funcional | Quantidade, taxa e IDs; respostas tardias ficam em categoria separada |
| Telemetria | Respostas válidas, idade da leitura, IDs e maior lacuna por boot | Não inferir perda de leitura física a partir de perda UDP |
| Payload | Bytes do artefato e bytes observados no transporte, se capturados | Separar tamanho do artefato de tráfego com cabeçalhos/retransmissões |
| Etapas locais | Download, validação, escrita, carga, instanciação e ativação | Medir no relógio do componente e dentro do mesmo boot |
| Memória | SRAM e PSRAM antes do runtime, após inicialização, durante troca e após liberação | Reportar valores, picos e fonte; null/not_applicable quando não medido |

O primeiro marco de resposta v2, o aceite, o novo boot, as perdas e a recuperação
ficam disponíveis como tempos separados. O observador preserva
`control_tail_censored_ns`; derivar também a cauda da telemetria pelos limites da
janela. Com zero respostas corretas, guardar zero respostas, janela observada e
perdas, mas deixar a duração física como `null`. O timeout por mensagem não
substitui o prazo total do slot. Campos não aplicáveis recebem `null` e um
motivo; nunca zero.

### Plano estatístico

1. Contar slots planejados, resultados de despacho, tentativas aceitas e estados
   de atualização por cenário. Publicar tanto a conclusão por slot quanto o
   sucesso condicional ao aceite e à validade instrumental, cada um com seu
   numerador e denominador; contar invalidações em separado.
2. Calcular intervalos marginais de 95% das proporções por cenário pelo método
   score de Wilson, inclusive quando a taxa observada for 0% ou 100%. Rotular os
   intervalos como marginais, não como prova de equivalência.
3. Comparar primeiro `slot_success` de forma pareada por bloco, com o intervalo
   score assintótico para a diferença de proporções binárias pareadas, conforme
   [Fagerland, Lydersen e Laake (2014)](https://doi.org/10.1002/sim.6148).
   Publicar as quatro contagens do quadro pareado (`11`, `10`, `01`, `00`),
   indicando qual cenário ocupa cada posição, e a diferença estimada. Comparar
   sucesso após aceite separadamente, apenas nos blocos com tentativas aceitas e
   instrumentalmente válidas nos dois cenários. Declarar as comparações como
   exploratórias e indicar que os ICs por contraste não têm ajuste familiar.
   Intervalo que inclua zero ou ausência de pares discordantes não prova
   equivalência.
4. Registrar `session_id` em todo bloco. Quando mais de um bloco ocorrer na
   mesma sessão, usar a sessão como unidade de reamostragem para uma análise de
   sensibilidade por bootstrap, mantendo juntos os três cenários e todos os
   blocos daquela sessão. Aplicar a análise de sensibilidade às taxas binárias e
   contínuas. Registrar método, número de sessões, reamostragens e seed. Definir
   na #19 o mínimo de sessões necessário para reportar incerteza
   entre sessões. Se o mínimo não for atingido, declarar que a comparação é
   condicional às sessões observadas.
5. Para tempos de atualização bem-sucedidos, reportar mediana, intervalo
   interquartil, distribuição e número de sucessos. Calcular diferenças apenas
   nos pares completos, indicar quantos entraram e descrevê-las como condicionais
   ao sucesso. Não transformar timeout em zero nem excluí-lo do quadro de slots.
6. Apresentar payload, lacunas, perdas, memória e tempos por etapa como métricas
   separadas. Não misturar métricas da campanha de resiliência à análise normal.
7. Tratar chamadas repetidas dentro do mesmo boot como medidas internas. Elas
   não aumentam o número de atualizações independentes.
8. Limitar conclusões à placa, rede, versões, carga e condições medidas. Uma
   única placa não sustenta generalização para frotas ou sistemas críticos.

Gerar tabelas e figuras apenas de dados derivados pelo script versionado. O
script lê N e os slots de `schedule.json`, valida que há exatamente 3N slots, um
slot por cenário em cada bloco, ordem balanceada segundo N, integridade e
unicidade dos IDs, hashes dos artefatos, sequência dos eventos e campos de
resultado exigidos. Não codificar 90 como constante. Se faltar dado bruto, houver
ID duplicado, esquema desconhecido, status instrumental `unresolved` ou slot sem
estado de despacho/validade, parar com erro e explicar o motivo. Não completar
campos silenciosamente.

Entregas da issue #24:

- tabela de configuração com placa, cenários, versões, artefatos e hashes;
- tabelas de disparos e slots por cenário, com resultados de aceite, atualização,
  validade instrumental e reposições;
- gráficos separados de conclusão por slot e de sucesso após aceite, com IC
  score de Wilson, contagens e denominadores;
- distribuição acumulada dos tempos entre atualizações bem-sucedidas, com as
  contagens de falha visíveis em tabela própria;
- gráfico pareado das diferenças de sucesso por bloco, com IC para pares
  combinados, quadro 2×2 e número de pares completos;
- pontos por slot para lacuna de serviço, cauda censurada e respostas perdidas;
- gráficos de payload e memória por etapa somente quando houver medida válida;
- tabela da matriz de resiliência, separada por célula e resultado esperado;
- scripts determinísticos, ambiente de análise, dados usados e SHA256SUMS;
- descrição de protocolo, hardware, toolchains, limitações e passos de reprodução.

## Critérios de passagem

| Gate | Pode avançar quando |
|---|---|
| Viabilidade | O mesmo workload passa nos cenários nativo, interpretado e AOT; hashes e configurações estão guardados |
| Serviço | Os serviços nativo e modular produzem resultados de referência, e o GPIO físico bate com o solicitado |
| Atualização | A/B/C completam v1→v2 observável; cada falha tem status e registro bruto |
| Medição | O validador reconstrói métricas dos arquivos brutos e falha em pacote incompleto |
| Piloto | Pelo menos 15 tentativas normais aceitas, cinco por cenário; dispatches sem aceite registrados em separado; parâmetros e agenda congelados |
| Campanha normal | Todos os slots planejados têm resultado; falhas e reposições estão ligadas e contabilizadas |
| Resiliência | Matriz separada executada com IDs, eventos seriais/UDP e denominadores por célula |
| Análise | Scripts refazem tabelas e figuras somente com o pacote arquivado e os hashes conferem |

## Agenda prática de execução

1. Conferir a #10 já integrada pela PR #28. Preservar os dados, a calibração e
   `control_tail_censored_ns`; confirmar que os campos necessários chegaram ao
   observador integrado.
2. Fechar a decisão da histerese e executar a #11 em uma sessão própria assim que
   a #10 estiver integrada e a placa estiver disponível. A #11 não espera #6, #7
   ou #9.
3. Avançar #6 → #7. Agendar a participação humana da #9 durante a espera externa
   e executá-la assim que #7 cumprir seus critérios. Depois de #7 e #4, iniciar
   #15 sem esperar #9, #12, #13 ou #14.
4. Com #9 aprovada, seguir #12 → #13/#14. Fazer #16 quando #11 e #15 estiverem
   prontas; fazer #17/#18 quando suas demonstrações e #15 estiverem prontas.
5. Concluir #19 somente após #16–#18, validando o esquema de slots, denominadores,
   intervalos, censura e agenda variável por N. Fazer #20 com regras já
   pré-registradas; alterações depois do congelamento geram nova revisão e novo
   campaign_id.
6. Executar #21 por blocos. Fazer #22 em sessão própria assim que #12, #15 e a
   decisão #5 permitirem. Rodar #23 após #20 e #22, com IDs e diretório próprios.
7. Fechar #24 só quando os dois conjuntos estiverem completos e a análise puder
   ser refeita dos dados brutos.

Não são definidas datas de calendário nesta revisão. A placa, o LXC e a
participação humana criam esperas que não cabem em estimativa de esforço de
engenharia. Reestimar depois da prova #9 e do primeiro ciclo integral da #16.

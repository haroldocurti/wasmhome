# Plano de execução dos experimentos

Status sincronizado em 13/10/2026: execução iniciada, com preparação e diagnóstico físico registrados. Este
plano prevalece sobre o protocolo inicial e sobre os comandos provisórios do
README. Plataforma comum e observador têm evidências de diagnóstico no ESP32-S3;
os serviços definitivos, a execução WAMR e as atualizações dos três cenários
permanecem pendentes. As [evidências de 12/10](evidence/retomada-linux-2026-10-12/e1-scenarios/README.md)
confirmam LLVM/wamrc R5 compatível com CT107, artefatos Wasm/AOT reproduzidos e
builds de firmware A/B/C. Ainda faltam testes locais da revisão recente,
atestado independente do compilador, revisão/aceite #7 e execução no MCU #9.
O [checklist de execução](checklist-execucao.md) detalha
as dependências, os critérios de aceite e as revisões de cada entrega.

## Objetivo e delimitação

Avaliar como a estratégia de execução e atualização afeta o tempo de retorno,
as lacunas de serviço, a perda de respostas, o volume transferido e o uso de
recursos no mesmo ESP32-S3. O resultado do TCC será uma bancada reproduzível,
seus dados e uma análise.

O detalhamento operacional de dependências, preparação da campanha, formato dos
registros e análise está em [Planejamento detalhado para coleta e análise](plano-detalhado-coleta-analise.md).

| Cenário | Execução | Aplicação e atualização |
|---|---|---|
| A | C nativo sobre ESP-IDF/FreeRTOS | Telemetria e controle no firmware monolítico; OTA da imagem da aplicação e reinicialização |
| B | Wasm no interpretador clássico WAMR | Telemetria em módulo estável; download e substituição do módulo Wasm de controle |
| C | AOT no WAMR para ESP32-S3 | Telemetria em módulo estável; download e substituição do módulo AOT de controle |

FreeRTOS permanece comum aos três cenários. AOT não é um terceiro sistema
operacional. Usaremos uma carga funcional com entradas e resultados equivalentes
nos três modos. Ela processará amostras numeradas de um gerador externo e terá
duas versões, v1 e v2, diferenciadas por uma pequena alteração de parâmetro.
As saídas esperadas permitirão comprovar a ativação da nova versão.

O código inteiro em `workload/benchmark.c` é um teste preliminar de execução.
Não o apresentar como Embench. Conforme o domínio aprovado em `CONTEXT.md` e
no mapa da issue #1, a campanha usa dois serviços: telemetria DS18B20 estável e
controle GPIO substituível. Em B/C, as regras de ambos pertencem a módulos
separados; o firmware-base mantém drivers e bindings. Somente o módulo de
controle muda de v1 para v2. Em A, os dois serviços integram a imagem nativa.
O controle usa limiares de 25 °C em v1 e 30 °C em v2, com histerese de 1 °C
e loopback GPIO5→GPIO6. A sequência de entrada atravessa os dois limiares, e o
observador confere a versão, o pedido e o GPIO observado. Não há migração de
estado entre versões. Sensores adicionais,
Kalman, dashboard, frotas e múltiplos dispositivos ficam fora da campanha.

## Problemas encontrados no plano inicial

1. O firmware carrega e instancia uma vez, mas copia os mesmos tempos para as
   30 linhas. São 30 chamadas na mesma sessão e uma observação de carga, não
   30 atualizações independentes.
2. Os módulos estão embutidos no firmware. Ainda faltam download, OTA, troca
   dinâmica e medição externa de disponibilidade e perda de respostas.
3. A memória é observada após inicializar WAMR e antes de criar o ambiente de
   execução. Faltam custo do runtime, buffers, pilhas e memória após liberação.
4. O coletor aceita conjunto vazio, não exige todos os cenários e confia em
   `status=ok` sem conferir checksum. Não distingue sessões ou tentativas.
5. Diretórios de build separados precisam também de arquivos `sdkconfig`
   independentes. As configurações efetivas devem constar das evidências.
6. No plano inicial, a combinação de versões e os comandos AOT não tinham
   sido executados. Em 12/10, geração/repetição Wasm/AOT e builds A/B/C foram
   validados no CT107. Faltam os testes locais da revisão recente e a prova
   dos artefatos na placa; compilação não comprova execução no MCU.

## Etapas e critérios de conclusão

As durações estimam esforço de uma pessoa. Esperas por equipamento ou ambiente
podem acrescentar tempo. Não são datas de entrega contratadas.

| Etapa | Entrega delimitada | Critério para concluir | Depende de | Esforço |
|---|---|---|---|---|
| E0. Inventário e contrato | Placa, ambiente, carga v1/v2 e métricas definidos | Inventário preenchido e entradas/saídas de referência registradas | Nenhuma | 0,5–1 dia |
| E1. Viabilidade | Builds mínimos dos três modos | Resultado correto na mesma placa, versões e logs preservados | E0 | 2–4 dias |
| E2. Observação do serviço | Gerador externo, respostas versionadas e coleta | Funcionamento estável sem atualização e interrupção conhecida detectada | E1 | 1–2 dias |
| E3. OTA nativo | Atualização v1→v2, reboot e reconexão | Resposta correta da nova versão com intervalo completo registrado | E2 | 1–2 dias |
| E4. Troca modular | Download, validação e substituição em B e C | Nova versão correta e continuidade do boot observada | E2 | 2–4 dias |
| E5. Piloto | Pelo menos cinco tentativas normais aceitas por cenário e protocolo congelado | Mínimo de 15 tentativas aceitas rastreáveis; dispatches sem aceite ficam separados e podem exigir slots adicionais | E3 e E4 | 1–2 dias |
| E6. Campanha | N blocos, um slot de A, B e C por bloco | 3N slots contabilizados; resultados por slot e por atualização aceita separados | E5 | 1–2 dias |
| E7. Análise | Tabelas, gráficos, texto e pacote de reprodução | Campanhas normal e de resiliência contabilizadas, resultados rastreáveis e limitações explícitas | E6 e campanha complementar da #23 | 2–3 dias |

Estimativa sequencial total: aproximadamente 11–20 dias de trabalho, além de
esperas externas. Reservar margem no calendário acadêmico. E3 e E4 são
independentes depois de E2; não é necessário executá-las simultaneamente.

### E0. Inventariar e fechar o contrato

Registrar modelo/revisão da placa, flash, PSRAM, porta serial, alimentação,
computador, sistema operacional, rede e acesso ao servidor. Escolher ambiente
de compilação e distribuição. Verificar a disponibilidade do Proxmox/LXC previsto
no TCC antes de pressupor a instalação de uma infraestrutura nova.

O levantamento inicial da bancada está em [Inventário da bancada física](bancada-fisica.md).
Porta serial, rede e condições ambientais são variáveis e devem ser reconfirmadas
no início de cada sessão ou campanha. O LXC do servidor de artefatos foi criado
como CT 107 em 01/10/2026; acesso, sistema-base e persistência foram verificados.
Em 09/10, a reconstrução do sistema-base foi executada no CT 109 e o caminho
ESP32→CT 107 foi demonstrado por TCP com nonce, origem e logs dos dois lados.
O CT 109 ficou parado ao final. As evidências e seus limites estão no
[registro da sessão](evidence/execucao-2026-10-09/README.md). Revisão final,
integração pela PR #29 e aceite da #6 foram concluídos em 10/10.
O [registro de retomada](retomada-linux.md) atualiza o preparo da toolchain.

A [documentação do provisionamento do LXC](provisionamento-lxc.md) registra a
inspeção de capacidade e rede, a configuração efetiva e os testes de
aceite da issue #6. O PC observador foi confirmado pelo usuário. A reserva DHCP
do CT 107 foi confirmada pelo usuário em 09/10; não houve inspeção do painel DHCP.
O ensaio TCP temporário comprova transporte, sem comprovar HTTPS, distribuição
assinada ou disponibilidade dos serviços da campanha.

Definir a transformação comum, a alteração v1/v2 e os dados de referência. O
[contrato UDP v1](protocolo-observacao.md) fixa os campos das mensagens; a
cadência de aquisição/observação é 500/100 ms conforme `CONTEXT.md`. O timeout
inicial de 300 ms segue como candidato a congelar ou ajustar após o piloto.

### E1. Comprovar viabilidade antes de construir o restante

Configurar as ferramentas e executar a carga mínima nos três modos. Antecipar
o teste AOT para detectar dificuldades com backend Xtensa, CPU, memória executável
e compatibilidade entre compilador e runtime. Não presumir que qualquer `wamrc`
pré-compilado sirva para ESP32-S3.

Guardar também a execução nativa da carga mínima e seu checksum na mesma placa.
O diagnóstico físico da plataforma comum não executa essa carga. Compilar um
firmware com benchmark habilitado tampouco comprova sua execução no dispositivo.

O pin histórico WAMR 2.4.0 foi substituído no candidato E1 por
`wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.1 e `wamrc` 2.4.5, com fontes
fixadas no lock e nos recibos de 12/10. Revisar as versões e a proveniência no
aceite da #7 e comprovar compatibilidade física na #9. Guardar flags e hashes.
Isolar `sdkconfig`; verificar interpretador clássico em B e AOT em C, clock,
otimização e o que efetivamente foi ligado em A. Revisar buffers graváveis,
alinhamento, pilha e limpeza de recursos. Verificar no código gerado que a carga
não foi eliminada pelo compilador ou reduzida a uma constante.

Se AOT não executar dentro da janela de E1, registrar erro, tentativas e
alternativas. A comparação dos três cenários permanece bloqueada. Mudança de
hardware, runtime ou terceiro cenário exige revisão explícita com o grupo e
orientador; não substituir o cenário silenciosamente.

### E2. Medir respostas da aplicação

O observador envia `sample_id` e a temperatura de estímulo ao serviço de
controle. O dispositivo responde com o mesmo ID, resultado, versão do módulo,
nível solicitado e nível observado pelo loopback. Apenas respostas corretas
dentro do timeout contam como serviço de controle prestado. Em paralelo, o
módulo de telemetria publica a última leitura válida do DS18B20, seu ID, idade
e estado. Heartbeat do firmware-base é adicional e não comprova disponibilidade
funcional dos dois serviços.

A calibração já registrada do observador usa uma fixture diagnóstica. E2 exige
validar também os serviços definitivos dos três cenários, das issues #11, #13
e #14. O fechamento da issue #10 não encerra E2.

Usar relógio monotônico do observador para eventos que atravessam reinicializações.
Registrar horário planejado/efetivo de envio, recepção, duplicatas e atrasos.
Escolher uma cadência que o cenário mais lento sustente e um prazo fixo para
respostas. Preservar janelas antes, durante e depois da atualização. Calibrar a
medição com uma interrupção controlada de duração conhecida.

### E3 e E4. Implementar as atualizações reais

Usar o mesmo servidor, rede, transporte e mudança observável do controle. Em A,
telemetria e controle são atualizados juntos pela imagem OTA da aplicação, não
pela imagem inteira da flash. Em B/C, transferir somente o módulo de controle.
A telemetria permanece ativa durante a troca modular. Arrays embutidos ficam
restritos à prova inicial.

Definir e implementar uma política comparável de integridade/autenticidade antes
da campanha. Documentar os campos autenticados do manifesto, incluindo versão
e hash, e a proteção contra versões antigas. Medir validação separadamente.
Não alegar conformidade integral à RFC 9019 apenas por usar hash ou assinatura.

Baixar e validar antes de interromper a aplicação modular. Trocar em ponto seguro
entre amostras e documentar se instâncias coexistem. Medir o pico de memória.
Definir recuperação quando a ativação falhar; nunca liberar a instância enquanto
ela ainda estiver executando.

Preparar a bateria complementar separada conforme a
[decisão de resiliência](research/ensaios-resiliencia.md): cinco tentativas por
célula aplicável, totalizando 70 falhas e dois controles. Inclui assinatura,
hash e sequência, traps Wasm e aborto nativo controlado, com limites de
comparação explícitos. Implementar e comprovar os mecanismos no ticket #22;
executar a matriz no ticket #23, após congelar o protocolo no piloto.
Usar o canal serial de ensaio para disparo/eventos e o UDP para disponibilidade
funcional. Preservar rejeições e falhas segundo a contagem própria da bateria.

### E5. Pilotar, corrigir e congelar

Executar pelo menos cinco tentativas normais aceitas por cenário, total mínimo
de 15. Um slot que termine sem aceite não conta entre as cinco e deve permanecer
registrado como resultado de despacho; planejar slots adicionais quando
necessário. Não incorporar os dados do piloto à campanha definitiva. Corrigir
a coleta para distinguir campanha, slot, bloco, sessão, disparo e tentativa
aceita; rejeitar evidências vazias ou malformadas; verificar a referência de
resultado e a presença da matriz completa. Preservar todas as falhas.

Congelar janela, cadência, timeout, condições iniciais, memória e sequência de
eventos. O timeout funcional congelado aplica-se também à campanha de resiliência;
300 ms é seu valor inicial, e 30 segundos permanece o limite operacional de
recuperação dessa bateria. Ajustar o aquecimento ao serviço persistente. O comando atual executa
ao boot, portanto esperar 60 segundos depois dele não aquece as medições já
realizadas. A espera deve anteceder explicitamente o disparo medido.

Antes do piloto, registrar a regra para selecionar o timeout funcional e o tamanho
amostral. Cinco tentativas aceitas por cenário servem para validar o procedimento,
mas não estimam poder com precisão. Se não houver uma base independente para alterar N,
manter 30 blocos por viabilidade e apresentar a análise como descritiva. Congelar
N e a agenda antes da primeira coleta definitiva.

### E6. Coletar por blocos

A agenda por slots a seguir é proposta operacional. A referência anterior e
o checklist dizem 30 tentativas por cenário, 90 no total, enquanto o contrato
inicia uma tentativa normal após aceite. Com recusas, 90 slots podem gerar
menos de 90 tentativas aceitas. Resolver no congelamento da #20 se o alvo será
de slots ou de tentativas aceitas e registrar a agenda correspondente. Esta
publicação não decide essa mudança; #21 permanece bloqueada até a conciliação.

Usar N=30 blocos como referência, cada um com um slot planejado de A, B e C na
mesma placa. Isso gera 90 slots, com cada uma das seis ordens aparecendo cinco
vezes. Se N mudar, gerar 3N slots e distribuir as ordens com contagens que diferem
no máximo em uma. Registrar `slot_id`, `session_id`, a semente e a agenda
congelada. O slot é a oportunidade planejada; a tentativa normal de atualização
começa somente com `UPDATE_ACCEPTANCE=ACCEPTED` correlacionado. Chamadas de função
são observações internas, não atualizações independentes.

Restaurar v1 antes de cada tentativa e aguardar estabilidade. Excluir esse
preparo da janela de medição. Se houver contador antirretorno, usar pacote de
preparação autenticado com sequência nova para a lógica v1, seguido de sequência
maior para v2; não desativar a proteção durante a campanha.

Todos os slots entram na taxa de conclusão por slot. Recusas e timeouts antes do
aceite são resultados de despacho, não falhas de uma atualização aceita. Reportar
em separado a taxa de sucesso entre as tentativas que receberam aceite e tiveram
instrumentação válida. Falhas e timeouts depois do aceite permanecem no
denominador dessas tentativas. Só repor um slot por falha instrumental ou
impedimento externo comprovado; manter o registro original e ligar a reposição
ao mesmo slot. Não repetir seletivamente resultados desfavoráveis.

### E7. Analisar e redigir

A análise final da issue #24 exige a campanha normal e a complementar de
resiliência da issue #23. É possível preparar a análise normal após E6;
a entrega completa permanece pendente até contabilizar ambas separadamente.

Apresentar conclusão por slot e sucesso após aceite com instrumentação válida,
com numeradores, denominadores e intervalos score de Wilson de 95% por cenário.
Comparar as taxas
binárias pareadas com o intervalo score assintótico para pares combinados,
conforme [Fagerland, Lydersen e Laake (2014)](https://doi.org/10.1002/sim.6148),
e publicar a tabela 2×2 de cada contraste. Para tempos e sensibilidade a sessões
compartilhadas, preservar os blocos e reamostrar por `session_id`; declarar
incerteza entre sessões não estimável quando houver sessões insuficientes. Tempos de atualizações
bem-sucedidas devem vir acompanhados das contagens de falhas e timeouts. Não
convertê-los em zeros nem excluí-los silenciosamente. As comparações entre os
três cenários são exploratórias; resultado sem diferença detectável não prova
equivalência.

O microbenchmark de processamento será secundário, com chamadas agrupadas por
sessão. Valores repetidos de uma única carga não formam amostras independentes.
Uma placa e uma rede limitam a generalização; não concluir confiabilidade para
sistemas críticos a partir desta bancada.

## Métricas e fronteiras

| Métrica | Definição | Cuidado |
|---|---|---|
| Tempo envio→resposta | Primeira resposta correta de v2 menos envio do comando, em ms no observador | Inclui o despacho; timeout é resultado de slot sem conclusão |
| Duração após aceite | Primeira resposta correta de v2 menos recepção do aceite | Mede a atualização normal depois que ela começa |
| Lacuna de serviço | Maior intervalo entre respostas funcionais corretas na janela | Reportar também a cauda censurada até o fim da janela |
| Indisponibilidade estimada | Excesso da maior lacuna observada sobre o intervalo nominal, limitado a zero | Não inclui a cauda censurada e não é duração física exata |
| Perda de respostas | IDs enviados sem resposta correta até o prazo fixado | Reportar total enviado, atrasos e perdas sem atualização |
| Telemetria | IDs de leitura, idade, estado do sensor e lacunas entre mensagens válidas | Não inferir perda de aquisição física a partir de perda de datagramas |
| Payload | Bytes da imagem/módulo e metadados necessários | Tráfego total com retransmissões é métrica distinta |
| Tempos locais | Download, validação, carga, instanciação e ativação, em µs | Não subtrair relógios entre dispositivos nem através do reboot |
| Memória | SRAM/PSRAM antes do runtime, durante troca e após liberação | Incluir estáticos, buffers e pilhas; rotular mínimo desde boot |
| Processamento | Tempo da mesma transformação com resultado verificado | Sem impressão no trecho medido; identificar overhead da chamada |
| Conclusão por slot | Aceite pontual e resposta correta de v2 dentro dos prazos | Denominador: slots planejados; despacho e atualização têm resultados separados |
| Sucesso após aceite | Resposta correta de v2 dentro dos prazos após `ACCEPTED` | Denominador: tentativas aceitas com instrumentação válida |

Campos não aplicáveis recebem `null`/`not_applicable`, não zero. A perda de
controle medida é a perda de respostas ao gerador; não demonstra perda de
aquisição do sensor. A telemetria reporta separadamente mensagens, IDs, idade
e estado observados. O custo de disponibilidade do firmware-base pode ser
medido à parte quando necessário.

Preservar tanto o envio do disparo quanto a recepção de `update_accepted` no
relógio monotônico do observador. O aceite inicia a tentativa normal, conforme
`CONTEXT.md`; o tempo total ainda inclui o intervalo desde o envio correlacionado
até a primeira resposta correta de v2. Disparos nunca aceitos são preservados
como eventos de disparo, separados do denominador de tentativas aceitas. Falhas
e timeouts depois do aceite permanecem nesse denominador.

## Evidências e conclusão

Preservar revisão do código, inventário, versões, configurações efetivas, hashes,
ordem dos blocos e logs brutos de observador, dispositivo e servidor. Cada
registro identifica campanha, slot, bloco, tentativa quando aceita, cenário,
sessão, boot e versão.

Entregar dados brutos, tabela derivada, scripts de análise, gráficos, instruções
de reprodução e limitações. Encerrar após contabilizar todas as tentativas e
rastrear cada conclusão à evidência. Compilação, execução de checksum e
atualização real são marcos distintos, cada um com seu registro.

# Plano de execução dos experimentos

Status: planejamento revisado. Este plano prevalece sobre o protocolo inicial e
sobre os comandos provisórios do README. O firmware existente é preliminar;
nenhum cenário foi comprovado no ESP32-S3.

## Objetivo e delimitação

Avaliar o impacto da atualização da mesma aplicação no ESP32-S3 sobre duração,
indisponibilidade, perda de respostas, volume transferido e consumo de recursos.
O resultado do TCC será uma bancada reproduzível, seus dados e uma análise.

| Cenário | Aplicação | Atualização |
|---|---|---|
| A | C nativo sobre ESP-IDF/FreeRTOS | Imagem da aplicação por OTA e reinicialização |
| B | Wasm no interpretador clássico WAMR | Download e substituição do módulo |
| C | AOT no WAMR para ESP32-S3 | Download e substituição do módulo AOT |

FreeRTOS permanece comum aos três cenários. AOT não é um terceiro sistema
operacional. Usaremos uma carga funcional com entradas e resultados equivalentes
nos três modos. Ela processará amostras numeradas de um gerador externo e terá
duas versões, v1 e v2, diferenciadas por uma pequena alteração de parâmetro.
As saídas esperadas permitirão comprovar a ativação da nova versão.

O código inteiro em `workload/benchmark.c` é um teste preliminar de execução.
Não o apresentar como Embench. Sensores físicos, Kalman, dashboard, frotas,
múltiplos módulos e migração de estado ficam fora da primeira campanha. Usar uma
transformação sem estado entre amostras para delimitar a aplicação.

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
6. A combinação de versões atual e os comandos AOT ainda não foram executados.
   Os testes locais não compilam o C nem comprovam os artefatos na placa.

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
| E5. Piloto | Cinco ciclos completos por cenário e protocolo congelado | 15 tentativas rastreáveis, instrumentos e critérios revisados | E3 e E4 | 1–2 dias |
| E6. Campanha | 30 tentativas por cenário | 90 tentativas contabilizadas, inclusive falhas | E5 | 1–2 dias |
| E7. Análise | Tabelas, gráficos, texto e pacote de reprodução | Resultados rastreáveis aos dados e limitações explícitas | E6 | 2–3 dias |

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
Conectividade com o ESP32 e reconstrução em CT separado continuam pendentes.

A [documentação do provisionamento do LXC](provisionamento-lxc.md) registra a
inspeção de capacidade e rede, a configuração efetiva e os testes de
aceite da issue #6. O PC observador foi confirmado pelo usuário; os caminhos de
rede entre o ESP32 e o LXC permanecem pendentes de comprovação.

Definir a transformação comum, a alteração v1/v2 e os dados de referência. O
[contrato UDP v1](protocolo-observacao.md) fixa os campos das mensagens; a
cadência de aquisição/observação é 500/100 ms conforme `CONTEXT.md`. O timeout
inicial de 300 ms segue como candidato a congelar ou ajustar após o piloto.

### E1. Comprovar viabilidade antes de construir o restante

Configurar as ferramentas e executar a carga mínima nos três modos. Antecipar
o teste AOT para detectar dificuldades com backend Xtensa, CPU, memória executável
e compatibilidade entre compilador e runtime. Não presumir que qualquer `wamrc`
pré-compilado sirva para ESP32-S3.

Revisar os avisos oficiais das versões antes de manter o pin WAMR 2.4.0. Fixar
versões exatas e origem dos componentes após a prova. Guardar flags e hashes.
Isolar `sdkconfig`; verificar interpretador clássico em B e AOT em C, clock,
otimização e o que efetivamente foi ligado em A. Revisar buffers graváveis,
alinhamento, pilha e limpeza de recursos. Verificar no código gerado que a carga
não foi eliminada pelo compilador ou reduzida a uma constante.

Se AOT não executar dentro da janela de E1, registrar erro, tentativas e
alternativas. A comparação dos três cenários permanece bloqueada. Mudança de
hardware, runtime ou terceiro cenário exige revisão explícita com o grupo e
orientador; não substituir o cenário silenciosamente.

### E2. Medir respostas da aplicação

O observador envia `sample_id` e entrada. O dispositivo responde com o mesmo ID,
resultado, versão da aplicação e identificador de boot. Apenas respostas de
cálculo correto contam como serviço prestado. Heartbeat do firmware-base é
adicional e não comprova que a aplicação permaneceu disponível.

Usar relógio monotônico do observador para eventos que atravessam reinicializações.
Registrar horário planejado/efetivo de envio, recepção, duplicatas e atrasos.
Escolher uma cadência que o cenário mais lento sustente e um prazo fixo para
respostas. Preservar janelas antes, durante e depois da atualização. Calibrar a
medição com uma interrupção controlada de duração conhecida.

### E3 e E4. Implementar as atualizações reais

Usar mesmo servidor, rede, transporte e alteração funcional. Em A, transferir
a imagem OTA da aplicação, não a imagem inteira da flash. Em B/C, transferir
os módulos pela rede. Arrays embutidos ficam restritos à prova inicial.

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

Executar cinco ciclos completos v1→v2 por cenário. Não incorporar esses dados
à campanha definitiva. Corrigir coleta para distinguir campanha, bloco, sessão
e tentativa; rejeitar evidências vazias ou malformadas; verificar referência de
resultado e presença da matriz completa. Guardar as falhas como resultados.

Congelar janela, cadência, timeout, condições iniciais, memória e sequência de
eventos. O timeout funcional congelado aplica-se também à campanha de resiliência;
300 ms é seu valor inicial, e 30 segundos permanece o limite operacional de
recuperação dessa bateria. Ajustar o aquecimento ao serviço persistente. O comando atual executa
ao boot, portanto esperar 60 segundos depois dele não aquece as medições já
realizadas. A espera deve anteceder explicitamente o disparo medido.

Reavaliar 30 tentativas por cenário à luz da dispersão do piloto, antes de iniciar
a coleta definitiva. Justificar qualquer ajuste de tamanho amostral.

### E6. Coletar por blocos

Usar 30 blocos, cada um com uma tentativa de A, B e C na mesma placa. Balancear
a ordem: cada uma das seis permutações aparece cinco vezes, com embaralhamento
e semente registrados. A unidade experimental é uma tentativa completa de
atualização. Chamadas de função são observações internas à tentativa.

Restaurar v1 antes de cada tentativa e aguardar estabilidade. Excluir esse
preparo da janela de medição. Se houver contador antirretorno, usar pacote de
preparação autenticado com sequência nova para a lógica v1, seguido de sequência
maior para v2; não desativar a proteção durante a campanha.

Falhas de atualização e timeouts permanecem no denominador. Falhas comprovadas
do instrumento podem invalidar uma tentativa, com motivo preservado e reposição
identificada. Não repetir seletivamente resultados desfavoráveis.

### E7. Analisar e redigir

Apresentar taxa de sucesso, distribuição, mediana e intervalo interquartil por
cenário. Comparar diferenças pareadas por bloco, com intervalos de confiança
de 95% por reamostragem dos blocos, método e semente documentados. Tempos de
atualizações bem-sucedidas devem ser acompanhados das contagens de falhas e
timeouts. Não convertê-los em zeros nem excluí-los silenciosamente.

O microbenchmark de processamento será secundário, com chamadas agrupadas por
sessão. Valores repetidos de uma única carga não formam amostras independentes.
Uma placa e uma rede limitam a generalização; não concluir confiabilidade para
sistemas críticos a partir desta bancada.

## Métricas e fronteiras

| Métrica | Definição | Cuidado |
|---|---|---|
| Tempo total | Primeira resposta correta de v2 menos envio do comando, em ms no observador | Timeout é falha registrada |
| Lacuna de serviço | Maior intervalo entre respostas funcionais corretas na janela | Inclui cadência normal e efeitos de rede; comparar com linha de base |
| Indisponibilidade estimada | Excesso da lacuna sobre o intervalo nominal, limitado a zero | Resolução depende da cadência; não é duração física exata |
| Perda de respostas | IDs enviados sem resposta correta até o prazo fixado | Reportar total enviado, atrasos e perdas sem atualização |
| Payload | Bytes da imagem/módulo e metadados necessários | Tráfego total com retransmissões é métrica distinta |
| Tempos locais | Download, validação, carga, instanciação e ativação, em µs | Não subtrair relógios entre dispositivos nem através do reboot |
| Memória | SRAM/PSRAM antes do runtime, durante troca e após liberação | Incluir estáticos, buffers e pilhas; rotular mínimo desde boot |
| Processamento | Tempo da mesma transformação com resultado verificado | Sem impressão no trecho medido; identificar overhead da chamada |
| Sucesso | Resposta correta de v2 dentro do timeout | Em B/C, registrar reboot inesperado e recuperação |

Campos não aplicáveis recebem `null`/`not_applicable`, não zero. A perda medida
é perda de respostas ao gerador, não uma prova direta de perda de aquisição de
um sensor real. O custo de disponibilidade do serviço-base pode ser medido à
parte quando necessário.

## Evidências e conclusão

Preservar revisão do código, inventário, versões, configurações efetivas, hashes,
ordem dos blocos e logs brutos de observador, dispositivo e servidor. Cada
registro identifica campanha, bloco, tentativa, cenário, boot e versão.

Entregar dados brutos, tabela derivada, scripts de análise, gráficos, instruções
de reprodução e limitações. Encerrar após contabilizar todas as tentativas e
rastrear cada conclusão à evidência. Compilação, execução de checksum e
atualização real são marcos distintos, cada um com seu registro.

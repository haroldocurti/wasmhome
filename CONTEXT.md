# Glossário do WasmHome

## Bancada experimental

Conjunto formado pelo ESP32-S3, sensor DS18B20, rede, servidor de artefatos,
observador externo e software necessário para executar e medir os cenários.

## Cenário de execução

Uma das três estratégias executadas no mesmo ESP32-S3: C nativo com atualização
OTA, WebAssembly no interpretador clássico do WAMR ou WebAssembly AOT no WAMR.

## Firmware-base

Software estável que contém FreeRTOS, conectividade, runtime WAMR, orquestração,
drivers físicos e bindings autorizados. Nos cenários WebAssembly, ele não contém
as regras dos serviços de telemetria ou controle.

## Módulo de telemetria

Aplicação modular estável que obtém a última leitura válida do DS18B20 por um
binding nativo, numera as amostras e publica temperatura e estado do sensor.
Permanece ativo durante a substituição do módulo de controle.

## Módulo de controle

Aplicação que contém a regra de controle do GPIO. Possui versões v1 e v2 e é o
único módulo substituído durante a atualização modular.

## Driver de temperatura

Parte do firmware-base responsável pelo protocolo físico do DS18B20. Opera o
sensor em três fios, com resolução de 10 bits e conversão assíncrona. Mantém a
última leitura, seu timestamp e seu estado disponíveis ao binding.

## Atualização experimental

Transição observada da aplicação v1 para v2. No cenário nativo, substitui a
imagem OTA e reinicia o dispositivo. Nos cenários WebAssembly, substitui apenas
o módulo de controle e preserva a instância do módulo de telemetria.

## Disponibilidade funcional

Capacidade de um serviço produzir, dentro do prazo definido, uma resposta ou
evento com identificador, versão e resultado corretos. Conectividade ou heartbeat
isolado não comprovam disponibilidade funcional.

## Indisponibilidade parcial

Intervalo em que o serviço de controle fica indisponível enquanto o módulo de
telemetria continua produzindo amostras válidas no mesmo boot.

## Tentativa

Uma execução completa e identificada da atualização experimental, incluindo
janela estável anterior, comando de atualização, ativação ou falha e janela de
observação posterior.

## Bloco experimental

Conjunto de três tentativas realizadas sob condições próximas, uma por cenário,
com ordem definida pelo plano balanceado da campanha.

## Observador externo

Ferramenta executada fora do ESP32-S3 que envia estímulos, recebe eventos e
respostas, verifica resultados e registra os limites temporais de cada tentativa.

## Canal de atualização

Conexão HTTPS usada para transferir imagem OTA, módulos e manifestos do servidor
de artefatos para o ESP32-S3.

## Canal de observação

Comunicação UDP local usada pelo observador externo para enviar estímulos e
receber respostas numeradas dos serviços. Não oferece retransmissão automática;
perdas e atrasos são registrados pela própria ferramenta experimental.

## Disparo de atualização

Mensagem UDP que contém o identificador da tentativa e a sequência de destino.
O dispositivo aceita o comando somente para o servidor e caminho previamente
configurados, responde com `update_accepted` e inicia o download por HTTPS. A
tentativa começa quando o observador recebe esse aceite. Uma perda anterior ao
aceite não inicia tentativa; falha ou timeout posterior integra seu resultado.

## Protocolo de observação

Formato UDP binário v1 definido em
[Protocolo binário de observação](docs/protocolo-observacao.md). Usa cabeçalho
fixo de 48 bytes, UUID de tentativa, contador de mensagem, `boot_id` e timestamp
monotônico; inteiros em ordem de rede. Tipos incluem telemetria, pedido e
resultado de controle, disparo e aceite/recusa de atualização. O codec Python
valida vetores no host; transporte no firmware e na placa ainda não foi
implementado.

## Registro bruto

Arquivo JSONL gravado pelo observador externo. Preserva cada mensagem enviada ou
recebida, horário monotônico local, conteúdo decodificado e resultado das
validações. Tabelas CSV e gráficos são derivados desse registro.

## Servidor da bancada

Container LXC executado no Proxmox e usado na campanha definitiva para compilar,
assinar e hospedar por HTTPS todos os artefatos. O desenvolvimento pode usar uma
máquina local, mas os artefatos medidos são entregues pelo servidor da bancada.

## Plataforma comum do MCU

ESP32-S3 executando ESP-IDF e FreeRTOS nos três cenários. Inclui conectividade,
particionamento, drivers, canais experimentais e instrumentação compartilhada.
Não existe cenário bare-metal ou sem FreeRTOS nesta campanha; a variável
independente é a estratégia de execução e atualização da aplicação.

## Campanha normal

Conjunto principal de atualizações válidas usado para comparar os três cenários.
Começa somente após o piloto e mantém falhas e timeouts no denominador.

## Campanha de resiliência

Bateria complementar, separada da campanha normal, que observa rejeição de
assinatura inválida, hash divergente e sequência antiga, além da contenção de
falhas reproduzíveis durante a execução. A definição dos testes de memória depende
de pesquisa sobre semântica equivalente e não presume que endereço linear zero
produza um trap em WebAssembly.

## Canal de diagnóstico

Saída serial do ESP32-S3 usada para eventos internos e investigação de falhas.
Não define a disponibilidade funcional observada pela campanha.

## Envelope de atualização

Manifesto comum aos três cenários que identifica cenário, tipo de artefato,
versão, sequência monotônica, tamanho e hash SHA-256. Uma assinatura ECDSA P-256
autentica sua representação canônica. O ESP32-S3 valida o envelope antes de
gravar ou instanciar o payload.

## Cadeia de confiança experimental

Combinação do canal HTTPS ancorado em uma autoridade certificadora local com a
validação do envelope de atualização. Rejeita hash divergente, assinatura inválida
e sequência antiga. Secure Boot e criptografia de flash não fazem parte desta
delimitação inicial.

## Cadência de aquisição

O driver inicia uma conversão assíncrona de 10 bits no DS18B20 a cada 500 ms.
Cada leitura válida recebe um identificador e timestamp próprios.

## Cadência de observação

O módulo de telemetria publica a cada 100 ms a última temperatura válida, o
identificador da leitura e sua idade. O observador também estimula o serviço de
controle a cada 100 ms. O timeout inicial do piloto é 300 ms e será congelado ou
ajustado antes da campanha definitiva.

## Regra de controle

Transformação determinística de valores de teste em estado do GPIO, com histerese
de 1 grau Celsius. A versão v1 usa limiar de 25 graus Celsius e a v2 usa limiar
de 30 graus Celsius. O observador envia uma sequência conhecida que atravessa os
dois limiares e verifica o resultado correspondente à versão ativa.

## Loopback de GPIO

Ligação física entre o pino comandado pelo módulo de controle e um pino de entrada
do ESP32-S3. Permite distinguir o nível solicitado do nível elétrico observado.
Cada resposta de controle registra ambos.

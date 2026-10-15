# Observador externo

Implementação local de [Construir e calibrar o observador externo](https://github.com/haroldocurti/wasmhome/issues/10).
O processo Python funciona fora do MCU, sem serial e sem reiniciar a placa.
Usa somente a biblioteca padrão e o [codec v1](protocolo-observacao.md).

## Capturar uma janela

Configurar no firmware o endereço do PC observador e a porta UDP 3333. Usar
um diretório novo para cada execução. O exemplo pressupõe IPs da bancada;
substituir pelos endereços configurados.

```powershell
python tools/external_observer.py --device-ip 192.168.1.50 `
  --bind-ip 192.168.1.20 --seconds 30 --output results/observer/window-001
```

Para observar uma tentativa v1→v2, informar a sequência positiva do manifesto
real e o cenário, `1=native`, `2=interp`, `3=aot`:

```powershell
python tools/external_observer.py --device-ip 192.168.1.50 `
  --bind-ip 192.168.1.20 --seconds 30 --trigger-after 5 `
  --target-sequence 2 --scenario 1 --output results/observer/attempt-001
```

O destino HTTPS permanece configurado no dispositivo. Não há retransmissão
automática. Um único disparo é enviado após aprender o boot por telemetria.
O UUID identifica esta janela; a tentativa normal começa somente após aceite
correlacionado recebido dentro do prazo. Recusa e aceite tardio não começam
uma tentativa. Primeira resposta correta de v2 após aceite registra ativação;
resposta posterior de v1 é uma regressão inválida.

Cadência padrão de 100 ms, timeout de 300 ms e versão inicial v1. Os argumentos
`--period-ms`, `--timeout-ms` e `--initial-version` permitem declarar outra
configuração. `--max-sample-age-ms`, por padrão 1000 ms, limita a idade de uma
leitura válida. Esse limite é uma política provisória do observador, a confirmar
no piloto. Os parâmetros efetivos ficam no JSONL e no resumo.
`--interface-index` seleciona a interface de saída no Windows sem alterar
rotas; `--port` e `--device-port` configuram as portas locais e remotas.

O observador espera telemetria do endereço e porta configurados. Cada novo
boot reinicia os contadores recebidos e fornece o boot dos próximos pedidos.
Boots já substituídos não voltam a ser adotados. UDP não autentica o emissor,
e o protocolo não ordena boots nunca vistos; manter a bancada isolada conforme
as condições do experimento.

## Validar o serviço

Entradas repetidas de 23, 32 e 27 °C ficam fora das bandas de histerese de
ambas as versões. A entrada de 27 °C exige GPIO alto em v1 e baixo em v2.
Isso permite verificar a versão declarada e o resultado sem presumir estado
inicial dentro da banda. Este gerador não mede o comportamento dentro da banda
de histerese nem substitui os testes das regras nas etapas dos módulos.

Uma resposta funcional exige ID enviado, UUID correspondente, boot do pedido
e boot atual, versão permitida, estado `APPLIED`, GPIO solicitado e observado
iguais ao esperado e recepção dentro do prazo. Cada ID conta no máximo uma
vez. Respostas incorretas, tardias e ausentes permanecem no denominador.
Erros de transporte ficam registrados e não ocasionam novo envio.

Telemetria tem validação independente do controle. Leituras inválidas ou
antigas não contam como disponíveis. IDs de aquisição podem repetir durante
publicações de 100 ms; para o mesmo ID, a temperatura não pode mudar. IDs
regressivos são inválidos dentro do boot. Telemetria não possui campo de versão
do módulo no contrato v1, portanto essa versão não é inferida.

Contadores recebidos são verificados por tipo e boot. Lacunas, duplicatas,
conteúdo conflitante com o mesmo contador e mensagens fora de ordem geram
eventos. Uma resposta de controle fora de ordem ainda pode ser válida se
correlacionada dentro do prazo. Telemetria fora de ordem fica no log bruto,
mas não substitui o estado atual. Lacunas preenchidas posteriormente deixam
de contar em `unresolved_sequence_gaps`; a contagem começa no primeiro
contador visto e não estima perdas anteriores à captura.

## Registro e métricas

- `observations.jsonl` preserva intenção de envio, horário planejado, chamada
  efetiva de `sendto`, resultado do transporte, bytes recebidos, endereço do
  emissor, conteúdo decodificado, validações e resumo final. Datagrama inválido
  ou de outro endereço também fica preservado.
- `summary.json` contém enviados, perdas funcionais, atrasos, boots,
  duplicatas, lacunas de sequência e limites temporais da atualização.

O horário de envio é capturado imediatamente antes de `sendto`; seu término
também fica registrado. Recepção usa relógio monotônico do PC. Nenhuma métrica
subtrai relógios do PC e do MCU. O loop mantém o calendário de envio, registra
slots perdidos pelo host e evita rajadas para recuperar atraso. Após a janela
de estímulos, aguarda um timeout adicional para as últimas respostas.

`control_max_gap_ns` é a maior lacuna entre respostas funcionais corretas dentro
do prazo. `control_unavailability_estimate_ns` desconta uma cadência dessa
lacuna, com mínimo zero, conforme o plano. É uma estimativa, sujeita à rede e
ao escalonamento do host. `telemetry_max_gap_ns` mede as leituras disponíveis
separadamente. Sem dois eventos válidos, a lacuna recebe `null`.
`control_tail_censored_ns` e os timestamps inicial/final permitem identificar
janelas que terminam sem recuperação; a lacuna entre respostas não estima
sozinha uma interrupção ainda em andamento. Sem qualquer resposta correta,
não há duração estimada. `lost_controls` inclui todas as requisições sem
resposta correta e pontual, inclusive erros de envio.

`update_total_ns` vai do envio do disparo à primeira resposta correta de v2.
Sem ativação recebe `null`. `update_state` distingue ausência de disparo,
ausência de aceite pontual, aceite sem v2 e ativação observada. Não há prazo
global da atualização congelado nesta etapa. A duração da janela deve conter
as janelas anteriores e posteriores exigidas pelo piloto. O retorno normal
da CLI confirma término da captura; consultar o resumo para o resultado do
serviço e da atualização.

Para OTA nativo com reinicialização, use `tools/analyze_native_update.py` após a
captura. Ele reconcilia os eventos UART nos dois boots com o aceite e os
resultados funcionais UDP. Os contratos e limites estão em
[análise de atualizações assinadas](issue17-module-update-observer.md).

Para trocas modulares assinadas das issues #17/#18, execute depois
`tools/analyze_module_update.py` com o log serial `WASMHOME_UPDATE`,
`observations.jsonl` e `summary.json` da mesma tentativa. O relatório correlaciona
o `attempt_id`, valida o boot, as etapas e a continuidade da geração/hash da
telemetria, e calcula payload, memória, lacunas e recuperação. O contrato e os
limites estão em [Observação de atualizações modulares](issue17-module-update-observer.md).

Logs e resumos existentes não são sobrescritos. O observador reserva JSONL e
resumo com abertura exclusiva antes da rede; os calibradores exigem uma pasta
nova antes de iniciar sockets ou serial. Uma captura interrompida pode deixar
arquivos incompletos, que devem ser preservados e não reutilizados.
Interrupção por Ctrl+C registra evento
e preserva resumo parcial. Encerramento abrupto preserva as linhas já gravadas,
mas pode deixar o resumo ausente. Arquivos mantêm os eventos; a análise desta
primeira versão também conserva IDs e datagramas em memória durante a janela,
portanto deve usar janelas limitadas por tentativa, em vez de captura indefinida.

## Calibração reproduzível

```powershell
python tools/calibrate_observer.py --output results/calibration/same-boot --no-reboot
python tools/calibrate_observer.py --output results/calibration/reboot
.\tools\test-platform.ps1
```

A fixture usa sockets UDP reais em loopback, publica telemetria, aplica o
oráculo de controle e responde ao disparo com aceite e v2. Após um segundo,
suprime telemetria e descarta pedidos por 1000 ms. O ensaio com reboot muda
o boot e reinicia contadores ao terminar a interrupção. A duração conhecida
vem do relógio monotônico da fixture; seu JSONL preserva limites e datagramas.

Antes da captura, a calibração fixa a tolerância de uma cadência sem reboot
e duas com reboot. Neste último caso, a redescoberta pela telemetria pode
consumir uma cadência antes do próximo estímulo. A tolerância não é garantia
para uma rede física com perdas ou um host sobrecarregado. O comando falha
se o erro exceder a tolerância, não houver perdas durante a interrupção,
faltarem aceite/ativação, houver resultado de controle incorreto ou o número
de boots não corresponder ao ensaio.

Os [resultados locais](evidence/observador-externo-2026-10-02.md) calibram a
ferramenta no host. A [integração na placa](evidence/observador-hardware-2026-10-02.md)
também verificou interrupção conhecida, respostas do GPIO físico, versões da
fixture e recuperação após reboot real. Execução dos módulos definitivos e OTA,
captura no LXC e parâmetros congelados da campanha permanecem nas próximas
etapas. A telemetria diagnóstica da plataforma comum não prova funcionamento
dos serviços modulares.

## Integrar na bancada física

A calibração da ferramenta da issue #10 inclui o ESP32-S3. Os serviços nativos
definitivos da issue #11 dependem dela; o gerenciador e os módulos WebAssembly
ficam nas issues #12–#14. Usar a fixture `WASMHOME_OBSERVER_SELFTEST`, desabilitada
por padrão e exclusiva do cenário nativo, para verificar o observador sem
antecipar o aceite desses serviços. Ela exclui o diagnóstico anterior e o
microbenchmark, responde usando GPIO5→GPIO6 e publica o DS18B20 real.

Preservar a região que será gravada, configurar localmente rede e IP do
observador e compilar uma aplicação separada. Na topologia verificada, PC
`192.168.0.190`, interface 9 e placa `192.168.0.145`, usar Python com `pyserial`.
O Python já instalado para ESP-IDF 5.2.6 na bancada contém essa dependência.

```powershell
$taskPython = 'C:\esp\tools-task8\python_env\idf5.2_py3.11_env\Scripts\python.exe'
& $taskPython tools/calibrate_observer_hardware.py --device-ip 192.168.0.145 `
  --bind-ip 192.168.0.190 --interface-index 9 --mode gap --output results/hardware/gap
& $taskPython tools/calibrate_observer_hardware.py --device-ip 192.168.0.145 `
  --bind-ip 192.168.0.190 --interface-index 9 --mode reboot --output results/hardware/reboot
& $taskPython tools/calibrate_observer_hardware.py --device-ip 192.168.0.145 `
  --bind-ip 192.168.0.190 --interface-index 9 --mode v2 --output results/hardware/v2
```

Reconfirmar porta e endereços antes de executar. `--port` seleciona a UART,
por padrão COM12. O wrapper não grava firmware nem reseta pelas linhas de
controle da UART. Envia comandos exclusivos da fixture:

- `WH_V1` e `WH_V2` selecionam a regra de referência, sem substituir artefatos.
- `WH_GAP` bloqueia telemetria e respostas por um segundo, mantendo o sensor
  e a rede ativos. Os eventos seriais delimitam a duração efetiva no mesmo
  relógio do MCU. A estimativa UDP usa exclusivamente o relógio do PC.
- `WH_REBOOT` chama `esp_restart`; o teste exige respostas corretas nos dois
  boots, além de telemetria. A duração física desse reboot não é conhecida,
  portanto não recebe erro de calibração por comparação com um valor fixo.

A fixture rejeita disparos de atualização, com `BUSY` no boot correto ou
`WRONG_BOOT` no incorreto, e registra que não implementa OTA ou módulos.
Não produz aceite de atualização fictício. O ensaio comprova o envio UDP e
a correlação da recusa na placa. Aceite e transição v1→v2 por artefato continuam
nas etapas de atualização; os testes do host cobrem sua lógica de observação.

`firmware.jsonl` preserva comandos e eventos seriais selecionados, junto de
`observations.jsonl` e `summary.json`. `hardware-calibration.json` registra
o resultado. Os timestamps do MCU somente definem a duração conhecida da
pausa no mesmo boot; não são subtraídos de timestamps do PC. Ao terminar
o ensaio, restaurar a aplicação preservada e verificar a retomada do firmware
anterior. Não medir a campanha com a fixture de calibração habilitada.

# Plataforma comum do ESP32-S3

Implementação local da [issue #8](https://github.com/haroldocurti/wasmhome/issues/8).
O [build nativo e os testes locais passaram em 02/10/2026](evidence/plataforma-comum-2026-10-02.md).
Os ensaios no dispositivo estão em andamento. As configurações abaixo são a base
proposta para os três cenários e devem ser preservadas nas evidências de build.
Os [ensaios no ESP32-S3](evidence/esp32s3-hardware-2026-10-02.md) registram
sensor, GPIO, PSRAM, Wi-Fi, UDP e watchdog, com as falhas e os limites encontrados.

## Configuração compartilhada

| Item | Valor |
|---|---|
| ESP-IDF | Família 5.2.x, conforme manifesto existente |
| CPU e FreeRTOS | 240 MHz, tick de 1 ms, gerenciamento dinâmico de energia desligado |
| Flash e partições | 16 MiB; NVS, otadata, PHY e dois slots OTA de 7 MiB |
| PSRAM | Octal, 80 MHz, alocador habilitado |
| Memória interna | Alocações até 16 KiB preferem RAM interna; reserva interna de 32 KiB |
| Wi-Fi | STA, DHCP, sem power save; buffers RX estáticos/dinâmicos 10/32, TX dinâmicos 32 |
| Watchdog | Inicializado pelo IDF; timeout de 5 s, panic, idle dos dois núcleos e tarefas sensor/UDP monitorados |
| Sensor | Um DS18B20 em três fios no GPIO4, 10 bits, aquisição a cada 500 ms |
| Loopback | GPIO5 de saída, inicialmente baixo; GPIO6 de entrada com pulldown |
| UDP | IPv4, porta proposta 3333 em ambos os lados; endereço do observador configurado localmente |
| Tarefas | Sensor: prioridade 5, stack 4096 bytes; UDP: prioridade 5, stack 6144 bytes |

O endereço do observador precisa ser reconfirmado na rede da bancada. Não se
presume que o IP do LXC seja o IP do observador. A senha Wi-Fi fica exclusivamente
no `sdkconfig` local, ignorado pelo Git. O arquivo gerado e o firmware compilado
contêm essa senha e não devem integrar o pacote público de evidências.

`partitions.csv` prepara os slots; atualização OTA e validação do envelope
continuam nas issues seguintes. Não foi implementado download nesta etapa.

## Driver e interfaces

`sensor.c` usa `espressif/onewire_bus` 1.0.2 com backend RMT. Em cada ciclo,
configura o scratchpad em 10 bits e dispara a conversão sem usar a função
bloqueante do componente DS18B20. A espera de 200 ms suspende somente a tarefa
de aquisição. O máximo especificado para 10 bits é 187,5 ms, conforme o
[datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/DS18B20.pdf).
Após essa espera, lê o scratchpad e valida CRC, configuração e faixa física.
O ensaio físico encontrou um resultado zero na consulta isolada de conclusão
com o backend RMT fixado; essa consulta foi removida. A leitura após a espera
prevista no datasheet produziu amostras válidas na bancada, sem alterar a cadência.
Falhas preservam a última leitura válida e seu timestamp. Uma conversão em
curso não invalida a amostra anterior. Não há persistência de configuração no
EEPROM do sensor. O uso de Skip ROM pressupõe o único sensor da bancada.

`wh_sensor_latest()` retorna uma cópia protegida do estado, sem bloquear durante
a conversão. `wh_gpio_apply()` comanda o pino e lê o nível do loopback físico.
Estas interfaces não contêm limiar, histerese ou regra de aplicação.

`observation_wire.c` codifica o cabeçalho em ordem de rede e valida os cinco
tipos do protocolo v1. `platform.c` recebe apenas pedidos de controle e disparos
de atualização do endereço/porta configurados. Descarta estruturas inválidas
sem resposta e descarta controle destinado a outro boot. O handler recebe um
disparo válido com boot divergente para produzir a recusa semântica prevista.

`wh_observation_set_handler()` instala o serviço da próxima etapa. O callback
roda na tarefa UDP, deve terminar rapidamente e não pode reter o buffer. Sua
responsabilidade inclui deduplicação, conflito de IDs, recusas semânticas e
execução das regras. Enquanto não houver handler, o firmware registra
`service_not_installed` e não simula respostas funcionais nem aceite de download.
`wh_observation_send()` envia telemetria, resultado ou aceite, validando o corpo.
Os contadores são independentes por tipo, sobrevivem a reconexões e nunca
reutilizam um valor consumido por falha de envio. A publicação periódica de
telemetria pertence aos módulos das issues #11–#14.

## Instrumentação

Cada boot recebe um identificador aleatório não zero. `WASMHOME_EVENT` contém
evento, cenário, boot em hexadecimal, tempo monotônico, código de estado e heap interno.
`boot_observed` usa o campo `status` para registrar `esp_reset_reason()`.
Os demais eventos incluem inicialização, associação/perda de Wi-Fi, bind/falha
do UDP e erros de aquisição. São diagnóstico serial. `platform_initialized`,
`wifi_ip_acquired` e `udp_bound` não comprovam disponibilidade funcional.

Durante a execução, a plataforma também emite a linha fixa
`WASMHOME_UART_HEARTBEAT,v1` no intervalo configurável
`CONFIG_WASMHOME_UART_HEARTBEAT_INTERVAL_MS` (500 ms por padrão; faixa segura
de 500–750 ms para o lease remoto de 2 s). Ela não contém estado da aplicação,
identificadores ou dados de rede, e não modifica o protocolo nem a cadência UDP.

O benchmark preliminar fica desabilitado por padrão. Para a prova específica
de execução da issue #9, habilitar `WASMHOME_RUN_BENCHMARK` e registrar esse
desvio. As linhas CSV continuam sendo diagnóstico, não tentativas de atualização.

## Compilação e configuração local

Em um ambiente ESP-IDF 5.2.x, com target ESP32-S3:

```powershell
idf.py -B build-native -DSDKCONFIG=build-native/sdkconfig -DEXPERIMENT_SCENARIO=native set-target esp32s3
idf.py -B build-native -DSDKCONFIG=build-native/sdkconfig -DEXPERIMENT_SCENARIO=native menuconfig
idf.py -B build-native -DSDKCONFIG=build-native/sdkconfig -DEXPERIMENT_SCENARIO=native build
```

Configurar SSID, senha e IPv4 do observador em `WasmHome common platform`.
Com IPv4 vazio, UDP permanece desabilitado e registra esse estado. Os outros
dois cenários devem usar `build-interp/sdkconfig` e `build-aot/sdkconfig`, com
os defaults específicos e artefatos exigidos pelo harness. Sua viabilidade
WAMR/AOT ainda pertence à issue #9.

Não executar flash como consequência do build. Conferir porta, preservar os
dados existentes e autorizar a gravação antes de testar na placa.

## Verificação

No Windows com Visual Studio C instalado:

```powershell
.\tools\test-platform.ps1
# Outra instalação:
.\tools\test-platform.ps1 -VcVars 'C:\caminho\VC\Auxiliary\Build\vcvars64.bat'
```

O script compila o codec C com warnings tratados como erros e executa os
testes Python contra a DLL real. Compara os vetores publicados, todas as
mutações de um byte, truncamentos e 5.000 mutações combinadas com o codec
de referência. Testa temperatura negativa, limites, CRC e resolução do sensor.
Sem `WASMHOME_WIRE_LIBRARY`, esses testes C ficam explicitamente ignorados.

Antes do aceite da issue, preservar:

- Build ESP-IDF, revisão exata, versões dos componentes e configurações efetivas
  sem credenciais; tamanho e hash da imagem e tabela de partições.
- Logs de boots distintos, leitura do sensor, desconexão/CRC inválido e retomada.
- GPIO solicitado/observado com o jumper presente e removido.
- Associação Wi-Fi, tráfego UDP nas duas direções e retomada após reboot do watchdog. A perda e reconexão do enlace Wi-Fi dentro do mesmo boot ainda não foram ensaiadas.
- Teste de watchdog e evidência de que diagnóstico não é contado como serviço.

Os testes no host validam o codec e a interpretação do scratchpad. Não validam
RMT, PSRAM, Wi-Fi, GPIO, watchdog ou tráfego no ESP32-S3.

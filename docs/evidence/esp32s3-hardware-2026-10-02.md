# Ensaios da plataforma no ESP32-S3

Data: 02/10/2026. Diagnóstico da issue #8, separado de disponibilidade funcional
dos módulos e de resultados das campanhas.

## Placa e preservação

Leitura ao vivo na COM12 com esptool 4.12.0 confirmou ESP32-S3 QFN56, revisão
v0.2, flash de 16 MiB e PSRAM AP de 8 MiB/3,3 V. Antes de gravar, foi feita uma
cópia completa de 16.777.216 bytes da flash. Ela permanece local em
`build-hardware/flash-before-test-2026-10-02.bin`, SHA-256
`9435f250d8aef09305c554d84e9a39faa8fda633a0979fca7865cb11d5f9cf3b`.
Não foram alterados eFuses, rotas do sistema, firewall ou equipamentos da rede.

O firmware diagnóstico habilita `WASMHOME_PLATFORM_SELFTEST`, desabilitado
por padrão. Usa os drivers e o transporte da plataforma para emitir leituras
reais e alternar GPIO5, observando GPIO6. A telemetria deste fixture não é a
implementação do módulo da campanha. Seu handler apenas registra pedidos
recebidos; não executa controle nem aceita atualização.

## Resultados comprovados

| Verificação | Resultado |
|---|---|
| Flash e boot | Gravação dos quatro segmentos concluída; hash verificado pelo esptool em cada segmento |
| PSRAM | Boot identificou 8 MiB a 80 MHz; `SPI SRAM memory test OK`; reserva interna de 32 KiB registrada |
| DS18B20 | Leituras com CRC, resolução e faixa válidos; 27,50–27,75 °C nos ensaios positivos |
| GPIO | Solicitação 0 observada como 0; solicitação 1 observada como 1; API sem erro |
| Wi-Fi | Associação à `Hal9000`, canal 5/BW20; DHCP `192.168.0.145`, gateway `192.168.0.1` |
| UDP dispositivo → PC | 177 datagramas válidos no ensaio de 20 s com interface Ethernet fixada; sequência crescente dentro do boot |
| UDP PC → dispositivo | Pedido válido, sequência 1, ID 291, registrado pelo handler diagnóstico |
| Boot errado | Pedido com ID 292 registrado como `control_wrong_boot` e não despachado ao handler |
| Datagramas truncados | Descartados antes do handler, sem resposta de serviço |
| Watchdog | Comando serial diagnóstico `WH_WDT` suspendeu a tarefa monitorada; log de TWDT identificou `wh_diag`, abortou e reiniciou |
| Identificação de boot | IDs `e46e46310723c1fc` e `fb8c18c6435211f9`; motivo do segundo reset 6, `ESP_RST_TASK_WDT` |
| Retomada após watchdog | Nova associação Wi-Fi e leituras válidas; 301 datagramas no ensaio de 40 s, contadores reiniciados somente no novo boot |
| Observação sem reset | 120 s no boot `c6e0799686eecc62`: 1.200 datagramas, 120 linhas de diagnóstico válidas, 27,75–28,00 °C e nenhum evento de erro |
| Retirada do fio do sensor | Estado `WH_SENSOR_DISCONNECTED`, erro de aquisição 261; amostra 3957 e temperatura de 28,50 °C preservadas, idade crescente até saturar em 65.534 ms |
| Retirada do jumper | GPIO5 solicitado em 1 e GPIO6 observado em 0; a diferença aparece no diagnóstico, com API GPIO sem erro |
| Reconexão dos dois fios | 20 s, 200 datagramas válidos e 20 linhas de diagnóstico com sensor válido; GPIO 0→0 e 1→1; nenhum evento de erro, desconexão Wi-Fi ou reset |

Os 17 testes locais passaram após a correção do driver. Também passou um build
nativo com diagnóstico desabilitado, em `build-native-validated`. A imagem
nativa tem 744.752 bytes; o diagnóstico gravado tem 767.376 bytes. Nenhum desses
ensaios executou OTA, intérprete WAMR, AOT ou troca de módulos.

## Desconexão e recuperação físicas

O usuário retirou somente o jumper GPIO5→GPIO6 e o fio de dados GPIO4 do
DS18B20, mantendo alimentação e GND. A coleta `physical-transition` de 45 s
registrou leituras válidas antes da retirada e falhas depois, com 450 pacotes
UDP. A coleta seguinte, `physical-fault`, preservou 449 pacotes e o estado de
desconexão do sensor. O jumper foi reconectado perto do final dessa coleta,
que também registrou o retorno de GPIO6 ao nível solicitado.

Durante a falha, o ID da amostra permaneceu em 3957 e a temperatura em
28,50 °C. A idade aumentou de 357 ms até o teto de 65.534 ms do campo UDP,
sem transformar a leitura antiga em uma nova amostra válida. O diagnóstico
serial preserva a idade sem essa saturação. GPIO6 permaneceu em zero nas
solicitações de nível alto enquanto o jumper estava ausente.

Após o usuário confirmar a reconexão de ambos os fios, `physical-recovered`
registrou 200 pacotes em 20 s, todos com sensor válido, IDs de amostra de
3998 a 4038 e idade máxima de 457 ms. As 20 linhas de diagnóstico confirmaram
os dois níveis do GPIO e temperaturas de 28,25–28,50 °C, sem eventos de erro.
As três coletas mantiveram o boot `c6e0799686eecc62`, contadores UDP crescentes
e nenhum evento de desconexão Wi-Fi. A recuperação não exigiu reset ou flash.
As coletas foram passivas, sem pedidos de controle.

## Falhas encontradas e correções

1. O driver consultava um único bit de conclusão após esperar 200 ms. Essa
   consulta retornou zero no backend RMT usado na placa, causando estado
   `NOT_READY` e ausência de amostras. A consulta foi removida. A espera de
   200 ms cobre os 187,5 ms máximos previstos para 10 bits no
   [datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/DS18B20.pdf).
   Em seguida, o scratchpad é lido e CRC, configuração de 10 bits e temperatura
   são validados. Os ensaios seguintes produziram amostras válidas. A causa
   interna do retorno zero no RMT não foi isolada.
2. O SSID inicial `hal9000` produziu motivo 201, `NO_AP_FOUND`. O usuário
   confirmou o nome exato `Hal9000`; após corrigir a caixa, a placa associou.
3. A primeira ferramenta enviou pedidos assim que apareceu o IP, antes do
   evento de bind UDP, e recebeu WinError 10054. Passou a esperar `udp_bound`;
   ICMP Port Unreachable agora é preservado no log, sem retransmissão automática.
4. Com o socket na interface padrão, os três pedidos foram descartados, inclusive
   o válido. O Windows tinha duas rotas `192.168.0.0/24`, com preferência pela
   Tailscale. O teste fixou somente seu socket na Ethernet 2, índice 9, com
   origem `192.168.0.190`. O pedido válido e as duas rejeições então seguiram
   os caminhos esperados. A seleção usa
   [IP_UNICAST_IF](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options);
   não foi modificada a tabela de rotas.

## Evidências preservadas

O diretório [esp32s3-2026-10-02](esp32s3-2026-10-02/) contém JSONL, resumos e
trechos seriais selecionados. Inclui a primeira falha do sensor, o SSID incorreto,
o erro de bootstrap UDP, a coleta pela interface padrão, o watchdog, o ensaio
positivo com Ethernet e a retirada/reconexão físicas dos fios. As tentativas
malsucedidas permanecem registradas.
Não são dados da campanha normal ou da campanha complementar de resiliência.

Senhas, sdkconfigs locais, firmware com credenciais, backups e serial bruta
permanecem em diretórios ignorados. O material selecionado foi verificado para
não conter a credencial Wi-Fi configurada.

## Reprodução do diagnóstico

Habilitar `WASMHOME_PLATFORM_SELFTEST` apenas na bancada documentada. Configurar
a rede e o IP do observador localmente, compilar, preservar a flash existente e
gravar os segmentos definidos pelo build. Para este PC e a topologia observada:

```powershell
python tools/probe-platform.py --port COM12 --seconds 20 --bind-ip 192.168.0.190 --interface-index 9 --output build-hardware/ethernet-probe
python tools/probe-platform.py --port COM12 --seconds 40 --watchdog --bind-ip 192.168.0.190 --interface-index 9 --output build-hardware/watchdog-probe
```

O primeiro comando reinicia a placa e observa a plataforma; o segundo também
provoca o timeout do watchdog pela UART. Não usar esse fixture para medir
disponibilidade funcional. Reconfirmar porta, IPs e índice da interface antes
de repetir. A credencial da bancada não deve entrar nos argumentos ou no Git.

## SHA-256 das imagens

| Imagem local | Hash |
|---|---|
| Diagnóstico gravado | `e4203d59c92e73357cfe66b0a415b2e99a8bac2a596f64589bf43e6457b6db7f` |
| Nativo sem diagnóstico | `34ac78bf105b09b75aec1e66f80d4310fcd124ee6ef68cb23ae1f4b461af450d` |

## Pendências

- Integrar a implementação na `main` após revisão. A issue permanece aberta.
- Demonstrar os módulos de serviço, calibrar o observador e executar as
  atualizações nas respectivas issues. Pacote diagnóstico ou log serial não
  conta como serviço prestado.

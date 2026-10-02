# Integração do observador no ESP32-S3

Data: 02/10/2026. Complemento físico de
[Construir e calibrar o observador externo](https://github.com/haroldocurti/wasmhome/issues/10).
Implementação e evidências locais na branch `codex/issue-10-external-observer`.

## Escopo e firmware

A issue exige uma ferramenta externa capaz de medir o serviço durante
reinicializações do ESP32-S3. A validação no host foi complementada pela placa
real. Os serviços nativos definitivos da issue #11 dependem da conclusão desta
ferramenta; firmware-base, módulos e atualizações continuam nas demais issues.

`WASMHOME_OBSERVER_SELFTEST`, desabilitada por padrão, habilita uma fixture
de calibração exclusiva do cenário nativo. Ela responde aos estímulos usando
GPIO5 e lê GPIO6, publica dados reais do DS18B20 e oferece pausa controlada
e reboot via comandos seriais. Versões v1 e v2 são regras da fixture,
selecionadas por serial. Não são artefatos atualizados por OTA ou módulos WAMR.
Disparos UDP são recebidos e recusados; a fixture não fabrica aceite de download.

ESP-IDF 5.2.6 compilou a aplicação de ensaio sem warnings ou erros. A imagem
tem 768.096 bytes, SHA-256
`e98c2a85ee21a1126e4d117045ec0ea6fb8e9e876475d852c3a79f18e13ed374`.
O build nativo com fixture desabilitada também foi verificado.
O MSVC compilou o codec C em C11 `/W4 /WX` e os 28 testes passaram, sem skips.
Os dois testes novos do avaliador físico exigem duração conhecida no mesmo
boot e recuperação funcional após reboot; receber telemetria sozinha não basta.

## Preservação e gravação

COM12 reconfirmou ESP32-S3 QFN56 v0.2, flash de 16 MiB e PSRAM de 8 MiB.
Duas tentativas de copiar toda a flash, a 921600 e 460800 baud, falharam por
quadros seriais incompletos; não produziram cópia completa nem autorizaram
gravação. Logs de falha permanecem em `build-observer-selftest`, ignorado pelo Git.

A gravação foi restringida à aplicação em `0x20000`. Antes dela, 1.048.576 bytes
desse endereço foram lidos com sucesso a 115200 baud. A região cobre toda a
imagem do ensaio e o alinhamento dos setores que ela ocupa. A aplicação
preservada corresponde byte a byte à imagem diagnóstica que estava na placa.
Backup local `build-observer-selftest/app-before-observer.bin`, SHA-256
`b00a479e56a1a486703093e93683de845dd09ae5392152a10f49fe9b15cde171`.

Somente essa região de aplicação foi escrita, com hash verificado pelo esptool.
Bootloader, tabela de partições, `otadata`, NVS e eFuses não foram gravados
pelos comandos de flash. Nenhuma rota, firewall ou equipamento de rede foi
reconfigurado. Credenciais, backup, sdkconfig, imagens e logs de build/flash
permanecem locais em diretórios ignorados.

## Resultados físicos

PC `192.168.0.190`, Ethernet 2, índice 9; placa `192.168.0.145`; UDP 3333.
Os ensaios usaram cadência de 100 ms, timeout de 300 ms e duas versões da
fixture. A duração conhecida da pausa vem da diferença entre os eventos
`observer_fixture_gap_start` e `observer_fixture_gap_end` no mesmo relógio
do MCU. A lacuna UDP é calculada no relógio do PC; os relógios não são misturados.

| Ensaio | Duração conhecida | Estimativa externa | Erro absoluto | Resultado |
|---|---:|---:|---:|---|
| Pausa em v1 | 1003,158 ms | 1010 ms | 6,842 ms | Passou, resolução de 100 ms |
| Pausa em v2 | 1002,940 ms | 1009 ms | 6,060 ms | Passou, resolução de 100 ms |
| Reboot real | Não fixada | 2416 ms | Não aplicável | Recuperação funcional comprovada |

A pausa em v1 registrou 99 pedidos, 89 respostas corretas e 10 perdas. A
pausa em v2 registrou 99 pedidos, 88 respostas corretas e 11 perdas.
Nenhum dos ensaios registrou resposta de controle incorreta. A leitura do GPIO
coincidiu com a saída exigida pela versão e pelo estímulo, incluindo 27 °C,
que distingue v1 de v2. Telemetria válida do DS18B20 também foi observada.

No reboot, o processo externo permaneceu ativo. O comando `WH_REBOOT` e o
novo evento de boot foram registrados por serial. O observador registrou
boots `b8cce96bfe66be96` e `4f9a4bb63197dee6`, contadores separados por boot,
199 pedidos, 175 respostas corretas e 24 perdas. Foram 30 respostas corretas
antes e 145 depois do reset. A estimativa de 2416 ms desconta uma cadência
da maior lacuna funcional e inclui boot, associação Wi-Fi, descoberta e rede;
não é uma duração interna exata do reset. Não há duração conhecida para
comparar nesse ensaio, portanto erro de calibração recebe `null`.

Os ensaios v1 e reboot enviaram disparos UDP correlacionados com recusa
`BUSY`, sem iniciar tentativa normal. O ensaio v2 selecionou a regra pela
UART e não enviou disparo de atualização. Não houve HTTPS, validação de
artefatos, OTA, atualização WAMR ou execução das campanhas.

## Evidências

- [Pausa em v1](observer-2026-10-02/hardware-gap/hardware-calibration.json),
  [resumo](observer-2026-10-02/hardware-gap/summary.json),
  [UDP bruto](observer-2026-10-02/hardware-gap/observations.jsonl),
  [comandos e eventos seriais](observer-2026-10-02/hardware-gap/firmware.jsonl).
- [Reboot físico](observer-2026-10-02/hardware-reboot/hardware-calibration.json),
  [resumo](observer-2026-10-02/hardware-reboot/summary.json),
  [UDP bruto](observer-2026-10-02/hardware-reboot/observations.jsonl),
  [comandos e eventos seriais](observer-2026-10-02/hardware-reboot/firmware.jsonl).
- [Pausa em v2](observer-2026-10-02/hardware-v2/hardware-calibration.json),
  [resumo](observer-2026-10-02/hardware-v2/summary.json),
  [UDP bruto](observer-2026-10-02/hardware-v2/observations.jsonl),
  [comandos e eventos seriais](observer-2026-10-02/hardware-v2/firmware.jsonl).

A reprodução está em [Observador externo](../observador-externo.md).
Esses resultados comprovam integração e calibração do observador usando
um respondedor físico de referência. Não dão aceite aos serviços definitivos
das issues #11–#14 nem aos mecanismos de atualização ou à campanha.

## Restauração da bancada

Depois dos três ensaios, a região preservada de 1 MiB foi restaurada em
`0x20000`, com hash verificado pelo esptool. O firmware diagnóstico anterior
voltou a executar. Uma captura passiva de seis segundos, sem novos estímulos
ou reset, recebeu 60 datagramas com contadores crescentes no boot
`f35780170073574a`. As seis linhas de diagnóstico mostraram sensor válido a
28 °C e ambos os níveis do GPIO corretamente observados, sem erros.
O [resumo da retomada](observer-2026-10-02/restored-platform-summary.json)
é diagnóstico da plataforma restaurada. A fixture de calibração não permaneceu
gravada na placa.

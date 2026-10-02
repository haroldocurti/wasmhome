# Verificação local da plataforma comum

Data: 02/10/2026. Issue #8 em andamento. Implementação na branch
`codex/issue-8-common-platform`. Este registro descreve a verificação local;
a publicação no GitHub é acompanhada nos comentários da issue.

## Resultado

- Build nativo ESP32-S3 concluído, incluindo `main.c`, `platform.c`, `sensor.c`
  e `observation_wire.c`. Nenhum warning ou erro foi encontrado no log.
- Imagem de aplicação: 744.736 bytes, `0xb5d20`. Cada slot OTA tem 7.340.032
  bytes; a verificação de tamanho do ESP-IDF passou.
- Codec C compilado pelo MSVC em C11 com `/W4 /WX`.
- 17 testes passaram, sem skips. Incluem os cinco vetores do contrato, 79.872
  mutações de um byte, 5.000 mutações combinadas e rejeições do scratchpad.
- `git diff --check` passou.

## Ambiente usado

| Componente | Versão ou revisão |
|---|---|
| ESP-IDF | v5.2.6, `9ef24e3e2a2c96e720d83c574a3f8699177573da` |
| Toolchain Xtensa | GCC 13.2.0, pacote `esp-13.2.0_20250707` |
| Python do IDF | 3.11.2, venv separado |
| CMake / Ninja | 3.30.2 / 1.12.1 |
| Component manager | 2.5.2 |
| esptool | 4.12.0 |
| onewire_bus | 1.0.2, hash `c4c781940d8bd988432b47ac3ee68479c69e14e01fdfba0b61365b233a3ba5d6` |
| wasm-micro-runtime | 2.4.0~1, hash `04f25aad2896b5e906397a061d35cce560609bebd3913a4be7e365ecbf2dd9d6` |
| MSVC | 14.51.36231 |
| Python dos testes | 3.14 |

SDK e ferramentas foram preparados somente no Windows, nos diretórios
`C:\esp\v5.2.6-task8` e `C:\esp\tools-task8`. Isso não é instalação ou aceite da
toolchain reproduzível no LXC da issue #7. O componente WAMR foi resolvido e
compilado pelo build, mas não houve execução nem validação de intérprete/AOT.

## Reprodução da verificação

```powershell
$env:IDF_TOOLS_PATH = 'C:\esp\tools-task8'
$env:PATH = 'C:\esp\tools-task8\python_env\idf5.2_py3.11_env\Scripts;' + $env:PATH
. C:\esp\v5.2.6-task8\export.ps1
idf.py -B build-native -DSDKCONFIG=build-native/sdkconfig -DEXPERIMENT_SCENARIO=native -DIDF_TARGET=esp32s3 build
.\tools\test-platform.ps1
git -c core.autocrlf=false diff --check
```

O log completo ficou em `build-native/task8-build.log`, ignorado pelo Git.
SSID/password/observador não foram usados para realizar associação ou tráfego.
O benchmark está desabilitado. A configuração gerada confirmou CPU de 240 MHz,
tick de 1 ms, flash de 16 MB, tabela customizada, PSRAM Octal de 80 MHz,
reserva interna de 32 KiB, limite preferencial interno de 16 KiB, buffers Wi-Fi
RX 10/32 e TX dinâmicos 32, e watchdog de 5 s com panic e os dois idle tasks.

## SHA-256

| Arquivo | Hash |
|---|---|
| `build-native/wasmhome_bench.bin` | `330db5edd42b76e966932c57cc66f0f91b72a5b26dea518e0a152f6f6c9c237a` |
| `build-native/partition_table/partition-table.bin` | `f1e567915e11bc9c49e65fe894f631ff59ef8c971da2aee4437bb8e70a262300` |
| `dependencies.lock` local | `8b6aace6c1f7c2124acdb019a0b799bfd1db8cd012d116262cf6bed77758a177` |
| `main/platform.c` | `380e34a41147b9b0449eaae2b59b994718e8695e57aca299f8620ab0b17669d7` |
| `main/sensor.c` | `ea889df315b41059f05d6afdf4f05dfab09fc17012feb2d42419fb3d56ac702f` |
| `main/observation_wire.c` | `9409c0358cc9013af7bdafda311c30a8aefbb996edf0fb329ff061243186c836` |

O hash da imagem identifica este build local; não é promessa de reprodução
binária em outro caminho ou ambiente. O lock e as imagens permanecem locais.

## Limite da evidência

Neste primeiro build local não houve flash, acesso serial ao dispositivo, associação Wi-Fi, tráfego UDP,
leitura física do DS18B20, teste do jumper, reset de watchdog ou ensaio da PSRAM.
Naquele momento, os três critérios da issue #8 estavam sem aceite na placa.
Os ensaios físicos posteriores estão no [registro de diagnóstico](esp32s3-hardware-2026-10-02.md).
A camada comum
fornece transporte e drivers; regras, deduplicação e publicação periódica dos
serviços permanecem nas issues #11–#14. Este registro não comprova merge ou
fechamento da issue.

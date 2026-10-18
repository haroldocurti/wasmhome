# Bancada experimental WasmHome

Português | [English](README.en.md)

Este repositório contém um harness preliminar para os três cenários definidos
no TCC. Consulte o [plano de execução por etapas](docs/plano-de-execucao.md)
para conhecer o escopo, as dependências e os critérios de aceitação.

| ID | Cenário | Artefato executado | Conteúdo da atualização |
|---|---|---|---|
| A | C nativo | Firmware ESP-IDF | Imagem completa da aplicação OTA |
| B | Interpretador WAMR | `benchmark.wasm` | Módulo Wasm |
| C | WAMR AOT | `benchmark.aot` | Módulo WAMR AOT para Xtensa |

A carga determinística de inteiros em `workload/benchmark.c` é um harness
preliminar de execução. Ela não é a aplicação final da campanha nem o Embench.
A campanha compara os serviços comuns de telemetria e controle de GPIO definidos
em `CONTEXT.md` e no [plano de execução por etapas](docs/plano-de-execucao.md).

## Estado atual

Estado sincronizado em 2026-10-13 com base nas evidências de 2026-10-12. O
[registro de retomada no Linux](docs/retomada-linux.md) documenta as entregas
integradas do observador/LXC, a preparação do SDK, o LLVM/wamrc R5 compatível e
os builds verificados dos firmwares A/B/C no CT107. Os artefatos Wasm/AOT foram
reproduzidos com hashes idênticos; consulte as
[evidências dos builds E1](docs/evidence/retomada-linux-2026-10-12/e1-scenarios/README.md).
Ainda faltam os testes locais das alterações mais recentes, a atestação
independente do compilador e a revisão/aceitação do item #7. A execução A/B/C
no ESP32-S3 (#9), os serviços da campanha, as atualizações e as campanhas
experimentais também estão pendentes. Nenhum firmware foi gravado durante a
retomada de 2026-10-12.

O contrato de observação UDP, o codec de referência executado no computador, o
harness do firmware, a carga de trabalho, o gerador de artefatos, o coletor de
logs e os testes locais automatizados estão disponíveis. O código-fonte da
plataforma comum agora inclui aquisição de sensores, loopback de GPIO, Wi-Fi e
transporte UDP embarcado. Consulte a
[configuração e validação da plataforma](docs/plataforma-comum.md). Os
diagnósticos iniciais da plataforma ESP32-S3 estão registrados na
[verificação de hardware](docs/evidence/esp32s3-hardware-2026-10-02.md). O
[observador externo](docs/observador-externo.md) agora envia sondas funcionais
de controle e gatilhos de atualização, preserva eventos JSONL e permite
calibração UDP local com reinicialização simulada. A
[calibração no ESP32-S3](docs/evidence/observador-hardware-2026-10-02.md)
também cobre interrupções conhecidas, loopback físico de GPIO e reinicialização
real. OTA, substituição dinâmica e integração com os módulos da campanha ainda
precisam ser validadas nas respectivas etapas.

Trinta chamadas atuais não equivalem a trinta atualizações: o carregamento
ocorre uma vez por inicialização. A plataforma comum foi compilada localmente
com ESP-IDF 5.2.6 em 2026-10-02; o
[registro de verificação](docs/evidence/plataforma-comum-2026-10-02.md)
identifica as versões das ferramentas e os hashes das imagens. As evidências de
2026-10-12 também registram os SDKs verificados, a execução do `wamrc` no
computador e os builds A/B/C no CT107. A execução interpretada/AOT no
dispositivo ainda precisa ser validada; compilar no computador e reproduzir
artefatos não comprova a viabilidade física.

## Pré-requisitos

- Placa de desenvolvimento ESP32-S3
- ESP-IDF 5.2.6 no Git SHA `9ef24e3e2a2c96e720d83c574a3f8699177573da`
- Python 3.11 ou mais recente
- Pacote oficial WASI-SDK 27.0 para Linux x86_64 e extração verificada
- `wamrc` candidato compilado a partir do WAMR upstream 2.4.5, SHA `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, com LLVM para Xtensa
- Runtime candidato independente `wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.3; consulte [port candidato e reprodução](docs/runtime-port-candidato.md)

## Compilação

Estes comandos são provisórios. A etapa E1 precisa validar as versões dos
componentes/compiladores e isolar os arquivos SDKCONFIG por cenário antes do uso
experimental.

Primeiro obtenha e verifique o código-fonte candidato; em seguida, gere os dois
artefatos modulares. A geração é executada no PowerShell para Linux e exige uma
revisão limpa do WasmHome e o comprovante real do build doador descrito no
documento do candidato. Sem essa comprovação, a geração é bloqueada antes de
qualquer gravação. A revisão independente e a validação física de B/C continuam
pendentes:

```powershell
python tools/runtime_package.py --acquire
$env:WASI_SDK_PATH = '/tools/wasi-sdk-27.0-x86_64-linux'
$env:WAMRC = '/tools/wamrc'
$env:IDF_PATH = '/tools/esp-idf'
./tools/build-artifacts.ps1 -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
```

Depois, compile cada firmware em um terminal PowerShell do ESP-IDF:

```powershell
idf.py -B build-native -DSDKCONFIG=build-native/sdkconfig -DEXPERIMENT_SCENARIO=native set-target esp32s3 build
idf.py -B build-interp -DSDKCONFIG=build-interp/sdkconfig -DEXPERIMENT_SCENARIO=interp `
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.interp' set-target esp32s3 build
idf.py -B build-aot -DSDKCONFIG=build-aot/sdkconfig -DEXPERIMENT_SCENARIO=aot `
  '-DSDKCONFIG_DEFAULTS=sdkconfig.defaults;sdkconfig.aot' set-target esp32s3 build
```

Configure localmente o Wi-Fi e as opções do observador antes de usar a
plataforma. Ative `WASMHOME_RUN_BENCHMARK` para a verificação preliminar de
execução. Nesse modo, o firmware emite uma linha CSV para cada repetição medida,
após cinco execuções de aquecimento. Antes de coletar dados, consulte o
[protocolo experimental](docs/experiment-protocol.md).

## Firmware do gerenciador de módulos (#12)

Os fontes convidados ABI-v1 são `workload/service_telemetry.c` e
`workload/service_control.c`. `tools/generate_service_modules.py` compila as
imagens Wasm e Xtensa AOT e gera os arquivos C embarcados no diretório ignorado
`artifacts/`. O script de build executa esse gerador, verifica os comprovantes
da cadeia de ferramentas e fornece a cada cenário seu próprio `sdkconfig` e
diretório de build. Os serviços gerados comprovam o carregamento e a ABI do
gerenciador. Uma prova física mínima no ESP32-S3-N16R8 executou telemetria e
controle em conjunto no perfil de interpretador clássico, incluindo loopback de
GPIO; a execução física de AOT faz parte do item #14. Consulte as
[evidências físicas do #12](docs/evidence/issue-12-allocator-alignment-2026-10-15/README.md)
para ver resultados, limites e detalhes da restauração.

Defina `WASI_SDK_PATH`, `WAMRC` e `IDF_PATH`; depois, compile um dos cenários
dedicados no ambiente PowerShell do ESP-IDF:

```powershell
./tools/build-module-firmware.ps1 -Scenario module-interp `
  -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
./tools/build-module-firmware.ps1 -Scenario module-aot `
  -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
```

Esses builds usam o perfil sdkconfig de serviços modulares, definem o alvo como
ESP32-S3 e habilitam o alocador do WAMR com contabilização de uso, sem alterar o
pacote de runtime fixado. Mantenha as credenciais de Wi-Fi e do observador em um
sdkconfig local, fora do perfil versionado. Em tempo de execução, o firmware
emite snapshots `WASMHOME_MODULE_MEMORY` nos limites de inicialização, carga e
instanciação de slots, sobreposição de candidatos e liberação. Eles separam as
leituras de heap do ESP-IDF dos contadores do alocador WAMR e classificam a
capacidade dos payloads da fila, as pilhas de worker solicitadas e os slots do
watchdog retidos como categorias do gerenciador. Os handles e gates dos timers
do watchdog permanecem nos slots estáticos entre gerações porque o ESP-IDF
5.2.6 despacha a exclusão de timers de forma assíncrona; callbacks atrasados
verificam o prazo da chamada ativa.

## Verificações locais

```powershell
python -m unittest discover -s tests -v
python tools/collect_results.py --help
```

Passar nessas verificações comprova localmente os contratos da carga de trabalho
e do parser. Isso não comprova que o ESP-IDF compila, que os módulos carregam no
ESP32-S3 ou que AOT funciona na placa selecionada.

O build interpretado seleciona intencionalmente o interpretador clássico do
WAMR porque esse é o modo definido na proposta de pesquisa. Alterá-lo para o
interpretador rápido cria um cenário experimental diferente e essa mudança
precisa ser registrada.

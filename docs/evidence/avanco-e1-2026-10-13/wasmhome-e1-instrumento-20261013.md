# Preparação E1 — memória e validador offline (#9)

Data: 2026-10-13  
Repositório: `/home/haroldocurti/Documents/wasmhome`  
Base observada: `0ba2faab16dc9671f89a663eea8e6d0d1f528997`  
Estado: mudanças locais não commitadas; sem flash, serial ou acesso ao hardware.

## Implementação

`main/main.c` emite linhas JSON `WASMHOME_MEMORY,` vinculadas ao cenário e `boot_id`, com unidade em bytes. Os eventos cobrem `before_runtime`, `after_init`, `after_load`, `after_instantiate`, `after_exec_env_lookup`, `after_warmup`, cada amostra antes/depois de cada chamada medida e `after_release`. Para builds nativos, as fases do runtime WAMR ficam explicitamente `not_applicable`.

Cada amostra contém total, livre e maior bloco livre para `MALLOC_CAP_INTERNAL` e, se `CONFIG_SPIRAM` estiver habilitada, `MALLOC_CAP_SPIRAM`. Sem essa configuração, os valores PSRAM são `null/not_applicable`. A linha JSON inteira é formatada em buffer local limitado (`snprintf`) e entregue em uma única chamada `printf`, após colher as métricas. O teste host injeta uma linha `WASMHOME_EVENT` imediatamente após cada emissão concluída e exige uma única chamada de saída por evento; um teste separado confirma que o parser rejeita JSON de memória quebrado por uma linha intercalada. Os snapshots declaram `phase_peak_free_bytes:null` e `phase_peak_status:not_instrumented`; não chamam o mínimo global desde o boot de pico da fase. A memória estática continua marcada como externa ao evento, com fonte ELF e bytes `null`. O CSV anterior e o intervalo medido de `execute_us` foram preservados: a amostra pré-chamada vem antes do timer; a pós-chamada ocorre depois de calcular o tempo.

`tools/validate_e1_device.py` é um verificador offline independente do coletor histórico. Exige manifesto com os cenários `native/interp/aot`, artefatos ELF/BIN presentes e hashes coincidentes, identidade declarada da imagem gravada, logs com hashes, boot IDs distintos, checksum `0xDBB2F0D4`, cinco warmups, exatamente 30 linhas CSV válidas por cenário e eventos de memória completos/ordenados. Também rejeita valores PSRAM zerados quando ausentes e amostras pontuais apresentadas como pico. O resultado declara `offline_consistency_only_not_independent_flash_or_hardware_attestation`: o campo do manifesto sobre verificação de flash não é atestado independente pelo parser.

`tests/test_e1_runner.py` mantém os dez estágios e assertions anteriores; no host Linux usa C real (`cc`) quando MSVC não existe. O harness exercita ambos os ramos de PSRAM (configuração ausente e presente) e confirma as fases, `null`, status e que os 30 `execute_us` continuam inalterados. `tests/test_validate_e1_device.py` passou isoladamente 10/10 e cobre sessão positiva, CLI e rejeições de hashes/identidades, checksum, boot duplicado, linhas incompletas/intercaladas, fase ausente, flash não verificado, PSRAM ausente com zero e falso pico.

## Verificação

Suíte completa executada com o `PATH` explícito do CMake/Ninja e PowerShell portátil, e `WASMHOME_WIRE_LIBRARY=/tmp/wasmhome-p1-20261013-r2/libobservation_wire.so`:

- `python3 -m unittest discover -s tests -v`: **75 testes passaram de 76 executados; 1 skip**, apenas a verificação negativa de junction do Windows, específica da plataforma. Código de saída 0.
- Os testes do runner compilam o `main.c` em C no Linux e percorrem os dez estágios; também compilam uma segunda variante host com `CONFIG_SPIRAM` ativo. O teste de interleaving valida uma linha concorrente completa após cada evento; não reivindica atomicidade física byte a byte do UART.
- `git diff --check`: código de saída 0.
- A falha intermediária da primeira tentativa de teste da CLI (import ausente de `subprocess`) foi preservada em `unittest-full-cli-import-error.log`; após corrigir o import, os dez testes do validador e a suíte completa passaram. A última suíte é `unittest-full-final.log`.

## Limites e revisão pendente

Não executei build ESP-IDF do firmware, gravação, sessão serial, coleta física de PSRAM ou prova de funcionamento na placa. O harness host testa o fluxo e os dois ramos de compilação, não substitui o build nem a execução ESP32-S3. O campo de pico permanece explicitamente não instrumentado; os snapshots antes/depois não detectam alocações transitórias dentro da chamada. O `heap_min` legado no CSV permanece o mínimo desde o boot e deve ser interpretado com esse escopo.

Esta alteração muda novamente os hashes de `main/main.c` e `tests/test_e1_runner.py`, que já continham alterações das trilhas P1 e #11. A revisão independente B/C deve examinar o diff final completo contra a base abaixo; pareceres com hashes anteriores não abrangem esta revisão. `tools/collect_results.py` e documentação compartilhada não foram alterados nesta trilha.

## Snapshot e hashes

O snapshot e o diff escopado ficaram em `/tmp/wasmhome-e1-instrumento-20261013/`. O `scope.diff` contém o delta dos quatro arquivos listados, incluindo em `main.c` e `test_e1_runner.py` alterações de tarefas anteriores presentes no mesmo working tree.

- SHA-256 do diff escopado: `e28395811d3a7381a5f3a156be67a0eb7ac79f6fe817f2607042f54fec664b0c`
- `main/main.c`: `bbdb1e79abb5eec3bd39af9382bdd186654cd87d420145632340d66a189024fd`
- `tests/test_e1_runner.py`: `ad35c03423850fbf3d8828d6f9ee4314978cf7e2ce2a2d0a188a7bb98d05e369`
- `tools/validate_e1_device.py`: `c72b604e44531de86b43bc97b43686918fa14a5862810e7995f4cb215718ab4c`
- `tests/test_validate_e1_device.py`: `f37aa1e9733140dbf04c8209c5abc95324318f30d2de1b0f64a4ea4b39a0ce83`
- Log final da suíte: `f248d61f7803e5fde56c60020ce9d4a060c75953b6dc4eee94d8e6876e44677c`

Os relatórios e diretórios anteriores de P1 e serviços nativos permanecem preservados em `/tmp/wasmhome-p1-20261013-r2/` e `/tmp/wasmhome-native-services-20261013.md` mais `/tmp/wasmhome-native-services-20261013/`.

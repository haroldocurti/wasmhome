# Análise offline de atualizações assinadas — issues #16, #17 e #18

Este documento define como correlacionar a disponibilidade funcional observada
por UDP com as etapas do atualizador registradas pelo firmware na UART. O
analisador modular serve ao interpretador clássico (`scenario=2`) e ao AOT
(`scenario=3`). A issue #17 usa a sequência 2 e atualiza somente o módulo de
controle de `v1` para `v2`. A análise nativa da issue #16 correlaciona
explicitamente o boot original ao novo boot após OTA.

## Captura

O observador externo envia o disparo UDP uma única vez depois de aprender o
boot. Para o cenário interpretado, use `--scenario 2`, `--initial-version 1`,
`--target-sequence 2` e mantenha a janela com tempo antes, durante e depois da
troca. O destino HTTPS e a identidade do dispositivo continuam na configuração
local do firmware. A captura serial deve preservar os eventos completos
`WASMHOME_UPDATE,` da mesma sessão.

Depois da captura, gere a análise em um caminho novo e privado:

```sh
python3 tools/analyze_module_update.py \
  --firmware-log <captura-privada>/uart.log \
  --observations <captura-privada>/observations.jsonl \
  --summary <captura-privada>/summary.json \
  --scenario interp --sequence 2 \
  --output <captura-privada>/module-update-analysis.json
```

O comando só lê os arquivos existentes e recusa sobrescrever a análise. Ele
não abre sockets nem porta serial. A saída registra hashes SHA-256 dos três
arquivos de entrada, contagens e métricas calculadas; mantenha os arquivos
brutos e o relatório completo no diretório privado da sessão. Publique apenas
uma versão sanitizada e hashes depois de conferir endereços e outros dados da
bancada.

Para analisar OTA nativo, capture a mesma combinação de logs com sessão UDP
`scenario=1` e execute o reconciliador que exige a transição de dois boots:

```sh
python3 tools/analyze_native_update.py \
  --firmware-log <captura-privada>/uart.log \
  --observations <captura-privada>/observations.jsonl \
  --summary <captura-privada>/summary.json \
  --scenario 1 --sequence 2 \
  --output <captura-privada>/native-update-analysis.json
```

Esse modo exige aceite UDP ligado ao boot original, o mesmo `attempt_id` e
`origin_boot_id` após o reboot, `health_check_passed`, leitura de volta do piso
antirrollback e hash da imagem em execução igual ao payload verificado. Também
exige resultados funcionais discriminantes: em 27 °C, v1 deve aplicar GPIO alto
antes do aceite e v2 GPIO baixo após a ativação. A lacuna de serviço usa o
relógio monotônico do observador e relata perdas; o analisador nunca subtrai
`elapsed_us` entre boots.

## Evento serial `WASMHOME_UPDATE`

Cada linha contém um prefixo de log do ESP-IDF, `WASMHOME_UPDATE,` e um objeto
JSON. O schema comum usa estes campos:

| Campo | Significado |
| --- | --- |
| `attempt_id` | UUID compartilhado com o observador UDP |
| `boot_id` | Identidade do boot, igual em todas as etapas de uma troca modular |
| `scenario` / `component` | `2` interpretado ou `3` AOT; componente `1` é controle |
| `event` / `phase` | Nome do evento; precisa ser igual ao nome da etapa |
| `status` / `failure` | Estado `in_progress`, `ok`, `rejected` ou `error`; sucesso usa `failure=none` |
| `release_version` / `target_version` | Texto assinado, por exemplo `v2`, e versão numérica `2` |
| `sequence` | Sequência antirretorno do manifesto |
| `payload_bytes_expected` / `payload_bytes_received` | Comprimentos do módulo transferido |
| `payload_sha256` | Hash do payload; nulo até a validação terminar |
| `manifest_sha256` | Hash do manifesto baixado e validado |
| `elapsed_us` | Relógio monotônico do MCU, válido dentro do mesmo boot |
| `elapsed_scope` | Para troca modular, deve ser `boot` em todas as etapas |
| `heap` | Bytes livres e maior bloco livre em heap interno e PSRAM |
| `telemetry_runtime_busy_drops` | Contador cumulativo opcional de amostras recusadas durante ocupação do runtime |
| `telemetry_generation` / `telemetry_sha256` | Identidade da instância estável de telemetria |
| `control_generation` / `control_module_version` / `control_sha256` | Identidade e versão do controle ativo |
| `control_artifact_bytes` | Tamanho do módulo ativo, se fornecido pelo gerenciador |

Uma troca modular bem-sucedida registra, nesta ordem, `accepted`,
`manifest_download_started`, `manifest_downloaded`, `signature_verified`,
`payload_download_started`, `payload_verified`, `staged`, `activating`,
`candidate_loaded`, `candidate_instantiated`, `candidate_health_check`,
`floor_committed`, `control_activated` e `previous_control_released`.
`payload_download_progress` pode aparecer várias vezes. Eventos de início
(`manifest_download_started`, `payload_download_started` e `activating`) usam
`status=in_progress`; `accepted` significa que o comando foi aceito e usa
`status=ok`. As etapas concluídas usam `status=ok`, `failure=none` e
`event==phase`. O hash do manifesto em `manifest_downloaded` deve coincidir com
o de `signature_verified`. `floor_committed` precisa registrar
`ledger_readback_sequence` igual à sequência alvo, depois de commit e leitura de
volta. Rollback, falha ou regressão na mesma tentativa invalida o resultado.

O snapshot `accepted` identifica o controle v1 e a telemetria antes da
transferência. `candidate_health_check` valida o candidato antes do swap e
`floor_committed` registra o ledger já confirmado. Em seguida,
`control_activated` e `previous_control_released` registram o controle v2 e
repetem a identidade de telemetria. O analisador exige a mesma
geração e SHA-256 de telemetria nos três snapshots; observar apenas o mesmo
boot ou uma sequência UDP crescente não basta para afirmar que a mesma
instância permaneceu ativa. O analisador também exige que o hash do payload
verificado seja igual ao hash do controle ativo e, se disponível, que o tamanho
do artefato ativo coincida com os bytes recebidos. Uma resposta rotulada como v2
não basta para ligar o pacote assinado ao módulo em execução.

O log combinado também inclui snapshots `WASMHOME_MODULE_MEMORY,` do gerenciador.
Para aceitar a troca, `module_load` e `module_instantiate` do papel
`control_candidate` precisam ocorrer após a verificação do payload e antes do
commit do piso. Depois da leitura de volta do piso, `candidate_overlap` precisa
registrar o candidato pronto enquanto o controle antigo ainda está ativo;
`control_replaced_old_released` comprova a liberação da referência antiga antes
do fim da troca. O relatório valida ordem, papéis, estados, heaps interno/PSRAM
e informa heap mínimo e contadores do allocator WAMR. Se o contador
`telemetry_runtime_busy_drops` estiver presente, deve existir nos snapshots
`accepted`, `control_activated` e `previous_control_released`; o relatório mede
seu delta e rejeita regressão. O observador UDP também mantém lacunas de
telemetria e IDs de amostra separadas desse contador de ocupação.

## Métricas e limites

O relatório distingue duração por etapa e total no relógio monotônico do MCU,
tamanho/hash do módulo, snapshots de heap, lacunas UDP, mudança observada de
v1 para v2, lacuna entre respostas funcionais e estimativa de indisponibilidade.
A estimativa subtrai uma cadência do intervalo entre a última resposta correta
antes da primeira v2 observada e essa primeira resposta v2. Ela é uma medida do
serviço observado e inclui rede e escalonamento do host.

Eventos de falha de candidato, rejeição, rollback e regressão de versão são
preservados no campo `recovery` do relatório. Um resultado bem-sucedido sem tais
eventos informa que a recuperação não foi exercitada; não afirma que ocorreu um
rollback físico. Falhas ou regressões tornam a análise incompleta e exigem
revisão da evidência de recuperação antes de declarar aceite.

O analisador mede a cadência mediana de telemetria antes do aceite e conta os
datagramas válidos durante o intervalo de aceite até a primeira resposta v2.
Quando o intervalo é menor que uma cadência, ele não exige uma amostra interna;
para intervalos maiores, exige cobertura proporcional, tolerando uma amostra de
borda. Também informa a maior lacuna observada. Uma amostra antes e outra depois,
sem cobertura durante uma atualização longa, não satisfaz essa verificação.

`elapsed_us` do MCU nunca é subtraído de `host_monotonic_ns` do observador. A
ferramenta valida a consistência dos arquivos fornecidos, mas não cria
proveniência física: build/configuração efetiva, placa, captura sincronizada,
restauração e hashes dos registros precisam acompanhar a evidência da sessão.
Resultado ausente, identidade incompleta, boot divergente, etapa fora de ordem,
payload incompleto ou telemetria sem continuidade produz um relatório
`incomplete_or_failed`; não deve ser apresentado como aceite.

## Eventos nativos com reboot

No OTA nativo, `accepted`, download, verificação, `staged`, `activating` e
`reboot_requested` pertencem ao boot anterior. `boot_pending`,
`health_check_passed` e `floor_committed` pertencem ao novo boot e carregam o
`origin_boot_id` anterior junto do mesmo `attempt_id`. O analisador rejeita IDs
inválidos ou inconsistentes e confere o boot de aceite UDP com o evento serial
`accepted`. O piso anterior deve ser inferior à sequência alvo; após o health
check, `accepted_floor` e, quando presente, `ledger_readback_sequence` precisam
confirmar a sequência alvo; a leitura de volta é obrigatória para passar.

Os tempos do dispositivo só são comparados dentro de cada boot. O intervalo do
disparo até a primeira resposta v2, a lacuna entre a última resposta v1 e a
primeira v2, e a lacuna de telemetria entre boots usam `host_monotonic_ns`.

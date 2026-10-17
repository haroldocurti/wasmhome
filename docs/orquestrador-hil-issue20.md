# Execução automática de HIL da issue #20

`tools/issue20_hil_orchestrator.py` prepara e captura slots consecutivos da
agenda congelada `issue20-v1.0`. Ele executa como usuário comum e usa a chave
SSH dedicada ao observador. O piloto aceita N=6 (18 slots); a campanha normal
aceita N=30 (90 slots). O número de slots solicitado nunca aumenta a agenda.

## Entradas congeladas

O diretório da campanha deve ter sido criado por `update_campaign.py plan`
no commit que será usado para executar a coleta. A baseline integral aprovada
tem 16 MiB e SHA-256
`00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`.
O identificador MAC esperado vem do recibo privado da placa, nunca de descoberta
automática de outro dispositivo.

Criar também um `profiles.json` privado, com este esquema para cada um dos
cenários `native`, `interp` e `aot`:

```json
{
  "schema_version": 2,
  "profiles": {
    "native": {
      "bootloader": {"path": "/CAMINHO_PRIVADO/native-bootloader.bin", "sha256": "SHA256_COMPLETO"},
      "bootloader_config": {"path": "/CAMINHO_PRIVADO/native-bootloader-sdkconfig.h", "sha256": "SHA256_COMPLETO"},
      "partition_table": {"path": "/CAMINHO_PRIVADO/tabela.bin", "sha256": "SHA256_COMPLETO"},
      "app_config": {"path": "/CAMINHO_PRIVADO/native-app-sdkconfig.h", "sha256": "SHA256_COMPLETO"},
      "app": {"path": "/CAMINHO_PRIVADO/native-v1.bin", "sha256": "SHA256_COMPLETO"}
    },
    "interp": {
      "bootloader": {"path": "/CAMINHO_PRIVADO/interp-bootloader.bin", "sha256": "SHA256_COMPLETO"},
      "bootloader_config": {"path": "/CAMINHO_PRIVADO/interp-bootloader-sdkconfig.h", "sha256": "SHA256_COMPLETO"},
      "partition_table": {"path": "/CAMINHO_PRIVADO/tabela.bin", "sha256": "SHA256_COMPLETO"},
      "app_config": {"path": "/CAMINHO_PRIVADO/interp-app-sdkconfig.h", "sha256": "SHA256_COMPLETO"},
      "app": {"path": "/CAMINHO_PRIVADO/interp-v1.bin", "sha256": "SHA256_COMPLETO"}
    },
    "aot": {
      "bootloader": {"path": "/CAMINHO_PRIVADO/aot-bootloader.bin", "sha256": "SHA256_COMPLETO"},
      "bootloader_config": {"path": "/CAMINHO_PRIVADO/aot-bootloader-sdkconfig.h", "sha256": "SHA256_COMPLETO"},
      "partition_table": {"path": "/CAMINHO_PRIVADO/tabela.bin", "sha256": "SHA256_COMPLETO"},
      "app_config": {"path": "/CAMINHO_PRIVADO/aot-app-sdkconfig.h", "sha256": "SHA256_COMPLETO"},
      "app": {"path": "/CAMINHO_PRIVADO/aot-v1.bin", "sha256": "SHA256_COMPLETO"}
    }
  }
}
```

Antes do planejamento, registrar o SHA-256 dos bytes desse arquivo em
`metadata.hil_profiles_sha256`. O manifesto `artifacts.json` continua a
identificar os alvos OTA v2. Os bootloaders, a tabela, os apps v1 e os dois
headers de configuração gerados por build são entradas de preparação
separadas, congeladas pelo hash de `profiles.json`. O orquestrador confere os
hashes e copia baseline e overlays para snapshots privados antes de acessar a
serial; mudanças posteriores no arquivo de origem não substituem os snapshots.

Usar o bootloader do mesmo build de cada app v1. `app_config` vem de
`build/config/sdkconfig.h` e `bootloader_config` vem de
`build/bootloader/config/sdkconfig.h`; não usar o `sdkconfig` de entrada.
Ambos são copiados para snapshots `.h` privados, com hash verificado, e
precisam selecionar ESP32-S3, flash de 16 MiB e tabela em `0x8000`. O
orquestrador exige rollback igual nos dois headers; no native ele precisa
estar habilitado. Interp/AOT aceitam ambos habilitados ou ambos desabilitados.
O parser remove comentários, rejeita condicionais, includes, continuação de
linhas e diretivas fora do formato gerado pelo IDF antes de avaliar macros.
Uma configuração ausente, divergente, alterada ou ambígua impede a preparação.

O BIN do bootloader também precisa passar pela validação estrutural local:
segmentos completos e sem sobreposição, entrada em segmento IRAM, descrição IDF
válida em DRAM, checksum XOR correto, SHA-256 final correto e nenhum byte extra.
`image_info` sozinho não é aceito como prova de checksum ou digest.

Antes de congelar o pacote, conservar também no recibo privado a origem e os
hashes do BIN native v2, de `native-v2/config/sdkconfig.h` e de
`native-v2/bootloader/config/sdkconfig.h`. Confirmar ESP32-S3, 16 MiB, offset
`0x8000`, rollback habilitado nos dois headers e que o SHA do BIN é o mesmo
do payload native no manifesto assinado. Os campos do perfil representam o
bootstrap v1; a evidência do candidato v2 fica nesse recibo, referenciado e
congelado pelo manifesto privado do pacote.

Um app com rollback e um bootloader sem rollback deixam o candidato em
`ESP_OTA_IMG_NEW`, impedindo a reconciliação de saúde. O hash e o cabeçalho
da imagem não provam essa opção de compilação. Não substituir os headers e
o recibo por uma alteração no reconciliador para aceitar `NEW`.

Perfis v1 e journals anteriores sem a versão dos perfis não podem preparar
novos slots. A ação `recover` continua disponível para restaurar a baseline
de uma operação interrompida. Registrar uma nova campanha com perfis v2;
preservar os dados e as claims da campanha anterior.

## Execução

Preparar o servidor HTTPS e o observador no CT107 conforme
[observador remoto](observador-remoto-ct107.md). Preencher os caminhos e o MAC
abaixo com o recibo privado da bancada. Todos os caminhos passados permanecem
no journal privado; não versionar o diretório da campanha.

```bash
umask 077
python3 tools/issue20_hil_orchestrator.py run \
  --campaign-root /CAMINHO_PRIVADO/campanha \
  --profiles /CAMINHO_PRIVADO/profiles.json \
  --baseline /CAMINHO_PRIVADO/baseline-integral.bin \
  --expected-mac MAC_DA_PLACA_VALIDADA \
  --device-ip 192.168.0.145 --serial-port /dev/ttyACM0 \
  --python /CAMINHO/DO/PYTHON --esptool /CAMINHO/DO/esptool.py \
  --flash-baud 921600 --read-timeout-seconds 600 \
  --bind-ip 192.168.0.138 \
  --remote-observer wasmhome@192.168.0.138 \
  --remote-known-hosts /home/haroldocurti/.ssh/wasmhome-ct107_known_hosts \
  --remote-identity /home/haroldocurti/.ssh/wasmhome-ct107-hil \
  --remote-server-log /home/wasmhome/HIL/http-events.jsonl \
  --max-slots 1
```

O valor padrão é um slot. Após a revisão da primeira captura, uma chamada com
os mesmos argumentos e `--max-slots 17` pode executar os slots restantes do
piloto, na ordem registrada. O estado `complete` significa que as chamadas de
captura retornaram e que a baseline foi restaurada e verificada; a aceitação
experimental ainda exige adjudicar os registros do observador e de servidor.

A leitura integral aprovada de 16 MiB levou **212,2 s a 921600 baud**, conforme
o recibo privado da captura. O default de transporte usa essa velocidade já
validada e permite 600 s por leitura integral. O limite de 240 s anterior
deixava pouca margem e era insuficiente para uma leitura a 460800 baud. O
parâmetro `--read-timeout-seconds` aceita inteiros de 360 a 3600; se a bancada
exigir transporte mais lento, reservar por exemplo 900 s. Alterar esse orçamento
de leitura não muda as janelas experimentais nem o timeout das gravações, que
continua em 240 s para a restauração integral. `--flash-baud` permanece
configurável; a velocidade registrada no journal não pode mudar no meio da
campanha.

O lock exclusivo em `~/.local/state/wasmhome-hil/hardware.lock` abrange todas as
campanhas e aliases seriais do usuário que usam este orquestrador. Ferramentas
externas da bancada também precisam respeitar esse lock. O programa não exige
root, sudo, alterações de grupos, serviços de sistema ou firewall.

## Escritas e recuperação

Antes de qualquer escrita, conferir o chip ESP32-S3, o MAC esperado, a flash
física de 16 MiB e o hash da leitura integral atual. Uma divergência interrompe
a execução sem preparar o perfil. Em cada slot:

1. Gravar e sincronizar a claim permanente do slot e o journal com
   `restore_required=true`.
2. Restaurar a baseline integral, ler os 16 MiB e verificar o SHA-256, mantendo
   o chip no carregador até gravar o overlay.
3. Gravar o bootloader ESP32-S3 em `0x0`, com no máximo `0x8000` bytes, a
   tabela em `0x8000`, com no máximo `0x1000` bytes, e o app ESP32-S3 em
   `0x20000`, com no máximo `0x700000` bytes. Conferir os hashes, a imagem
   inteira do bootloader (segmentos, checksum, digest e descrição), o layout
   completo e o MD5 da tabela, assim como os dois headers de configuração
   congelados; não aceitar partições adicionais ou movidas.
   A gravação mantém a placa no carregador; executar `verify_flash` nos três
   segmentos e reiniciar somente após a verificação concluir. Falha na
   gravação ou na verificação entra na restauração integral antes de qualquer
   disparo do observador.
4. Chamar `run_slot` uma única vez, com sequência alvo 2 e limites congelados
   de 150/60 s e 100/300/1000 ms. O runner exige UART e lease remotos frescos.
5. No `finally`, restaurar a baseline integral e conferir o hash de outra
   leitura completa, mantendo a placa no carregador ROM entre slots. A placa só
   reinicia ao gravar o overlay do slot seguinte. Falha na gravação do journal também
   tenta essa restauração. Falha de restauração bloqueia o lote.

A restauração integral reproduz os bytes aprovados, incluindo NVS e otadata.
O bootloader do perfil é transitório e também é substituído pelos bytes
originais na restauração integral. Sua preparação antecede a captura e não
faz parte das janelas de atualização medidas.
Não há edição isolada dessas partições, mudança de Wi-Fi ou escrita em eFuses.
O orquestrador não passa `--force`, não apaga NVS e não adapta a baseline ao
conteúdo encontrado na placa.

Os arquivos ficam em `campanha/hil-orchestrator`, com diretórios 0700 e journal
0600. Interrupção abrupta, queda de energia ou falha de restauração preservam
`restore_required=true`. Uma execução normal posterior recusa novos disparos.
Executar o mesmo comando com a ação `recover` no lugar de `run`: ele confere a
identidade, restaura e verifica a baseline; não abre observador, não envia
trigger e não reexecuta o slot. O resultado é `recovered_interruption`, que
mantém a campanha bloqueada para novos slots. Preservar as claims e a captura
parcial; se a sessão precisar continuar, registrar outra campanha conforme o
protocolo. A recuperação de arquivos do CT é separada e usa somente
`remote_observer.py status/recover` com o recibo original.

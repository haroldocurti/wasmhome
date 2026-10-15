# Issue #12 — alinhamento e prova física mínima

**Resultado:** correção do desalinhamento revisada, compilada e exercitada na
placa no perfil de interpretador clássico. O aceite não cobre execução física
AOT nem substitui as demonstrações específicas das issues #13 e #14.

## Correção

O ESP32-S3 só garantia alinhamento de 4 bytes em `heap_caps_malloc`, enquanto o
WAMR exige alinhamento de 8 bytes para as alocações do runtime e da memória
linear. O gerenciador agora usa `heap_caps_aligned_alloc` com alinhamento do
header, verifica overflow e arredonda o tamanho total. `realloc` faz alocação
alinhada, cópia limitada ao menor payload e liberação do bloco antigo somente
após sucesso. O heap interno atende metadados/runtime; memória linear usa PSRAM
quando `CONFIG_SPIRAM` está ativa. A configuração `WASM_ENABLE_BULK_MEMORY=1`
mantém compatível o layout do `WASMModule` privado consultado pelo gerenciador
no WAMR fixado.

## Verificações

- `python3 -m unittest tests.test_module_abi_v1 tests.test_module_manager_host -v`:
  dois testes passaram. O shim força `heap_caps_malloc` a retornar endereço
  desalinhado e verifica callbacks alinhados, preservação de dados em `realloc`,
  roteamento interno/PSRAM, falha de alocação, concorrência e troca segura.
- Builds ESP-IDF 5.2.6 para ESP32-S3 passaram. O perfil de interpretador clássico
  e o perfil AOT têm `CONFIG_SPIRAM=y` e serviços modulares habilitados. O perfil
  AOT compilou, mas não foi executado fisicamente nesta entrega.
- Dois pareceres independentes do diff não encontraram bloqueio. As ressalvas
  não bloqueantes foram que o pico do allocator pode incluir uma reserva cuja
  alocação física falhou e que o teste não verifica separadamente os contadores
  após liberar memória linear.

## Prova física no ESP32-S3-N16R8

- Telemetria e controle instanciaram com sucesso; o gerenciador ativou os
  serviços. As alocações de memória linear totalizaram 147.504 bytes em PSRAM.
- A captura estável do observador recebeu 84 telemetrias válidas, sem lacunas de
  sequência, com intervalo máximo aproximado de 107,9 ms. Registrou 78 respostas
  válidas em 79 controles; a primeira resposta expirou. Um lote separado de dez
  controles teve 10/10 respostas corretas para o loopback GPIO.
- Uma requisição UDP individual também retornou `requested_gpio=1`,
  `observed_gpio=1`, `status=0`.
- O binário AOT está compilado, mas não foi gravado/executado na placa. Essa prova
  física fica na #14.

## Restauração e proveniência

O `ota_0` original foi gravado com verificação de hash:
`67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b`.
A NVS foi regravada e lida de volta; backup e leitura final têm o mesmo SHA-256:
`d0c3e1277a6f707f413508fb0fdfcece1dd291081122c2689d176cf1635113f9`.
O firmware original iniciou antes da restauração final da NVS. A placa foi
deixada no bootloader ROM depois da leitura final para que a inicialização
normal não alterasse novamente a NVS.

Os hashes dos BINs produzidos são:

| Cenário | SHA-256 |
|---|---|
| Interpretador clássico | `1682f908277cef22778a74acfd43b0d1d60191e764406cd6445df401a5bd5c64` |
| AOT (compilado; não executado na placa) | `aab04b1e64a79cf2df877377a3faa7b4fc90b7a2131aa67059cb47371f729268` |

Os registros brutos UART, observador, comandos e imagens de restauração ficam no
arquivo privado local da sessão, fora do repositório. Este documento não contém
endereços de rede, credenciais, dumps, configurações privadas ou texto UART.

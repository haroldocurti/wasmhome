# Contrato congelado — issue #12

Status: **ABI congelada, aprovada por B em 14/10/2026**. Elaborado contra
WAMR `2.4.5-wasmhome.3` e ESP-IDF `5.2.6`. A evidência E1 não cobre
estas opções novas nem dois módulos funcionais.

## Limite da entrega

Fornecer firmware-base persistente para os cenários Wasm, bindings mínimos e
ciclo de vida independente de telemetria e controle. O controle poderá ser
preparado e trocado em ponto seguro; telemetria permanece na mesma instância.
Download, HTTPS, assinatura, anti-rollback, atualização nativa e campanha são
entregas próprias (#13–#24), fora desta issue.

## ABI v1

Valores e tipos abaixo são parte do contrato; inteiros Wasm usam os tipos
`i32`/`i64`. O guest nunca envia resultados observados de hardware.

| Papel | Import exato | Tipo | Efeito autorizado |
|---|---|---|---|
| Telemetria | `wasmhome_telemetry_v1.snapshot` | `(i32 offset) -> i32` | Copia exatamente 16 bytes da amostra corrente para a memória linear validada. Layout LE: `sample_id:u64`, `temperature_mC:i32`, `age_ms:u16`, `sensor_status:u8`, `reserved:u8=0`. Retorna `WH_OK=0` ou erro. |
| Telemetria | `wasmhome_telemetry_v1.monotonic_us` | `() -> i64` | Tempo monotônico do host em microssegundos. |
| Telemetria | `wasmhome_telemetry_v1.publish_telemetry` | `() -> i32` | Marca no máximo uma publicação da amostra corrente. O host constrói o pacote; não aceita bytes ou ponteiros de payload do guest. |
| Controle | `wasmhome_control_v1.monotonic_us` | `() -> i64` | Tempo monotônico do host em microssegundos. |
| Controle | `wasmhome_control_v1.gpio_request` | `(i32 level) -> i32` | Registra uma única intenção `level ∈ {0,1}` na chamada atual; não aciona hardware. |
| Controle | `wasmhome_control_v1.publish_control` | `(i64 sample_id, i32 requested) -> i32` | Registra uma única publicação; deve corresponder à amostra e à intenção da chamada. O host constrói o resultado observado. |

Allowlist estrita por papel. Telemetria pode importar somente as três funções
de seu namespace e não tem capacidade de GPIO. Controle pode importar somente
as três funções de seu namespace e não lê diretamente sensor ou estado GPIO.
Não se aceitam imports de outro namespace, função desconhecida, tabela,
memória, global, WASI, libc, pthread, ou qualquer outro tipo. A checagem é feita
antes de instanciar.

Exports obrigatórios e tipos exatos:

| Export | Tipo |
|---|---|
| `wh_abi_version` | `() -> i32`, deve retornar `1` |
| `wh_telemetry_tick` | `() -> i32`, apenas papel telemetria |
| `wh_control_request` | `(i64 sample_id, i32 temperature_mC) -> i32`, apenas papel controle |

O export de entrada do papel errado e exports extras são rejeitados. Um export
opcional `memory` só é aceito se for de tipo memória; memória importada é
proibida. O validador compara nome, tipo, quantidade e assinatura de cada
import/export antes da instanciação. ABI/export ausente, duplicado ou de tipo
incorreto rejeita o candidato. Código de retorno guest `0` significa sucesso;
qualquer valor diferente de zero ou trap é falha.

Módulos com start section/função de inicialização são rejeitados antes da
instanciação: WAMR executa esse código dentro de `wasm_runtime_instantiate_ex2`,
antes de devolver o handle da instância ao host. No pin `.3`, o manager consulta
o metadado carregado por formato (`WASMModule.start_function` para bytecode e
`AOTModule.start_func_index` para AOT) usando os headers internos fixados; valor
diferente do sentinela `UINT32_MAX` rejeita o artefato. Tipo de pacote
desconhecido ou caminho que não permita verificar esse metadado também falha
fechado. Portanto nenhuma instrução guest roda dentro da instanciação. O limite
de tamanho do artefato limita parsing/cópia; a quota global limita alocações.

Para cada callback com offset, o host valida o intervalo inteiro com
`wasm_runtime_validate_app_addr` antes de converter o offset e copiar. A soma
offset+comprimento é calculada sem overflow. O ponteiro nativo só vive durante
a cópia; nenhum ponteiro guest é armazenado. Erros de callback: `WH_OK=0`,
`WH_ERR_INVALID=1`, `WH_ERR_BOUNDS=2`, `WH_ERR_FORBIDDEN=3`,
`WH_ERR_DUPLICATE=4`, `WH_ERR_QUEUE_FULL=5`, `WH_ERR_STATE=6`.

## Semântica das chamadas e filas

**Telemetria:** no início de cada tick o host fixa uma cópia da amostra. Pode
haver zero ou uma chamada a `publish_telemetry`; ausência significa sem evento.
Duplicata invalida a publicação. Durante o callback, o host reserva um slot
não bloqueante na fila; após sucesso da função, confirma o slot com o payload
derivado da amostra fixada. Trap/erro libera a reserva sem evento. Fila cheia
retorna `WH_ERR_QUEUE_FULL`, incrementa `telemetry_queue_full_drops` e não
atrasa o tick.

**Controle:** antes de admitir a chamada, o host reserva um slot de resposta.
Uma requisição sem slot disponível não é admitida nem executada. Durante a
chamada, `gpio_request` e `publish_control` somente registram estado temporário.
Sucesso exige retorno zero, exatamente uma intenção GPIO válida e exatamente
uma publicação válida com o `sample_id` e nível correspondentes. Só então o
host aplica o GPIO uma vez, lê o loopback e enfileira exatamente um resultado
host-side (`requested`, `observed`, status). Nenhum callback escreve UDP ou
aciona GPIO.

Em ausência, duplicata ou divergência de publicação/intenção, o host descarta
o estado temporário, não aplica GPIO e enfileira exatamente um resultado com
`requested=255`, `observed=255`, status `EXECUTION_ERROR`. Se houver intenção e
publicação válidas, mas a função terminar em valor não zero ou trap, o host não
aplica GPIO e responde uma vez com o nível staged, `observed=255` e
`EXECUTION_ERROR`. Falha do driver após aplicar a intenção também produz um
único resultado com `observed=255` e `EXECUTION_ERROR`; se houver leitura
válida diferente do solicitado, status `MISMATCH`. Estados de controle
compatíveis com o protocolo de observação: `APPLIED=0`, `MISMATCH=1`,
`NOT_APPLICABLE=2`, `EXECUTION_ERROR=3`.

O slot reservado garante espaço para o resultado de cada chamada admitida.
Há filas limitadas e contadores próprios para recusas antes da admissão,
descartes de telemetria e falhas de envio. Um único consumidor de TX serializa
os datagramas e chama o transporte existente; callbacks Wasm nunca chamam
`sendto` nem esperam pela rede. Resultados de controle têm prioridade sobre
telemetria pendente. A falha de envio após enqueue é registrada pelo host e não
repete a ação GPIO.

O firmware mantém cache de replay limitado a 64 resultados por boot e o maior
`sample_id` admitido. IDs de novos pedidos devem ser positivos e estritamente
crescentes em cada boot; uma duplicata ou conflito pode reutilizar um ID existente.
Mesmo ID e mesma temperatura retorna o corpo de resultado
armazenado, com a versão original, e nunca executa Wasm/GPIO novamente. Mesmo
ID com temperatura diferente é conflito e não recebe segunda execução. ID mais
antigo que o maior aceito, mas já removido do cache, é `stale` e não é executado
nem recebe resultado inventado. Guardam-se todos os resultados de requisições
admitidas, inclusive erro/trap e falha de GPIO, antes de tentar enviar; assim
nenhuma repetição reaplica uma ação. A resposta repetida recebe sequência e
timestamp de envelope novos; `sample_id`, versão e resultado no corpo são os
armazenados. A reserva de saída também precede a enfileiração de uma resposta
repetida.

## Slots, preparação e troca

Runtime WAMR é inicializado uma vez; bindings são registrados uma vez antes
das cargas. Slots `telemetry` e `control` têm buffers de artefato, módulo,
instância, `exec_env`, versão, estado, quotas e contagem de chamadas em voo
independentes. Estados: `EMPTY`, `PREPARING`, `READY`, `ACTIVE`, `DRAINING`,
`FAILED`.

Cada instância recebe com `wasm_runtime_set_custom_data` um contexto de host
próprio e imutável quanto à identidade: magic, papel, slot, geração e referência
ao manager. O contexto também contém somente o estado da chamada corrente
(amostra, invocação, snapshot e contadores de imports). Toda entrada nativa
recupera a instância por `wasm_runtime_get_module_inst(exec_env)`, lê esse
contexto com `wasm_runtime_get_custom_data` e valida magic, papel, geração,
estado do slot e invocação ativa antes de agir. Contexto ausente, desatualizado,
de candidato ou com papel errado falha fechado. Controle ativo e candidato têm
contextos e buffers de staging separados; callback nenhum depende de um
ponteiro global para o “controle atual”. Há no máximo uma chamada em voo por
instância, protegida por flag/lock; tentativas paralelas na mesma instância
retornam `WH_ERR_STATE` antes de tocar estado Wasm ou staging.

O candidato de controle é carregado em buffer próprio, validado por papel e
ABI, instanciado com seus limites e tem apenas `wh_abi_version` executado para
validar o valor 1. Durante essa chamada, **todos** os imports recusam uso:
callbacks que retornariam `i32` dão `WH_ERR_STATE`; `monotonic_us` retorna `-1`
e marca uso proibido. Qualquer import usado durante a validação invalida o
candidato, mesmo se `wh_abi_version` ignorar o erro e retornar 1. Nenhuma
entrada funcional do candidato roda durante `PREPARING`. Qualquer falha limpa
exatamente os recursos do candidato em ordem inversa e preserva o controle
ativo e a telemetria. A troca ocorre no ponto entre requisições, sob
lock de lifecycle, quando a contagem em voo do controle ativo é zero. O novo
controle é publicado atomicamente; somente depois o antigo é drenado e liberado.
Não se destrói runtime enquanto houver slot ou worker ativo.

`module_version` é metadado confiável fornecido pelo host ao registrar a
imagem no slot (nesta issue, constante/configuração de firmware para artefato
embutido); nunca vem do guest. É distinto de `wh_abi_version=1`, que só
identifica o contrato. Cada resultado `CONTROL_RESULT` serializa a versão do
slot ativo que executou a chamada. O cache de replay preserva essa versão
original mesmo após uma troca de controle. Na entrega futura #15, a origem
passará ao manifesto autenticado; essa entrega não faz parte da #12.
Somente `module_version` 1 ou 2 é aceito; qualquer outro valor invalida o
registro do artefato.

Cada lane possui seu próprio `exec_env`, nunca compartilhado entre tasks nem
usado concorrentemente. Workers que chamam WAMR são criados por
`pthread_create` (o ensaio anterior mostrou que uma task FreeRTOS sem descritor
pthread aciona assert no port ESP-IDF). Cada worker inicializa
`wasm_runtime_init_thread_env` ao iniciar e chama
`wasm_runtime_destroy_thread_env` antes de sair. Locks/refcounts protegem
admissão, estado do slot, swap e liberação;
load/instantiate/deinstantiate/unload não podem liberar objeto referenciado por
chamada em andamento. Concorrência entre lanes e preparação paralela só será
habilitada após teste de stress no pin `.3` confirmar segurança de runtime,
allocator e cadência. Sem essa evidência, a configuração falha fechada e não
alega telemetria contínua durante preparo concorrente.

## Limites e medição

`wasm_runtime_instantiate_ex2` fixa `max_memory_pages`; stack do Wasm e
`host_managed_heap_size` são limites por instância. Limites de artefato, páginas,
heap, stack e buffers são configuração explícita, nunca derivados de pedido do
guest. Valores iniciais de 8 KiB para stack/heap são somente provisórios por
serem o baseline E1; páginas, tamanho máximo de artefato, heap global e filas
precisam ser fixados e aceitos com o módulo real e a medição da placa.

Cada chamada executa exatamente um export de entrada por tick ou requisição;
não há chamadas guest recursivas. O prazo de resposta funcional de controle é
300 ms fim a fim, medido pelo observador desde o envio até a recepção. O limite
inicial de execução guest é 200 ms para `wh_control_request` e validação de ABI,
e 50 ms para `wh_telemetry_tick`, deixando margem para GPIO, fila,
escalonamento e transporte UDP e para preservar o período de 100 ms. Um
watchdog separado solicita `wasm_runtime_terminate()` ao ultrapassar o limite,
mas não libera a instância até a chamada encerrar e a contagem em voo zerar. A
eficácia, latência e margem de transporte precisam ser provadas no pin `.3`; se
não forem, não se alegará contenção de loop infinito nem cumprimento do prazo.

O pin oferece `WASM_MEM_ALLOC_WITH_USAGE`, desabilitado por padrão. O build da
issue deve habilitá-lo no wrapper e auditar o `compile_commands.json`/símbolos
do binário. Allocator de WAMR terá teto duro configurável sobre bytes vivos
globais (`Alloc_For_Runtime` + `Alloc_For_LinearMemory`) e contadores de atual,
pico, chamadas e falhas por categoria e totais. Headers alinhados guardam o
tamanho/categoria; soma/overflow é checada. `realloc` reserva conservadoramente
o novo bloco completo antes da operação e só libera a reserva antiga após
sucesso; em falha desfaz a reserva e preserva o bloco original. Assim, o limite
considera sobreposição temporária de realocação. A implementação deve provar
thread safety e retorno ao baseline após todos os frees.

Contadores WAMR incluem memória linear e demais alocações recebidas por esse
allocator, classificadas pelo argumento `usage`; não somar a esses contadores o
delta dos heaps ESP-IDF. Heap interno e PSRAM (`total`, `free`, maior bloco)
serão consultados separadamente. Artefatos, stacks/tasks, filas e buffers
alocados fora do allocator WAMR são reportados por categoria própria; memória
estática do ELF à parte. Capturar baseline, runtime, cada instância, sobreposição
do candidato, chamadas, falhas e após liberação. Se uma métrica não puder ser
medida, emitir `null` e motivo.

O firmware emite snapshots `WASMHOME_MODULE_MEMORY` com schema
`wasmhome-module-memory-v1` nas fronteiras de baseline, runtime, cópia/load,
instanciação, ABI, ativação, sobreposição do candidato e liberação. Cada linha
separa heap interno/PSRAM total-livre-maior bloco dos contadores WAMR. A
capacidade de payload das filas e a pilha de worker solicitada são identificadas
como categorias do gerenciador; não representam a alocação real do RTOS. As
métricas de heap, high-water das tasks e limites continuam sujeitas à conferência
no firmware compilado e na placa. Para cada geração, há ainda um snapshot após a
primeira chamada funcional de telemetria e controle.

Os timers de watchdog e seus gates são infraestrutura do slot estático e ficam
retidos entre gerações e paradas do manager. No pin ESP-IDF 5.2.6, excluir um
timer apenas agenda sua liberação no task de timers, sem aguardar um callback já
expirado. A retenção mantém estável o argumento do callback; o deadline da
invocação ativa faz callbacks atrasados ignorarem a próxima geração. O campo
`persistent_watchdog_slots` e o heap interno deixam essa infraestrutura visível
nas medições; os contadores do allocator WAMR devem voltar a zero após liberar
runtime e módulos.

Configuração WAMR registra explicitamente `WAMR_ENABLE_APP_FRAMEWORK=n` para
os novos builds, sem afirmar que a evidência E1 cobre essa mudança. Preservar
ESP-IDF `5.2.6`, WAMR `2.4.5-wasmhome.3`, AOT validator e bounds checks. As
configurações efetivas de interpretador e AOT são revisadas separadamente.

## Prova antes do aceite

Testar ABI/imports/exports incompatíveis; start section no Wasm e no AOT;
offset inválido e overflow; erro de host; ausência/duplicata/divergência de
publish; trap depois de registrar intenção; falhas de load, instantiate,
exec_env, export, OOM e cada etapa de limpeza; chamada em voo durante troca;
replay igual, conflito e ID stale; versão host-side correta em resultado;
tentativa de uso concorrente do mesmo `exec_env`; limite global e `realloc`
concorrente/falho; e retorno de alocações ao baseline. Depois: build ESP-IDF
novo, revisão da configuração efetiva,
teste funcional e prova na placa com restauração documentada. Teste Linux com
WAMR fake cobre o contrato do gerenciador, não a implementação real do WAMR.

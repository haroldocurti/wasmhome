# Ensaios válidos de resiliência

Status: decisão fechada para implementação e campanha complementar.

## Decisão

A campanha de resiliência terá dois grupos de ensaios:

1. rejeição do envelope de atualização, aplicável aos três cenários;
2. contenção de falhas de execução, com traps definidos pela semântica do
   WebAssembly nos cenários interpretado e AOT e um aborto controlado no cenário
   nativo.

O ensaio de "desreferência de ponteiro nulo" foi substituído. WebAssembly usa
endereços inteiros como índices de uma memória linear iniciada em zero; uma
leitura em zero é válida quando os bytes acessados existem. O ensaio inclui uma
leitura válida em zero como controle negativo e uma leitura de 32 bits iniciada
três bytes antes do fim da memória como acesso fora dos limites.

Não haverá tentativa de produzir, em C nativo, acesso fora dos limites ou divisão
inteira por zero como se fossem equivalentes aos traps do WebAssembly. Em C,
esses casos têm comportamento indefinido, portanto o compilador e o processador
podem produzir resultados diferentes. O cenário nativo usará
`esp_system_abort("resilience:controlled_abort")` como controle reproduzível do
impacto de uma falha fatal sobre o firmware inteiro.

## Base normativa e comportamento do runtime

- A especificação do WebAssembly determina trap quando qualquer byte de um
  `load` ou `store` fica fora da memória linear. Ela também deixa claro que o
  endereço é um índice desde zero; não há uma regra que reserve o endereço zero
  como nulo. Consulte [Memory Instructions, WebAssembly Core
  Specification](https://webassembly.github.io/spec/core/exec/instructions.html#memory-instructions).
- A divisão inteira do WebAssembly é uma operação parcial. Divisor zero não tem
  resultado e a execução da instrução binária produz trap. A divisão assinada do
  menor inteiro por `-1` também não tem resultado; este estudo usa apenas divisor
  zero. Consulte [Integer division, WebAssembly Core
  Specification](https://webassembly.github.io/spec/core/exec/numerics.html#op-idiv)
  e [Numeric Instructions](https://webassembly.github.io/spec/core/exec/instructions.html#numeric-instructions).
- A API de incorporação do WAMR retorna `false` quando uma chamada falha e permite
  obter a exceção da instância com `wasm_runtime_get_exception`. Consulte
  [Embedding WAMR](https://github.com/bytecodealliance/wasm-micro-runtime/blob/main/doc/embed_wamr.md#native-calls-wasm-functions-and-passes-parameters).
- O WAMR oferece uma opção privilegiada para tornar configurável a desativação
  das verificações de limites no interpretador. A bancada deve manter
  `WAMR_CONFIGURABLE_BOUNDS_CHECKS=0` e não aceitar qualquer opção que desative
  bounds checks. Consulte [Configurable memory access boundary
  check](https://github.com/bytecodealliance/wasm-micro-runtime/blob/main/doc/build_wamr.md#configurable-memory-access-boundary-check).
- Traps de instruções básicas não dependem do suporte opcional à proposta de
  tratamento de exceções do WebAssembly. `WAMR_BUILD_EXCE_HANDLING` não deve ser
  usado para alterar esta bateria.
- A integração oficial do WAMR como componente do ESP-IDF inclui o ESP32-S3 e
  expõe a exceção da instância após a execução. Consulte o [componente oficial
  para ESP-IDF](https://components.espressif.com/components/espressif/wasm-micro-runtime)
  e o [exemplo ESP-IDF do
  WAMR](https://github.com/bytecodealliance/wasm-micro-runtime/blob/main/product-mini/platforms/esp-idf/main/main.c).
- O padrão C deixa indefinidos a divisão por zero, a aritmética de ponteiros fora
  de um objeto e a indireção por um ponteiro inválido. Consulte as seções 6.5.5,
  6.5.6 e 6.5.3.2 do [rascunho público N1570 do padrão
  C](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf).
- No ESP-IDF, `esp_system_abort` dispara o tratamento de pânico. Com
  `CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT`, o firmware imprime o diagnóstico e
  reinicia; após a inicialização, `esp_reset_reason()` deve informar
  `ESP_RST_PANIC`. Consulte [Fatal Errors no
  ESP32-S3](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/fatal-errors.html)
  e [Reset Reason e
  esp_system_abort](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/system/misc_system_api.html).

## Pré-condições comuns

- Executar todos os casos na mesma ESP32-S3, com ESP-IDF, WAMR, WASI-SDK,
  `wamrc`, opções de compilação e mapa de partições fixados no registro da
  campanha.
- Manter FreeRTOS nos três cenários e o observador externo enviando estímulos a
  cada 100 ms. O timeout funcional começa em 300 ms no piloto; o valor congelado
  ao final do piloto será usado também nesta bateria e registrado no JSONL.
- Executar exatamente uma injeção por tentativa, precedida e seguida por pelo
  menos 30 respostas válidas de cada serviço aplicável.
- Usar um `attempt_id` único e registrar no JSONL todos os datagramas, eventos
  internos, versão ativa, sequência aceita, `boot_id`, motivo de reset e tempos
  monotônicos.
- Desconectar JTAG. O modo de pânico deve ser
  `CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT`, pois um depurador conectado pode fazer
  o ESP-IDF parar em vez de reiniciar.
- O módulo de telemetria e o módulo de controle devem ter instâncias e ambientes
  de execução distintos. Um trap no controle nunca pode levar o firmware-base a
  chamar `wasm_runtime_destroy()`; apenas os recursos da instância de controle
  podem ser descartados.
- Manter uma cópia já verificada da última versão válida do controle para
  recuperação sem depender da rede.
- A configuração do WAMR deve preservar as verificações de limites tanto no
  interpretador clássico quanto no AOT. O artefato AOT deve ser gerado do mesmo
  `.wasm` usado no ensaio interpretado.

## Fixture de traps

As funções de falha pertencem a uma variante de teste do módulo de controle. A
fixture deve ser produzida a partir de WAT, para que o caso não dependa de
comportamento indefinido do código-fonte C nem de otimizações do compilador:

```wat
(module
  (memory (export "memory") 1 1)

  (func (export "probe_zero") (result i32)
    i32.const 0
    i32.const 305419896 ;; 0x12345678
    i32.store
    i32.const 0
    i32.load)

  (func (export "fault_oob")
    i32.const 65533    ;; 4 bytes cruzam o fim da página de 65536 bytes
    i32.load
    drop)

  (func (export "fault_div_zero") (param $divisor i32)
    i32.const 1
    local.get $divisor
    i32.div_s
    drop))
```

`probe_zero` deve retornar `0x12345678`. `fault_div_zero` recebe zero em tempo de
execução, evitando que a ferramenta elimine uma expressão constante. A fixture
de produção deve ainda expor a operação normal do controle e os metadados de
versão exigidos pelo protocolo.

## Matriz aprovada

Cada linha de falha terá cinco tentativas contabilizadas por cenário aplicável.
O controle `WASM-ZERO` será executado uma vez em cada modo antes da bateria. São
70 tentativas de falha e dois controles de conformidade. Os resultados dos
grupos não serão combinados como se tivessem a mesma causa.

| ID | Injeção exata | Nativo | Interpretado | AOT | Resultado esperado |
|---|---|---:|---:|---:|---|
| `ENV-SIG` | Manifesto e payload válidos para a próxima sequência, assinados por uma chave ECDSA P-256 não confiável | 5 | 5 | 5 | rejeição `SIGNATURE_INVALID` antes de gravar o payload ou instanciar |
| `ENV-HASH` | Alterar um bit do payload depois de assinar o manifesto; manter manifesto e assinatura originais | 5 | 5 | 5 | rejeição `HASH_MISMATCH` antes de instanciar ou ativar; gravação temporária somente em slot inativo |
| `ENV-OLD` | Reoferecer um manifesto corretamente assinado cuja sequência seja igual ou menor que a maior sequência aceita | 5 | 5 | 5 | rejeição `SEQUENCE_NOT_NEWER`, sem ativação |
| `WASM-ZERO` | Chamar `probe_zero` | — | 1 | 1 | retorno `0x12345678`, sem exceção; falhar este controle invalida a bateria de traps |
| `WASM-OOB` | Chamar `fault_oob` em uma memória fixa de uma página | — | 5 | 5 | chamada WAMR retorna `false`; exceção classificada como acesso fora dos limites |
| `WASM-DIV0` | Chamar `fault_div_zero(0)` | — | 5 | 5 | chamada WAMR retorna `false`; exceção classificada como divisão inteira por zero |
| `NAT-ABORT` | Chamar `esp_system_abort("resilience:controlled_abort")` no caminho de controle | 5 | — | — | pânico e reinicialização completa com novo `boot_id` e `ESP_RST_PANIC` |

A chave não confiável de `ENV-SIG` deve gerar uma assinatura bem formada; assim,
o ensaio testa confiança criptográfica, não apenas parsing. Em `ENV-HASH`, a
assinatura continua válida para o hash declarado, mas o payload recebido não
corresponde a ele. Em `ENV-OLD`, o dispositivo parte de uma versão válida já
ativa e de uma maior sequência persistida; reinicializar o equipamento não pode
apagar esse estado.

## Disparo, contagem e canal de eventos

A tentativa de resiliência é diferente da atualização normal. O observador
atribui um `attempt_id` e registra o pedido de injeção no seu relógio monotônico.
O firmware confirma o pedido com `fault_accepted` antes de executar o caso.
Esse evento aceita o ensaio, não o comando UDP de atualização nem o artefato.
Após essa confirmação, rejeições esperadas, falhas, resets e timeouts permanecem
no denominador da célula. Sem confirmação, marcar o disparo como `unconfirmed` e
preservar o pedido. O silêncio não prova que o dispositivo não recebeu o pedido,
nem que rejeitou o envelope. Consultar o estado do dispositivo antes de qualquer
reposição e confirmar que um novo disparo é seguro. Manter os disparos sem
confirmação em registro separado das 70 injeções aceitas.

O ticket #22 implementará um canal serial de ensaio separado do UDP v1. O pedido
será uma linha JSON com `event=fault_request`, `attempt_id` e `case_id` da matriz.
O dispositivo só aceitará casos e fixtures previamente configurados na bancada,
sem receber URL, caminho arbitrário ou chave confiável pelo pedido. Esse canal
é exclusivo do firmware de ensaio e não faz parte da atualização normal.

Para `ENV-OLD`, o injetor obtém a fixture assinada e chama o mesmo validador de
envelope usado na atualização normal, com o piso NVS real. Não altera o piso,
não desativa a verificação e não passa pelo filtro de sequência do
`UPDATE_TRIGGER`. Assim, `SEQUENCE_NOT_NEWER` deve ser emitido no estágio
`envelope_sequence`. Uma recusa antecipada do comando UDP é um teste separado
do protocolo, fora das 70 tentativas; não substitui esse caso.

O firmware emitirá os eventos abaixo como linhas JSON na serial. Cada evento
inclui `event`, `attempt_id`, `case_id`, `scenario`, `boot_id` e `monotonic_us`
do dispositivo. Rejeição inclui `reason` e `stage`; trap inclui função, retorno
da API e exceção bruta; recuperação e boot incluem versão ativa e sequência
aceita; boot também inclui motivo de reset. Após um reset, a correlação da
tentativa deve ser recuperada pelo mecanismo implementado no ticket #22.

O observador registra as linhas recebidas com seu timestamp monotônico local
e conserva a serial bruta, inclusive o marcador de aborto e o pânico. Junta
esses eventos às observações UDP por `attempt_id` e `boot_id`, sem subtrair
relógios de emissores diferentes. Ausência de evento é evidência incompleta,
nunca um motivo de rejeição inferido por timeout. O ticket #22 deve testar
correlação, parsing e recuperação após reboot antes da campanha #23.

O protocolo UDP v1 e seus cinco tipos permanecem como canal de disponibilidade
funcional. Seus códigos de `UPDATE_ACCEPTANCE` não representam resultados de
validação posteriores ao download. Não se introduzem tipos ou motivos novos
no codec v1 para transportar estes eventos de ensaio.

Em `ENV-HASH`, o payload pode ser recebido no slot OTA ou modular inativo para
calcular o hash completo. O conteúdo divergente deve ser invalidado e não pode
ser instanciado, selecionado para boot ou marcado ativo. O slot ativo, a versão
e o piso antirretorno permanecem intactos, conforme a política do envelope.

## Eventos e critérios de aprovação

O observador deve normalizar no JSONL estes eventos do canal serial de ensaio,
sem inferi-los apenas por timeout:

- `fault_accepted`: dispositivo aceitou a injeção identificada;
- `update_rejected`: inclui um dos motivos da matriz e o estágio de validação;
- `wasm_trap`: inclui modo, função exportada, retorno da API e texto bruto de
  `wasm_runtime_get_exception`;
- `control_recovery_started` e `control_recovery_completed`;
- `boot_observed`: inclui `boot_id`, versão, sequência e `esp_reset_reason()`.

Uma tentativa de rejeição do envelope é aprovada somente quando:

1. há `update_rejected` com o motivo esperado;
2. nenhum byte é marcado como imagem ou módulo ativo;
3. versão e maior sequência aceita permanecem iguais às anteriores;
4. `boot_id` não muda;
5. telemetria e controle continuam corretos, sem intervalo superior ao timeout
   funcional congelado após o piloto.

Uma tentativa de trap WebAssembly é aprovada somente quando:

1. a chamada da função injetora retorna falha e a exceção é registrada;
2. `boot_id` não muda e não há pânico ou reset do ESP32-S3;
3. o módulo de telemetria mantém respostas corretas, sem intervalo superior a
   timeout funcional congelado, durante toda a injeção e recuperação;
4. somente a instância do módulo de controle é destruída;
5. a última versão válida do controle volta a responder corretamente dentro de
   30 segundos, conservando a sequência aceita anterior.

Uma tentativa `NAT-ABORT` é aprovada somente quando:

1. a serial contém o marcador `resilience:controlled_abort` e o pânico;
2. o dispositivo reinicia, muda o `boot_id` e publica `ESP_RST_PANIC`;
3. o observador registra interrupção tanto da telemetria quanto do controle;
4. os dois serviços voltam na última versão válida em até 30 segundos.

O limite de 30 segundos é um timeout operacional para impedir tentativas
indefinidas. O tempo efetivo de recuperação e todos os intervalos de perda devem
ser preservados como métricas, sem substituir valores pelo timeout.

Para medir esse limite, iniciar o relógio monotônico local do observador ao
receber `fault_accepted`. Em `WASM-OOB` e `WASM-DIV0`, encerrar na primeira
resposta correta do controle após a falha; a telemetria também deve permanecer
disponível durante todo o intervalo. Em `NAT-ABORT`, encerrar quando ambos os
serviços tiverem respondido corretamente depois do novo boot. Guardar tempos de
recepção serial e UDP no mesmo relógio do observador; não subtrair timestamps do
dispositivo. Sem `fault_accepted`, não iniciar o cronômetro de recuperação.

Qualquer reset, pânico do firmware-base, lacuna de telemetria acima do timeout
funcional congelado ou
alteração da sequência aceita durante um trap Wasm reprova a contenção, mesmo que
o controle volte a funcionar depois.

## Limites das comparações

- `ENV-SIG`, `ENV-HASH` e `ENV-OLD` são comparáveis entre os três cenários porque
  exercitam o mesmo envelope e os mesmos critérios de rejeição. O volume e o
  tempo podem diferir conforme o payload.
- `WASM-OOB` e `WASM-DIV0` comparam interpretador e AOT. Eles verificam se duas
  formas de executar o mesmo módulo preservam o trap e a contenção esperados.
- `NAT-ABORT` mede o efeito de uma falha fatal controlada no firmware monolítico.
  Ele não é semanticamente equivalente a acesso fora dos limites ou divisão por
  zero em C e não deve ser usado para calcular uma taxa comum de "traps".
- A fixture WAT mede o isolamento oferecido a instruções WebAssembly. Ela não
  prova contenção de defeitos dentro de um binding nativo. Um import que recebe
  dados do módulo deve validar endereço e tamanho com as APIs do WAMR antes de
  acessar memória; uma falha no código nativo pode derrubar o firmware-base.
- Cinco repetições por célula dão evidência de repetibilidade funcional, mas não
  sustentam estimativas raras de probabilidade de falha. A campanha deve publicar
  contagens e resultados individuais.
- Uma mensagem de exceção textual do WAMR é evidência diagnóstica, não contrato.
  A aprovação usa o retorno da API, a função acionada e o efeito observado; o
  texto bruto fica preservado para auditoria.

## Entregas exigidas do ticket de implementação

- fonte WAT e hashes dos `.wasm` e `.aot` usados;
- configuração completa do WAMR demonstrando bounds checks ativos;
- injetor nativo com `esp_system_abort` e configuração de pânico;
- três fixtures de envelope derivadas de um artefato válido;
- canal serial de injeção/eventos, com correlação antes e depois de reboot;
- comprovação de que `ENV-OLD` usa o validador real e não a recusa UDP;
- máquina de recuperação que descarte somente a instância de controle;
- testes do observador para todos os eventos e critérios acima;
- JSONL bruto, versões de toolchain e resumo por `attempt_id` na campanha.

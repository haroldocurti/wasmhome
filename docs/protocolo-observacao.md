# Protocolo binário de observação v1

> **Contrato v1.** Na discussão da issue [Fechar o protocolo binário de
> observação](https://github.com/haroldocurti/wasmhome/issues/3), o usuário
> confirmou os tipos de controle separados, o cabeçalho de 48 bytes e as
> respostas UDP de aceite/recusa. Este documento fixa seus campos e códigos.

O objetivo é transportar telemetria do DS18B20, estímulos e resultados de
controle, e o disparo/aceite de uma atualização. UDP continua sem retransmissão
automática: perdas e atrasos são evidência, não são preenchidos pelo protocolo.

## Enquadramento comum

Todo pacote v1 tem um cabeçalho de 48 bytes seguido pelo corpo de tamanho fixo
para seu tipo. Inteiros usam ordem de rede (big-endian); `i32` usa complemento
de dois. UUID usa os 16 octetos na ordem de rede do UUID, sem a permutação de
campos de `GUID` do Windows. Não transmitir estruturas C diretamente.

| Deslocamento | Tamanho | Campo | Unidade e semântica |
|---:|---:|---|---|
| 0 | 4 | `magic` | ASCII `WHOB` |
| 4 | 1 | `version` | `1` |
| 5 | 1 | `type` | Código da tabela de mensagens |
| 6 | 2 | `body_length` | Bytes; deve corresponder exatamente ao tipo |
| 8 | 16 | `attempt_id` | UUID da tentativa; UUID zero quando fora de tentativa |
| 24 | 8 | `sequence` | Contador de mensagem do emissor, por direção, tipo e boot/sessão; começa em 1 |
| 32 | 8 | `boot_id` | Boot do ESP32 ao qual o pacote pertence |
| 40 | 8 | `monotonic_us` | Microssegundos monotônicos no emissor |

Contadores não voltam a zero dentro do mesmo boot/sessão. Repetição de um
datagrama conserva o mesmo contador; valor zero é inválido. `attempt_id` é um
UUID criado pelo observador, não uma credencial. Telemetria que chega durante
uma tentativa pode usar esse UUID; para determinar início e fim da janela, o
observador usa seu próprio log e não confia somente no campo recebido.

Em pacotes do dispositivo, `boot_id` identifica o boot emissor. Em pedidos do
observador, identifica o boot do dispositivo que o pedido espera alcançar,
aprendido de uma mensagem válida recente. Assim, pedido atrasado de outro boot
é recusado. `sequence` é diferente de `sample_id` e da sequência antirretorno do
artefato.

O relógio do observador e o do ESP32 não são sincronizados. Não subtrair seus
timestamps; para limites que atravessam boots, usar o relógio monotônico local do
observador no JSONL.

## Tipos de mensagem

| Código | Tipo | Direção | Corpo |
|---:|---|---|---:|
| `0x01` | `TELEMETRY` | Dispositivo → observador | 16 bytes |
| `0x02` | `CONTROL_REQUEST` | Observador → dispositivo | 12 bytes |
| `0x03` | `CONTROL_RESULT` | Dispositivo → observador | 12 bytes |
| `0x10` | `UPDATE_TRIGGER` | Observador → dispositivo | 12 bytes |
| `0x11` | `UPDATE_ACCEPTANCE` | Dispositivo → observador | 20 bytes |

### `TELEMETRY`

Corpo `!QiHBB`: `sample_id:u64`, `temperature_mC:i32`, `age_ms:u16`,
`sensor_status:u8`, `reserved:u8`.

- `temperature_mC` é a última temperatura válida em milésimos de grau Celsius;
  o valor continua sendo inteiro, sem ponto flutuante no fio.
- `age_ms` é a idade daquela leitura válida, saturada em `65534`. `65535` é
  reservado para “ainda não há leitura válida”.
- `sensor_status`: `0=VALID`, `1=CRC_ERROR`, `2=DISCONNECTED`, `3=NOT_READY`,
  `4=NO_VALID_SAMPLE`. O estado descreve a aquisição mais recente; após uma
  leitura inválida, a última leitura válida pode continuar presente com idade
  crescente.
- Sem leitura válida desde o boot: `sample_id=0`, `temperature_mC=-2147483648`,
  `age_ms=65535` e estado diferente de `VALID`. Com uma amostra, o ID é positivo,
  a temperatura fica entre −55 000 e 125 000 m°C e a idade não é `65535`.
- `reserved` deve ser zero.

O DS18B20 em 10 bits tem resolução de 250 m°C. O campo não depende disso para
ser decodificado; essa resolução é uma propriedade da leitura física.

### `CONTROL_REQUEST`

Corpo `!Qi`: `sample_id:u64`, `input_temperature_mC:i32`. O observador usa ID
positivo, envia uma entrada de controle conhecida e mantém o mesmo ID em qualquer
resposta correlata. A entrada é um estímulo da regra de controle, não uma segunda
leitura física do DS18B20. O domínio definido para o estímulo é −55 000…125 000
m°C, incluindo os limites físicos de medição do DS18B20.

### `CONTROL_RESULT`

Corpo `!QBBBB`: `sample_id:u64`, `module_version:u8`, `requested_gpio:u8`,
`observed_gpio:u8`, `status:u8`.

- `module_version` é `1` ou `2`.
- GPIO usa `0=baixo`, `1=alto`, `255=not_applicable`.
- `status`: `0=APPLIED` requer solicitado e observado iguais em `{0,1}`;
  `1=MISMATCH` requer valores diferentes em `{0,1}`; `2=NOT_APPLICABLE` requer
  ambos `255`; `3=EXECUTION_ERROR` requer observado `255`.
- `sample_id` repete o ID de `CONTROL_REQUEST`. Mensagem ausente ou tardia fica
  registrada pelo observador, sem retransmissão automática.

### `UPDATE_TRIGGER`

Corpo `!QBBH`: `target_sequence:u64`, `scenario:u8`, `component:u8`,
`target_version:u16`.

- `attempt_id` não pode ser UUID zero. `target_sequence` é a sequência monotônica
  do manifesto assinado, não `sequence` do cabeçalho.
- Cenário: `1=native`, `2=interp`, `3=aot`. Componente lógico `1=CONTROL`; no
  cenário nativo ele seleciona a imagem de aplicação que contém esse serviço.
  Os códigos de cenário coincidem com o manifesto assinado: `1=native`,
  `2=wasm` (interpretador WAMR), `3=aot`.
- O `campaign_id` não vem no UDP: é configurado para a campanha no dispositivo
  e deve coincidir com o `campaign_id` do manifesto assinado. O componente lógico
  `CONTROL` mapeia para `firmware` no manifesto nativo e `gpio-control` nos
  manifestos WASM/AOT; o firmware valida esse par antes do download.
- As versões usadas nesta campanha são `1` e `2`. Outros valores não zero no
  campo `target_version` são estruturalmente decodificáveis, mas recebem recusa
  semântica `UNSUPPORTED_VERSION`. O ensaio medido é v1→v2; restaurar v1 também
  usa uma sequência antirretorno nova.
- `component=1` seleciona `CONTROL`. Outros valores não zero são decodificáveis,
  mas recebem `UNSUPPORTED_COMPONENT`.
- O pacote não leva URL nem caminho de arquivo. O destino HTTPS é configuração
  local; cenário, componente, sequência, versão, hash e assinatura do manifesto
  continuam sujeitos à validação definida no envelope assinado.

### `UPDATE_ACCEPTANCE`

Corpo `!QQBBH`: `request_sequence:u64`, `target_sequence:u64`, `status:u8`,
`reason:u8`, `reserved:u16`.

- `request_sequence` copia a sequência do cabeçalho do `UPDATE_TRIGGER`;
  `target_sequence` copia a sequência de destino solicitada.
- `status`: `1=ACCEPTED`, `2=REJECTED`. Aceite requer `reason=0=NONE`; recusa
  exige motivo não zero. `reserved` é zero.
- Motivos: `1=WRONG_BOOT`, `2=SCENARIO_MISMATCH`,
  `3=UNSUPPORTED_COMPONENT`, `4=UNSUPPORTED_VERSION`,
  `5=SEQUENCE_NOT_NEWER`, `6=BUSY`, `7=ATTEMPT_CONFLICT`.
- O dispositivo transmite `ACCEPTED` antes de iniciar o download HTTPS. A
  tentativa normal começa no observador quando esse aceite chega. Recusa não
  inicia uma tentativa da campanha normal. A campanha de resiliência tem disparo
  e contagem próprios, definidos em [Ensaios válidos de
  resiliência](research/ensaios-resiliencia.md#disparo-contagem-e-canal-de-eventos).
  A janela de 300 ms é o timeout inicial do piloto, ainda a
  confirmar antes de congelar o protocolo.

`UPDATE_ACCEPTANCE` informa aceite ou recusa do comando, antes do download.
Não informa validação de assinatura/hash, trap ou recuperação. Esses eventos
da campanha de resiliência usam o canal serial de ensaio e são correlacionados
às mensagens UDP no registro do observador, sem alterar os cinco tipos v1.

## Regras de aceitação e rejeição

- Limite v1: 1 200 bytes de payload UDP (cabeçalho e corpo incluídos).
  [RFC 8085, seção 3.2](https://www.rfc-editor.org/rfc/rfc8085.html#section-3.2)
  recomenda evitar fragmentação IP; 1 200 bytes é o limite conservador definido
  para esta bancada, não um limite imposto pela RFC. Os cinco formatos v1 ocupam
  de 60 a 68 bytes no total.
- Rejeitar antes do despacho: datagrama curto/grande demais, `magic`, versão ou
  tipo desconhecido, tamanho divergente, corpo de comprimento incorreto, valor
  fora do domínio, enum desconhecido ou campo reservado não zero. Registrar o
  erro localmente; não responder a pacote malformado.
- `attempt_id=UUID zero` em `UPDATE_TRIGGER` ou `UPDATE_ACCEPTANCE`, e zero no
  contador de mensagem ou em `boot_id`, tornam o pacote malformado.
- `component` e `target_version` são campos numéricos: valores não zero ainda
  desconhecidos são decodificados e recusados semanticamente com
  `UNSUPPORTED_COMPONENT` ou `UNSUPPORTED_VERSION`. Enumerações desconhecidas
  e valores reservados inválidos continuam sendo descartados sem resposta.
- Um disparo estruturalmente válido pode receber `UPDATE_ACCEPTANCE=REJECTED`
  por boot divergente, cenário incompatível, componente/versão não suportados,
  sequência não nova, operação ocupada ou conflito de tentativa.
- Duplicata de disparo com mesmo boot, `attempt_id`, sequência de mensagem e
  conteúdo não inicia segundo download; a resposta de aceite é repetida. Mesmo
  `attempt_id` com outra sequência ou outro conteúdo recebe
  `ATTEMPT_CONFLICT`. Duplicata de `CONTROL_REQUEST` com o mesmo `sample_id` e
  corpo devolve o resultado registrado sem executar duas vezes; mesmo ID com
  corpo diferente é descartado e registrado como `control_id_conflict`. Não há
  retransmissão automática pelo observador.
- O observador só envia comandos ao endereço configurado. UDP não autentica nem
  cifra; HTTPS e a assinatura do manifesto validam o artefato em outra camada.

## Vetores de exemplo

Todos os vetores usam UUID `00112233-4455-6677-8899-aabbccddeeff` e
`boot_id=0x0102030405060708`. Hexadecimal sem espaços; os testes do codec
codificam, comparam e decodificam cada datagrama.

### Telemetria

`sample_id=7`, `25.250 °C`, idade `500 ms`, estado `VALID`, sequência de
mensagem `1`, timestamp emissor `1000 µs` (64 bytes totais):

```text
57484f420101001000112233445566778899aabbccddeeff0000000000000001010203040506070800000000000003e80000000000000007000062a201f40000
```

### Controle: pedido e resultado

Pedido de `30.000 °C`, `sample_id=42`, sequência `2`, timestamp `2000 µs`
(60 bytes):

```text
57484f420102000c00112233445566778899aabbccddeeff0000000000000002010203040506070800000000000007d0000000000000002a00007530
```

Resultado v2, GPIO solicitado e observado em nível alto, `APPLIED`, sequência
de resposta `12`, timestamp `2100 µs` (60 bytes):

```text
57484f420103000c00112233445566778899aabbccddeeff000000000000000c01020304050607080000000000000834000000000000002a02010100
```

### Disparo e aceite

Pedido AOT para componente de controle v2, sequência de artefato
`0x0102030405060709`, sequência da mensagem `1`, timestamp `3000 µs`
(60 bytes):

```text
57484f420110000c00112233445566778899aabbccddeeff000000000000000101020304050607080000000000000bb8010203040506070903010002
```

Aceite correlacionado com o pedido `1` e o mesmo destino, sequência de resposta
`13`, timestamp `3100 µs` (68 bytes):

```text
57484f420111001400112233445566778899aabbccddeeff000000000000000d01020304050607080000000000000c1c0000000000000001010203040506070901000000
```

## Codec de referência e fronteira de validação

[`tools/observation_protocol_v1.py`](../tools/observation_protocol_v1.py) é um
codec Python de host, não código para ESP-IDF nem transporte UDP. A bateria
[`tests/test_observation_protocol_v1.py`](../tests/test_observation_protocol_v1.py)
verifica ida e volta dos cinco vetores e recusa estruturas inválidas. Execução:

```powershell
python -m unittest discover -s tests -p 'test_observation_protocol_v1.py' -v
```

Os exemplos e rejeições passam no codec de referência. A implementação da
[plataforma comum](plataforma-comum.md) acrescenta codec C e transporte UDP
embarcado com interfaces para os serviços. Os testes comparativos executam o C
no host. Tráfego UDP real e validação no dispositivo permanecem pendentes;
isso pertence às etapas de implementação e viabilidade da bancada.

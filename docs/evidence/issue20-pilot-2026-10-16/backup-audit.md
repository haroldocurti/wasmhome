# Auditoria offline da imagem-base da #20

**Data:** 16/10/2026. **Escopo:** auditoria de recibos e hashes privados e
comparação somente de leitura da captura integral atual. O dump privado não foi
copiado nem publicado; não houve gravação.

## Resultado

O hash `b66c24a3514be56702c1cf7f8d2116e44d5fe0475e3c3c67b65850258eb18ae3`
tem proveniência privada rastreável e o arquivo correspondente está presente.
O snapshot privado tem 16.777.216 bytes; seu SHA-256 calculado localmente
coincide com o recibo privado do snapshot, cujo SHA-256 é
`0fe423467004becc5fdc6bbb41b41d0f4605bb087d558ab98902d04b6542e107`.
O arquivo e o recibo permanecem fora do repositório, em armazenamento privado;
nenhum dump foi copiado para esta evidência ou incluído no Git.

## Cadeia de evidência

- O recibo privado `baseline-resumed-20261016` identifica o snapshot integral
  `b66…` e registra que ele preserva o estado mais recente do dispositivo
  depois de uma interrupção prolongada e boot da aplicação. A comparação com o
  baseline imediatamente anterior registra alterações somente nos setores NVS
  `0xd000` e `0xe000` (1.963 bytes), sem diferenças de firmware, tabela de
  partições, `otadata` ou slots.
- A comparação inicial de preflight contra a baseline antiga `242b…` reprovou:
  a leitura de 16.777.216 bytes era `b66…` e não foi tratada como equivalente.
  Depois da seleção explícita do snapshot retomado, o recibo de preflight
  autoritativo confirma que a leitura atual coincidia byte a byte com `b66…` e
  que não houve escrita antes dessa verificação.
- O recibo privado de restauração da execução nativa #16 referencia fonte e
  leitura final de 16.777.216 bytes, ambos com SHA-256 `b66…`. A análise
  independente registrou oito regiões correspondentes. Os recibos de
  restauração integral e de regiões têm SHA-256, respectivamente,
  `7511e3ff8324ea544205d12089914a7282024d8f98e49a8973d567f4922f1a17` e
  `0ae9871c1437b5942c18373dd4982d553471634318e700567ff8b692ebe27c9a`, iguais
  aos identificadores publicados no [relato da #16](../issue16-native-ota-2026-10-16.md).

## Reconciliação dos hashes históricos

- `4fcc8eb606747bd4cbb016de24e06a12e08d02fbe9628143b9a933a118c74ed3` é um
  dump integral distinto, usado na revisão física `services-run03` de 14/10.
  Há vários readbacks privados dessa sessão com esse mesmo hash. Não é o
  snapshot retomado da execução #16 em 16/10.
- `b00a479e56a1a486703093e93683de845dd09ae5392152a10f49fe9b15cde171` é o
  hash histórico do backup de 1.048.576 bytes da aplicação em `0x20000`, da
  integração do observador de 02/10. Não é uma imagem integral da flash.
- O recibo privado da imagem retomada compara `b66…` ao baseline anterior
  daquela cadeia, `242b3ee70cd2c32ed6b033f34319606f616abc70a3daf51bd34703224d1ba080`;
  a divergência limitada à calibração PHY em NVS explica por que a revisão #20
  aponta para `b66…`, não para o hash anterior.

## Limite que permanece

Em 16/10/2026, o preflight vivo identificou ESP32-S3 QFN56 rev. 0.2, MAC
`dc:b4:d9:0b:22:e4`, e flash de 16 MiB. Os comandos `chip_id`/`flash_id` do
esptool 4.12.0 acionaram hard reset, sem gravação. A leitura integral local
ignorável tem 16.777.216 bytes e SHA-256
`d29c187d0c691c5b874d1ad3a1cda8632144a70e8613a9c9148e76f8c3aa8c78`.

A comparação byte a byte com o snapshot privado `b66…` confirmou que os tamanhos
são iguais e que o hash integral vivo não corresponde à imagem-base aprovada.
Foram diferentes 7.920 bytes em 138 sequências contíguas, todos na NVS:

| Setor | Bytes diferentes |
| --- | ---: |
| `0x9000–0x9fff` | 0 |
| `0xa000–0xafff` | 3.024 |
| `0xb000–0xbfff` | 3.944 |
| `0xc000–0xcfff` | 0 |
| `0xd000–0xdfff` | 0 |
| `0xe000–0xefff` | 952 |
| **Total NVS** | **7.920** |

Bootloader, tabela de partições, `otadata`, PHY-init, slots OTA e os intervalos
fora das regiões nomeadas no recibo não têm bytes diferentes. A comparação não
determina a causa nem a semântica das alterações NVS; não presume que sejam
inofensivas ou que correspondam à calibração histórica. O `boot_id`, firmware
ativo, sensor, GPIO5→GPIO6 e telemetria/controle vivos continuam sem evidência.
Como a condição de igualdade da imagem-base falhou, nenhuma gravação, restauração
ou tentativa de piloto pode começar até a divergência ser reconciliada e a
baseline aplicável ser aprovada. Os arquivos de dump continuam privados e
ignorados pelo Git.

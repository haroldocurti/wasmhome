# Auditoria offline da imagem-base da #20

**Data:** 16/10/2026. **Escopo:** leitura de recibos e hashes locais; sem abrir
serial, acessar a placa, copiar ou publicar imagem de flash.

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

Esta auditoria valida a existência e a cadeia histórica do artefato `b66…`; ela
não identifica a placa atualmente conectada nem demonstra que sua flash atual
contém esses bytes. O preflight vivo continua obrigatório: confirmar identidade
e tamanho e comparar uma leitura integral atual ao hash `b66…`. Sem essa
correspondência, não gravar nem restaurar usando qualquer dos snapshots antigos.

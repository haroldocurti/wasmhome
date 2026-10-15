# Armazenamento persistente dos módulos

Os cenários interpretado e AOT têm dois slots persistentes cada. Os slots usam
partições de dados com nomes fixos, descobertas pela API de partições do ESP-IDF;
nenhum endereço físico é codificado no firmware. Cada partição tem 64 KiB e
começa em limite de 64 KiB, portanto as operações de erase de 4 KiB permanecem
alinhadas.

| Cenário | Slot A | Slot B |
|---|---|---|
| Interpretador | `interp_slot_a` | `interp_slot_b` |
| AOT | `aot_slot_a` | `aot_slot_b` |

As quatro partições começam imediatamente após `ota_1`, em `0xe20000`, e terminam
em `0xe60000`. Os offsets e tamanhos existentes de bootloader, tabela de
partições, NVS, `otadata`, `phy_init`, `ota_0` e `ota_1` permanecem iguais. O
mapa reserva 256 KiB para payloads modulares e permanece dentro da flash de 16
MiB da placa registrada na evidência #14.

O firmware grava e apaga apenas o slot inativo do cenário ativo. Ele verifica o
payload novamente por hash após gravar e antes de selecionar o slot. Um estado
pequeno por campanha/cenário/componente em NVS controla slot ativo, slot
pendente e piso antirretorno; o blob do payload não é armazenado em NVS. O
firmware desta campanha fixa `issues16-18-bench-20261015` como campanha. Uma
campanha diferente precisa de estado e política de retenção próprios antes de
ser usada. O adaptador desta entrega aceita somente a transição assinada v1→v2
na sequência 2; outra tentativa requer restaurar o baseline e reiniciar o ensaio.

Os slots guardam artefatos assinados e não são confidenciais. Durante ensaios
físicos, o backup deve incluir a flash inteira e a tabela original. Depois da
captura, restaure a tabela e compare a flash completa ao backup antes de
retomar o firmware original.

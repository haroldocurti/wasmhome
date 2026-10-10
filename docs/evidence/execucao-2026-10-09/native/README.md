# Prova preparatória local do cenário A

O ensaio de 09/10/2026 comprovou a execução sequencial do microbenchmark nativo
no ESP32-S3, no boot `46da5751fa67bcfc`. O log bruto contém uma inicialização
E1, a declaração de cinco chamadas de aquecimento concluídas, 30 chamadas
medidas em ordem e o status final `native/ok`. As 30 respostas têm checksum
`0xDBB2F0D4`. As chamadas compartilham uma sessão do runner; não são 30
atualizações independentes. As chamadas de aquecimento não possuem registros
seriais individuais.

O ELF é o build local com `-O3` efetivo em `workload/benchmark.c`, sem símbolos
de execução WAMR. O binário da aplicação tem SHA-256
`7bdbf68d9827d52ff524d9e8c246ac7a4caab0af8a1af196ab09bfb7706e731a`.
O [log de gravação](../native-app-flash.txt) registra apenas a imagem da aplicação
em `0x00020000`, com 745.872 bytes e verificação de dados pelo esptool.
O serial também registra o prefixo `35ed6729a` do hash do ELF esperado.

O [verificador offline](../verify-e1-native.py) confere boot único, ordem,
quantidade, checksum, status, identidade do build, hashes das evidências e
transferência. Usa apenas o parser de CSV do coletor; não usa sua validação
permissiva. A [tabela derivada](derived-native.csv) e o
[resultado derivado](derived-native.json) identificam seus insumos por SHA-256.
O [inventário](build-inventory.json) liga o relatório de build, comando efetivo
da unidade, assembly e trecho seguro do resultado de compilação. Não foram
copiados sdkconfig, credenciais nem o log completo de compilação.

O transporte desse firmware continua sem validação. A captura teve zero
pacotes UDP, IP do dispositivo ausente e quatro desconexões Wi-Fi de motivo
210. Também registrou `udp_disabled_no_observer:259` e uma falha de aquisição
do sensor `sensor_acquisition_failed:261`. Esses fatos permanecem no resultado;
o checksum sequencial não comprova conectividade nem serviço funcional.

Esta prova é preparatória, local e limitada a A. Não conclui a issue #7 de
toolchain no LXC, a viabilidade dos três modos ou a campanha. Execução WAMR
clássica/AOT, serviço funcional e atualizações reais continuam pendentes.

Para conferir novamente, mantendo disponível a imagem local com o hash acima:

```powershell
python docs/evidence/execucao-2026-10-09/verify-e1-native.py
python -m unittest discover -s docs/evidence/execucao-2026-10-09 -p test_verify_e1_native.py -v
```

O comando de conferência é somente leitura: recalcula os resultados e os
compara com os derivados preservados. Para gerar outra cópia, usar
`--derive-output build-session-e0/native-recheck` com diretório que ainda não
exista. Uma falha não apaga nem altera os derivados anteriores. `--prepare` é
uma operação explícita de preservação de build e não faz parte da conferência
somente leitura.

Os quatro testes do verificador passaram na revalidação de 10/10. Seus casos negativos recusam conjunto
vazio, checksum errado mesmo com `status=ok`, falha declarada, chamada faltante,
aquecimento incorreto, boot duplicado, linhas fora de ordem e ELF divergente.
O teste de falha por imagem ausente confirma que JSON e CSV anteriores ficam
intactos.

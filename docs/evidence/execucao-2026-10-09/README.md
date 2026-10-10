# Evidências da bancada em 09/10/2026

Este registro distingue a prova mínima de transporte da issue #6 dos ensaios de
serviço, HTTPS/COSE e medições de campanha. Os arquivos brutos foram preservados.
A derivação verificável está em [pair-verification.json](pair-verification.json),
com SHA-256 e tamanho dos arquivos brutos e das fontes consultadas.

## ESP32 → CT 107

O positivo aceito é [lxc-positive-r2](lxc-positive-r2/summary.json), com servidor
temporário no CT 107 `192.168.0.138:18080` e ESP32 `192.168.0.145`. O firmware
registrou `ok`; o [leitor LXC](lxc-positive-r2-server.jsonl) registrou um pedido
`response_sent` com o mesmo nonce de 256 bits:

`731492b12bc06c09b1be2583f6b7f03c39c90feec4d95bb6308d1405f08a51e3`.

Há um único boot serial, três frames UDP distintos antes do comando e 164 depois
da resposta, totalizando 168, todos com o boot `4f876cd0df04d7e5`. O pareamento
usa nonce e endereços; não compara relógios do PC com UTC do LXC. O leitor
encerrou pela duração configurada e registrou `stopped`.

A [primeira captura](lxc-positive/summary.json) permanece inconclusiva para
continuidade. O terceiro frame UDP e a emissão do comando têm o mesmo timestamp
no relógio `time.monotonic_ns` do Python 3.11 no Windows. O resumo contou apenas
dois frames estritamente anteriores. Não reaproveitar esse resultado como aceite.
O repetidor R2 usa `time.perf_counter_ns`, com QPC de alta resolução, e preserva
as mesmas comparações estritas.

## Recusa controlada de origem

Em [lxc-negative-source](lxc-negative-source/summary.json), o leitor foi
configurado para esperar `192.168.0.146`. O [log do servidor](lxc-negative-source-server.jsonl)
registrou uma origem real `192.168.0.145` e `rejected_source`, sem nonce. O ESP32
registrou `response_mismatch`, sem `ok`, e manteve o boot e o fluxo UDP, com três
frames anteriores e 164 posteriores ao resultado.

Os dois lados observaram a condição controlada neste ensaio isolado. O pedido
recusado não fornece nonce para parear a transação individual entre os logs.
Esse limite consta da derivação. O ensaio não comprova ausência do leitor,
timeout de servidor ou autenticidade de serviço.

## Plataforma e reconstrução

O [diagnóstico inicial de plataforma](platform/summary.json) registra comunicação
PC ↔ ESP32, descarte de pedido com boot incorreto e de datagrama truncado. O
observador era este PC `192.168.0.190`, interface Ethernet 2, índice 11.

O CT 109 foi usado apenas para reconstrução separada. Os arquivos
[antes](lxc-rebuild-before-reboot.txt), [depois do reboot](lxc-rebuild-after-reboot.txt)
e [SSH após reboot](lxc-rebuild-ssh-after-reboot.txt) preservam o ensaio de
persistência. O [estado final](lxc-rebuild-final.txt) registra CT 109 parado e
CT 107 em execução. O positivo de transporte acima se refere ao CT 107 e não
estabelece um caminho ESP32 → CT 109.

A reserva DHCP de `192.168.0.138` para a MAC `BC:24:11:B6:36:F7` foi confirmada
pelo usuário nesta sessão. Não há captura administrativa do servidor DHCP
entre estes arquivos.

## Derivação e pendências

Executar com o Python 3.11 do SDK, que já tem pyserial:

```powershell
C:\esp\tools-task8\python_env\idf5.2_py3.11_env\Scripts\python.exe `
  docs/evidence/execucao-2026-10-09/verify-lxc-pair.py `
  --output docs/evidence/execucao-2026-10-09/pair-verification-check.json
```

O arquivo de saída deve ser novo. Para repetir a conferência da restauração,
é necessário o backup local ignorado `build-session-e0/app-original-1m.bin`;
ele não foi copiado para esta pasta, pois o firmware contém configuração local.
O verificador confere dados UDP codificados,
linhas seriais, resumo, nonce e endpoints; rejeita logs de leitor ainda ativo,
mais de um pedido e mudanças nos arquivos durante a derivação. As limitações
do negativo e da primeira captura não são removidas pela ferramenta.

- Checksum nativo no dispositivo: comprovado no [registro preparatório de A](native/README.md), com 30 chamadas na mesma sessão; não comprova E1 nos três modos nem a campanha.
- Restauração do app anterior: gravação e verificação de leitura confirmadas.
- HTTPS/COSE e serviço funcional: fora do escopo destes ensaios.

Não concluir a issue #6 ou registrar conclusão das pendências acima com base
apenas nesta derivação de transporte.

## Restauração e encerramento do instrumento

O [flash de restauração](app-restore-flash.txt) gravou a região de 1 MiB em
`0x20000` a partir do backup local com SHA-256
`b00a479e56a1a486703093e93683de845dd09ae5392152a10f49fe9b15cde171`.
O [verify_flash](app-restore-verify.txt) confirmou `verify OK (digest matched)`
para os mesmos 1.048.576 bytes. O backup é uma região de restauração; os slots
OTA físicos continuam com 7 MiB.

O [diagnóstico após restauração](restore/summary.json) tem boot
`7a90e79f8778787c`, 11 linhas de diagnóstico, das quais 10 têm amostra válida.
A primeira linha ainda aguardava aquisição. As válidas registraram 25 °C;
GPIO solicitou/observou 0/0 e 1/1, e foram recebidos 88 datagramas UDP. O pedido
válido foi observado, os casos de boot incorreto e truncamento foram descartados,
e não houve desconexão Wi-Fi no intervalo registrado.

O [comando final corrigido](lxc-session-final-confirmed.txt) registrou remoção
do leitor, ausência de listener em `18080`, CT 107 em execução com IP
`192.168.0.138` e CT 109 parado. O primeiro comando usou sintaxe inválida de
`ip` para consultar a interface; o erro está no transcript da ferramenta.
O [arquivo daquela tentativa](lxc-session-final.txt) contém somente os status
dos CTs, pois a saída da consulta SSH não entrou no pipe. Essa tentativa não
foi tratada como prova do endereço.

A [validação local](validacao-local.md) registra 68 testes sem skips na revalidação de 10/10 e a
reexecução dos verificadores contra os dados físicos. `.gitattributes` preserva
os bytes dos registros e das fontes usados pelo inventário da sessão para que
o checkout não altere os SHA-256 por conversão automática de finais de linha.

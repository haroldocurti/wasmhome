# Diagnóstico temporário ESP32 → LXC

Instrumento para a prova mínima de transporte da issue #6. Não implementa HTTPS,
COSE, distribuição de artefatos ou serviços de campanha. O pedido iniciado pelo
ESP32 e a resposta conferida no dispositivo foram registrados nesta sessão;
os resultados constam ao final deste documento.

## Preparação

O firmware deve usar `CONFIG_WASMHOME_PLATFORM_SELFTEST=y` e
`CONFIG_WASMHOME_LXC_PROBE_IPV4="192.168.0.138"` somente no `sdkconfig` local
ignorado. Não incluir senha Wi-Fi ou configuração local na evidência. A serial
aceita apenas `WH_LXC\n`: não há parâmetro de URL. Porta TCP `18080` e caminho
`/wasmhome-lxc/<nonce>` são fixos. O nonce contém 256 bits aleatórios em hexadecimal.

O alvo físico deste diagnóstico é o CT 107 de trabalho, `192.168.0.138`, e o
ESP32 `192.168.0.145`. O CT 109 é a reconstrução separada; este diagnóstico
não comprova seu caminho de rede. Revalidar os endereços antes do ensaio.
Copiar o script preservado e conferir seu
SHA-256 no CT. Executar como usuário operacional, com Python 3 disponível:

```sh
python3 /tmp/probe_lxc_network.py --bind 192.168.0.138 \
  --expected-peer 192.168.0.145 --duration 300 > /tmp/lxc-network.jsonl
```

O comando acima usa a duração padrão de 300 segundos. O parâmetro aceita entre
1 e 600 segundos. O servidor encerra ao atingir essa duração, sem serviço systemd, alteração de firewall
ou configuração global. Recusa fontes diferentes do IP configurado, nonce inválido,
outro caminho e pedido maior que 1.024 bytes. Cada conexão tem prazo total de dois
segundos. Encerrar o processo somente deste instrumento ao fim e preservar o log;
remover sua cópia temporária depois da coleta.

## Ensaio e aceite de transporte

1. Preservar hash da fonte, binário, sdkconfig saneado e firmware anterior.
   Manter o app em `0x20000` e preservar a região de 1 MiB usada para backup e
   restauração. O slot OTA físico tem 7 MiB, conforme o boot registrado nesta sessão.
2. Iniciar o leitor no LXC e registrar seu evento `listening`.
3. Enviar `WH_LXC\n` pela serial. A tarefa separada tem prazo total de 2.500 ms;
   a aquisição e UDP continuam na tarefa de diagnóstico existente. Comandos
   concorrentes são recusados; linha serial longa é descartada até nova linha.
   `WH_WDT` também recusa execução durante o teste LXC e registra
   `selftest_watchdog_busy`, sem suspender a tarefa de diagnóstico.
4. Preservar `WASMHOME_LXC` do ESP32 e o evento `response_sent` do LXC. Ambos devem
   ter o mesmo nonce; `source_ipv4` do servidor deve corresponder ao
   `local_ipv4` do ESP32 e ao IP esperado. O dispositivo só reporta `ok` após
   conferir a resposta HTTP inteira, incluindo nonce, endereço observado e EOF.
5. Ensaiar leitor ausente ou fonte esperada divergente: não aceitar `ok`.
   Guardar a evidência negativa. Um SYN, ping, `response_sent` isolado ou teste
   local em Python não comprova o percurso físico completo.
6. Encerrar leitor, preservar logs e restaurar o firmware anterior conforme o
   procedimento de bancada. Registrar o resultado e possíveis divergências.

Os testes locais em `tests/test_lxc_network.py` cobrem TCP com nonce e origem,
recusa de fonte, caminho/nonce malformados, limite de tamanho, silêncio/pedido
incompleto e resposta com nonce, origem ou contrato alterados. Eles validam o
instrumento Python; o build e o ensaio físico validam sua contraparte embarcada.

## Validação local de 09/10/2026

Os sete testes locais passaram. O build diagnóstico isolado com ESP-IDF 5.2.6,
Python 3.11 e alvo `esp32s3` terminou com código zero, sem aviso ou erro no
`platform_selftest.c`. O log local está em `build-lxc-network/build.log`.

O binário `build-lxc-network/wasmhome_bench.bin` tem 775.200 bytes, menor que
1.048.576 bytes. SHA-256:
`b1bed00b0c7981dbbb0db640d4479b1a8795a123e15f4a9538c98505f9af3589`.
Fonte `main/platform_selftest.c`, SHA-256:
`5e16f728cf543239c830635b5578a0e5cb8bbb76f579a137131d438b65c604bb`.
Leitor `tools/probe_lxc_network.py`, SHA-256:
`d94a8e9772eeafe540ec45361defbcde3e8cbe2048f4a955d3f0f339d98f8ec7`.

Este build já contém a correção da revisão adversarial para impedir `WH_WDT`
durante a prova LXC. As duas operações reservam o mesmo estado em seção crítica;
uma recusa não dispara watchdog nem atrasa a tarefa de diagnóstico. O log local
tem SHA-256 `7f41485f366de2c63ed671d2e09a11d6cdc7b09eec6c3260637ed9ae2c1b1db0`.
O build comprova compilação; a recusa física do comando concorrente deve ser
conferida na serial durante o ensaio e não foi inferida dos testes Python.
Após normalizar exclusivamente os finais de linha do C para LF, o rebuild
incremental passou e manteve o mesmo SHA-256 do binário.

O capturador desta sessão registra eventos e emissão do comando com
`time.perf_counter_ns`, que usa QPC de alta resolução no Windows. O relógio é
identificado no resumo. Timestamps iguais continuam inconclusivos: não se
considera um frame anterior ao comando quando ambos têm o mesmo timestamp.

A configuração foi copiada silenciosamente do diagnóstico existente para o
diretório de build ignorado. Nenhum arquivo de configuração foi preservado
na documentação. Para reproduzir o build, exportar o SDK em
`C:\esp\v5.2.6-task8`, com `IDF_TOOLS_PATH=C:\esp\tools-task8` e o Python
`C:\esp\tools-task8\python_env\idf5.2_py3.11_env\Scripts` primeiro no PATH:

```powershell
idf.py -B build-lxc-network -D SDKCONFIG=build-lxc-network/sdkconfig `
  -D EXPERIMENT_SCENARIO=native -D IDF_TARGET=esp32s3 build
```

O build usa a tabela atual do repositório, que define apps de 7 MiB. O boot físico
registrado em `evidence/execucao-2026-10-09/platform/serial.log`, linhas 25 e 26,
confirma `ota_0` em `0x20000` com tamanho `0x700000` e `ota_1` em `0x720000`
com tamanho `0x700000`. A região preservada para restaurar o app anterior é de
1 MiB; ela não representa o tamanho da partição. Seu backup tem SHA-256
`b00a479e56a1a486703093e93683de845dd09ae5392152a10f49fe9b15cde171`.

Gravar somente o binário diagnóstico em `0x20000`, depois da revisão adversarial,
para manter a tabela e as demais regiões e permitir a restauração do app anterior.
Não executar o comando de flash completo gerado pelo build. Esta validação local
isolada não comprova o teste ESP32 → LXC; a prova física está registrada abaixo.

## Resultado físico de 09/10/2026

O positivo definitivo é `evidence/execucao-2026-10-09/lxc-positive-r2`: ESP32
`192.168.0.145` iniciou pedido ao CT 107 `192.168.0.138:18080`, recebeu resposta
e validou nonce e endereço observado. O servidor registrou o mesmo nonce.
Houve um boot único, três frames UDP antes do comando e 164 depois do resultado.
A primeira captura permaneceu inconclusiva por empate de timestamps e não foi
usada para aceitar continuidade.

O ensaio controlado com leitor esperando `.146` registrou recusa da origem
real `.145`; o dispositivo respondeu `response_mismatch` sem `ok`, com fluxo
UDP contínuo. Como a recusa não ecoa nonce, a derivação registra os dois lados
da condição e preserva o limite de pareamento da transação negativa.

O operador principal restaurou a região anterior de 1 MiB em `0x20000` e
confirmou leitura com `verify_flash`. O diagnóstico restaurado teve 11 linhas,
10 válidas, uma inicial ainda aguardando aquisição, e 88 datagramas UDP.
O leitor foi removido e a porta temporária ficou sem listener. Detalhes e hashes
constam em [registro da sessão](evidence/execucao-2026-10-09/README.md) e
[derivação](evidence/execucao-2026-10-09/pair-verification.json). HTTPS/COSE e
serviços funcionais permanecem fora da prova apresentada.

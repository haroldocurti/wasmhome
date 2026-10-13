# Registro da execução e retomada pelo Linux

Consolidado para publicação em 12/10/2026, com snapshots e mudança de plano
registrados em 11/10. O usuário decidiu iniciar o computador pelo Linux e
autorizou documentar o trabalho e publicar todas as entregas pendentes na `main`.
A alternativa CT110 fica arquivada como proposta substituída. Nenhuma VM foi
desligada e nenhum CT110 foi criado ou iniciado.

Este registro reúne o estado de execução, as fontes de prova e o próximo ponto
de trabalho. O [checklist](checklist-execucao.md) continua organizado por E0–E7;
o [plano detalhado](plano-detalhado-coleta-analise.md) define a coleta futura.
Preparação, compilação, execução física e campanha são provas diferentes.

## Entregas integradas antes desta publicação

| Entrega | Resultado e limite |
|---|---|
| Plataforma comum, #8 | Integrada pela PR #27. Diagnóstico físico de sensor, GPIO, watchdog e UDP; não implementa os dois serviços definitivos da campanha. |
| Observador externo, #10 | [PR #28](https://github.com/haroldocurti/wasmhome/pull/28), merge `6c94ab505672e4056c1a87932403853fe9aa06e7`. 30 testes locais e recalibração do host, com registros preservados. A fixture não comprova OTA ou troca modular. |
| Bancada E0 e preparação E1 | [PR #29](https://github.com/haroldocurti/wasmhome/pull/29), merge `8b372147e739e46c8952ab8c7907b6c76a76345b`, árvore `56a6f161277d16ba725a536b76230aecf1e120d6`. 47 testes de plataforma e 21 de evidência, sem skips; 67 blobs conferidos por SHA-256. |
| Sistema-base LXC, #6 | Encerrada com sete critérios apoiados em evidência, comentário de resolução, remoção de `ready-for-agent` e atualização do mapa #1. Não encerra a toolchain #7. |

As árvores finais das duas PRs foram verificadas após a integração. Não havia
CI configurada; as contagens acima são de testes locais. Os recibos de
[integração e rastreador](evidence/retomada-linux-2026-10-11/integration/)
conservam SHAs, logs e limites. O snapshot de
[issues em 11/10](evidence/retomada-linux-2026-10-11/issues-2026-10-11.json)
confirma #6/#8/#10 fechadas e #7/#9/#11–#24 abertas. A divergência antiga das
caixas/rótulo de #2 continua registrada; não foi corrigida nesta publicação.

## Bancada e ensaios físicos de 09/10

- ESP32-S3 revisão 0.2, flash 16 MB, PSRAM 8 MB, MAC `dc:b4:d9:0b:22:e4`.
  COM12 era o adaptador CH343, VID `1A86`, PID `55D3`. No Linux, descobrir a
  porta novamente; COM12 não é um caminho Linux.
- PC observador `192.168.0.190`, ESP32 `192.168.0.145`, CT107 `192.168.0.138`.
  Endereços são históricos e precisam de reconfirmação por sessão.
- Diagnóstico: sensor em 11/11 leituras, GPIO nos dois níveis, 87 datagramas
  UDP, recusa de boot incorreto e de datagrama truncado.
- ESP32→CT107 foi demonstrado por TCP/HTTP em porta temporária 18080, com nonce,
  origem validada e continuidade UDP no mesmo boot. A primeira captura com
  empate temporal ficou inconclusiva e foi preservada. A captura R2 passou.
  O ensaio negativo recusou origem `.146` quando a origem real era `.145`.
  Isso não é ensaio de envelope ou atualização.
- O nativo preliminar fez cinco aquecimentos e 30 chamadas com checksum
  `0xDBB2F0D4`. Elas pertencem a um único boot/carga. Não são 30 atualizações.
  Nesse firmware houve falha de Wi-Fi, sem endpoint/IP e sem UDP funcional;
  essa captura não comprova a aplicação da campanha.
- A aplicação original foi restaurada apenas na região de 1 MiB em `0x20000`.
  A conferência de flash coincidiu com o backup SHA-256
  `b00a479e56a1a486703093e93683de845dd09ae5392152a10f49fe9b15cde171`.
  Bootloader, tabela de partições e NVS não foram regravados. Após a restauração,
  sensor e GPIO passaram, com 88 datagramas UDP e sem desconexão Wi-Fi observada.

As capturas e suas revisões estão no
[registro físico original](evidence/execucao-2026-10-09/README.md).
Os ensaios preliminares do observador ainda locais foram arquivados em
[observer-preliminary](evidence/retomada-linux-2026-10-11/observer-preliminary/),
incluindo a captura sem boot/sem controles. Não substituem as calibrações
finais de [02/10](evidence/observer-2026-10-02/) e
[10/10](evidence/observer-revalidation-2026-10-10/README.md).
Não houve novo flash ou coleta física durante o preparo de 10/10 ou esta publicação.

## Infraestrutura preservada

CT107 `wasmhome-bench` permanece na bancada, Debian 13.1, 1 CPU, 1024 MiB de RAM,
swap 0, disco 24 GiB. O endereço `.138` usa a MAC `BC:24:11:B6:36:F7`; a reserva
DHCP foi confirmada pelo usuário, sem inspeção do painel DHCP.
O acesso de trabalho é `wasmhome@192.168.0.138`, UID 1000.

CT109 `wasmhome-bench-rebuild` foi reconstruído com a configuração autorizada,
testado para acesso e persistência após reboot e deixado parado. O listener
temporário 18080 foi retirado. O preparo da toolchain não mudou os limites dos
CT107/109 nem o Python do sistema.

A [consulta de 11/10 às 10:17, horário de Brasília](evidence/retomada-linux-2026-10-11/ct107/proxmox-inventory-20261011T131737Z.json)
encontrou CT100, 101, 105, 106, 107 e 108 ligados; CT109 parado; VMs102, 103 e
104 ligadas. Host `haroldo`, `192.168.0.99`, com 2,72 GiB disponíveis e 6,45 GiB
de swap usada. São snapshots, não promessas de capacidade ou de memória
recuperável. Nenhum guest foi interrompido para criar o doador.

## Port candidato e correções adversariais

Os commits locais `40aca4c14c09a2fe5058eec9bfce9c7b08f5379c` e
`187ff96279ed8e6d3da14b7663ce5b5e856dff49` são incorporados nesta publicação.
A árvore de código usada no CT107 foi
`70580c660a5642e9f7226c0d9813b805ee8b3c64`.

O [port candidato](runtime-port-candidato.md) é
`wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.1, AOT formato 5. O
[lock](../runtime-port/lock.json) fixa upstream WAMR
`25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, framework
`e97860611e371d91d2ef25101b02b29719476a48` e o delta de cinco arquivos.
O digest materializado é
`7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`.
A referência Espressif é uma trilha de revisão do patch, sem autenticação
automática da sua origem histórica. O core upstream foi preservado.

As quatro correções da revisão inicial foram:

1. Publicação de artefatos em conjunto novo, com contenção de symlinks/reparse,
   backup, rollback e journal. São duas renomeações, com intervalo sem destino
   canônico; não há troca atômica única nem suporte a leitores concorrentes.
2. Proveniência de WasmHome, ESP-IDF, WASI-SDK e binário real `wamrc`, vinculada
   a fontes, comandos, caches, ferramentas e bibliotecas por hashes.
3. JSON sem chaves duplicadas/números não finitos e validação semântica de
   componente, versão e AOT.
4. Remoção da supressão global `-Wno-type-limits`.

O candidato corrigido passou por 60 testes MSVC, sem skips, e revisão Luna.
Os [pareceres e logs](evidence/retomada-linux-2026-10-11/runtime-review/)
preservam a revisão fixada. Essa revisão libera a preparação do candidato;
não substitui compilação B/C, origem do compilador ou prova no ESP32.

## Toolchain preparada no CT107 em 10/10

Diretório dedicado, preservado no CT107:

```text
/home/wasmhome/.local/toolchains/wasmhome-e1-20261010-187ff962
```

O [recibo completo do preparo](evidence/retomada-linux-2026-10-11/ct107/progresso-toolchain-ct107.md)
e os arquivos ao seu lado registram:

- Instalação de exatamente 21 pacotes novos com versões fixadas, sem upgrade
  ou remoção de outros. Simulações mais amplas foram preservadas e não executadas.
- Python do sistema mantido em 3.13.5, pacote 3.13.5-2, SHA-256 do executável
  `e59d0124ff06c248546876e01fcfb1ea3cda63534949f94a9372bfcfe3bfc3f5`.
- Venv `idf526-py313`, pip 26.2.1 e 57 distribuições resolvidas no Linux real,
  com URLs/hashes congelados e instalação offline. `pip check`, imports e
  verificação das dependências oficiais do IDF passaram. Não houve pip global.
- ESP-IDF 5.2.6, origem oficial, commit
  `9ef24e3e2a2c96e720d83c574a3f8699177573da`, submódulos e ferramentas conferidos.
  Ferramentas ficam em `idf-tools`; não foi executado `install-python-env`.
- WASI-SDK 27 em `wasi-sdk/wasi-sdk-27.0-x86_64-linux`, tar oficial SHA-256
  `b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2`.
- PowerShell 7.6.6 em `powershell-7.6.6`, tar SHA-256
  `ddbc4a2d113bbd46d283cfedcbcd117a70caefd7673f41f2b4e0000badf103bc`.
- Bundle Git da revisão 187ff96 transferido, checkout `project` limpo e runtime
  materializado/verificado. Não existem LLVM/wamrc reais nesse preparo.

## Build nativo real no CT107

`build-e1-native-ct107` concluiu 994 passos com exit 0, cenário nativo ESP32-S3,
240 MHz, PSRAM, workload `-O3`, um job por vez e sem ccache. Configurações de
Wi-Fi/observador ficaram vazias; esse binário não foi gravado na placa.

| Saída | Bytes | SHA-256 |
|---|---:|---|
| App BIN | 737088 | `bef4b0b0ce7061d3f2cb693a8d3f0240a95d84a80fa31b5379aaa3bb2e74f73c` |
| ELF | 9196308 | `3ffca7b09a450e0ac63f5e0d1ea67fb3d672eb55684ad9b6d7953898f90df500` |
| compile_commands | 7312459 | `99eb38926103ec6828155be728fcab761bddc3f5084791f22d23816e756d84c6` |

Foi coletado o lock de dependências e o pacote completo de
`espressif/onewire_bus` 1.0.2. Os 16 hashes de `CHECKSUMS.json` e as quatro fontes
externas realmente compiladas coincidiram. O inventário contém 18 arquivos.
O disassembly conservou o laço de 2000 iterações, sem substituir o workload por
retorno constante. Luna revisou a afirmação limitada de compilação concluída.

As primeiras transferências de ELF por SCP/SSH ficaram parciais e foram
preservadas, assim como a colisão inicial de coleta. A transferência R4 trouxe
o ELF completo, cujo hash coincidiu com o remoto. O objdump Xtensa local
produziu os mesmos tokens de endereços, opcodes e instruções que o remoto;
somente o espaçamento de colunas diferiu. Os
[recibos nativos](evidence/retomada-linux-2026-10-11/ct107/native-final-ct107/)
incluem o complemento R4. O build-check bruto anterior ainda diz disassembly
pendente; esse estado foi resolvido somente para o nativo pelos complementos,
sem reescrever o snapshot original.

Os BIN/ELF completos e as cópias interrompidas permanecem locais e no diretório
dedicado do CT107. Seus hashes estão no mapa de preservação; não são fontes
versionadas. No Linux, recuperá-los do CT107 ou recompilar em diretório novo.
O arquivo local completo era `build-session-e1/native-final-ct107/elf-r4/wasmhome_bench.elf`.

## Alternativas de doador examinadas, sem build

WSL Ubuntu 24.04.1 possuía CPU/RAM para tentativa, mas o disco físico C: tinha
13,87 GiB livres contra a reserva proposta de 20 GiB. O espaço virtual do VHD
não demonstrava espaço físico. A rede apresentou erro e fallback sem DNS/rota.
Dependências foram apenas simuladas; não houve reparo WSL ou limpeza de disco.

Foi preparada a alternativa CT110, Debian 13.1, 2 CPUs, 8 GiB de RAM, swap 0,
32 GiB em DevServer, DHCP, unprivileged e estado final parado. O template foi
conferido. A proposta precisava de 10 GiB disponíveis no host antes do início,
incluindo margem, e não recebeu autorização de criação ou de parada de guests.
As medições de RAM não atenderam à margem.

Os [scripts, índices, testes e revisões do doador](evidence/retomada-linux-2026-10-11/donor/README.md)
preservam a receita R3. Foram corrigidos três achados concretos: cleanup após
spawn, whitelist de configuração do CT e a janela de interrupção do Runner
antes de registrar o subprocesso. Sol e coordenador executaram 28 testes Linux;
foram conferidos 40 arquivos do índice R3. A revisão Luna R3 foi focal, sem
achado residual nessa correção. Não houve supervisor em CT110 real.

Um parecer intermediário R2 foi sobrescrito antes de sua preservação. Os bytes
originais não foram recuperados. O texto reconstruído está marcado como não
original e não serve de prova. A revisão R2 consolidada e a focal R3 são os
pareceres preservados atuais. Não alegar preservação integral daquele parecer.

## Execução no Linux em 12/10

O Linux físico foi identificado como Omarchy 4.0.4/Arch x86_64. O checkout
`main` corresponde ao commit `a7c4f72` e à árvore `f8e3ad5`; por falta de
autenticação Git na CLI, ele é raso. Os hashes do arquivo de evidências foram
conferidos e o runtime 2.4.5 foi adquirido/verificado. Esta retomada deixa
alterações locais de código e documentação. O snapshot isolado usado nos builds
do CT107 é `801f9381da1a8ed0895adc88e8cd03f3a1df6707`, árvore
`c728d9ed348e7fdefe4dac403a4c42b6916e704a`.

LLVM Espressif no pin `344b928e` compilou em Release para X86/Xtensa. O
`wamrc` R4 compilou, mas não executou no CT107: o Arch linkou `cosh`/`sinh`
em GLIBC 2.44 e `sqrtf` em GLIBC 2.43, enquanto o guest Debian 13.1 usa
glibc 2.41. R5 relinkou o `wamrc` contra o `libm.so.6` 2.41 do CT, sem
instalar ou substituir bibliotecas do guest. O binário R5 e o recibo foram
transferidos para um diretório novo no CT107; hashes, versão, dependências e
consultas de alvo/CPU passaram. A tentativa R4 incompatível permanece
preservada separadamente.

O recibo unsigned, comandos, logs e a comparação R4/R5 estão no
[complemento de evidência de 12/10](evidence/retomada-linux-2026-10-12/README.md).
Depois disso, os artefatos Wasm/AOT foram gerados duas vezes em árvores limpas
no CT107. As cinco saídas binárias coincidem por SHA-256; os manifests diferem
nos caminhos absolutos da origem. O ajuste de `wamrc` usa `--opt-level=3`, pois
esta versão rejeita `-O3`. O primeiro build interpretado também revelou que a
descoberta antecipada de componentes do ESP-IDF não herdava a variável CMake do
cenário; a configuração agora propaga o cenário pelo ambiente e adia a checagem
de arquivos de artefato durante essa expansão.

Os cenários nativo A, interpretado B e AOT C compilaram em diretórios separados
no mesmo snapshot `801f938`, ESP-IDF 5.2.6, alvo ESP32-S3, clock de 240 MHz e
workload `-O3`. Os três recibos passaram pelo validador de configuração,
símbolos e proveniência. O `benchmark_run` nativo e um objeto ELF Xtensa
derivado do `.wasm` foram inspecionados com `objdump`; ambos preservam o laço de
2.000 iterações. O assembly Wasm também contém o laço. Os recibos, hashes,
manifests, logs e disassembly estão no
[complemento E1](evidence/retomada-linux-2026-10-12/e1-scenarios/README.md).

Isso comprova geração e compilação no CT107, não execução do firmware. Não houve
gravação na placa nem execução de checksum no ESP32-S3; também falta a
reconstrução/atestado independente do recibo unsigned do compilador. Nenhuma
suíte de testes foi executada. #7 e #9 continuam abertas.

## Checklist para continuar no Linux

- [x] Integrar os commits candidatos, o plano pendente e os registros desta sessão.
- [x] Registrar distribuição, arquitetura, espaço físico, memória, rede e
  ferramentas do Linux físico; consulte o complemento de 12/10.
- [x] Conferir o snapshot inicial de `main` no commit/árvore publicados. O
  checkout é raso por falta de autenticação Git na CLI; alterações atuais são
  locais e o snapshot histórico `187ff96` não foi usado nos builds novos.
- [ ] Executar os testes locais com codec C compilado; essa suíte não foi executada.
- [x] Conferir os hashes do arquivo de evidências e adquirir/verificar o port
  com `tools/runtime_package.py`.
- [x] Adaptar a execução do doador ao Linux físico. Os perfis arquivados `wsl`
  e `lxc110` não são uma aprovação nem validação desse novo ambiente. Revalidar
  caminhos, versões/dependências e capacidade; usar diretórios exclusivos e logs.
- [x] Compilar LLVM Espressif no pin
  `344b928e67bfc1d07cb150609c55548e86a9bf19`, targets X86/Xtensa, Release/Ninja,
  LTO desligado e um job. Compilar `wamrc` 2.4.5 e registrar comandos, caches,
  versões e hashes. R5 usa o `libm` 2.41 do CT107 no link local para compatibilidade
  com o guest; comandos e logs estão no complemento de 12/10.
- [x] Emitir o recibo unsigned com `tools/record_wamrc_build.py` e revisar
  proveniência, target, CPU e compatibilidade do executável host no CT107. O
  recibo não é assinado e não autentica sozinho a associação binário/build.
  Não exigir duas compilações de LLVM como regra nova: a repetição documentada
  é dos artefatos A/B/C.
- [x] Transferir/conferir binário e recibo R5 no CT107; bibliotecas dinâmicas e
  consultas do `wamrc` real passaram. R4 falhou na glibc do guest e foi preservado.
- [x] Gerar A/B/C no CT107 e repetir os artefatos em outra árvore limpa. As cinco
  saídas Wasm/AOT coincidiram por SHA-256 entre R6/R7; os manifests conservam
  caminhos diferentes. Aceite e atestado independente do compilador continuam
  pendentes para #7.
- [x] Compilar firmware A/B/C no CT107 com configs isoladas e a mesma revisão;
  os validadores conferiram cenário, símbolos, AOT v5, bounds checks e runtime.
  A inspeção Xtensa confirma o laço do workload no objeto AOT.
- [ ] Executar checksum e comparar A/B/C no mesmo ESP32-S3. Nenhum firmware foi
  gravado nesta retomada; #9 segue aberta.
- [ ] Continuar #11–#24 pelos gates do plano; manter as campanhas normal e de
  resiliência separadas. Não considerar esta publicação encerramento de #7/#9.
- [ ] Antes de #20/#21, conciliar a referência de 90 tentativas com a agenda
  proposta de 90 slots, que pode gerar menos tentativas aceitas. A publicação
  preserva essa decisão pendente; não altera silenciosamente a amostra aprovada.

Para consultar o código pelo Linux:

```sh
git clone https://github.com/haroldocurti/wasmhome.git
cd wasmhome
git status --short
git rev-parse HEAD
python3 -B tools/runtime_package.py --acquire
python3 -B tools/runtime_package.py
```

O repositório requer acesso à conta autorizada. Não copiar credenciais do
Windows para o Git. A receita de testes com MSVC é específica do Windows;
no Linux, compilar o codec como biblioteca compartilhada e usar a variável
`WASMHOME_WIRE_LIBRARY`, conforme o guia de reprodução do arquivo de evidências.

## Preservação e limites desta publicação

O [arquivo de evidências](evidence/retomada-linux-2026-10-11/README.md) contém
recibos/logs byte a byte, mapas portáveis e índices SHA-256. Caminhos Windows
ou absolutos presentes nos snapshots identificam o ambiente de origem; não
são caminhos válidos de uma nova instalação Linux.

Backups da aplicação, caches de toolchains, artefatos gerados, renders acadêmicos
em `tmp/` e fotografias em `.codex-remote-attachments/` ficam preservados localmente.
Eles não são alterações de fonte pendentes para a `main`. Os registros relevantes
do projeto foram arquivados; a exclusão e seus motivos constam em `source-map.json`.
As árvores de trabalho originais não foram apagadas. Esta execução criou builds e
evidências apenas nos diretórios dedicados do CT107; não instalou pacotes do
sistema, não gravou firmware nem iniciou coleta experimental.

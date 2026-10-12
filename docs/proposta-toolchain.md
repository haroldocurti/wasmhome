# Proposta de execução da toolchain, issue #7

Atualização de estado em 11/10: o [registro de retomada pelo Linux](retomada-linux.md)
supera os estados históricos abaixo. SDKs/venv e build nativo no CT107 foram
executados; LLVM/wamrc real, geração/repetição A/B/C e aceite #7/#9 seguem pendentes.


Preparada em 09/10/2026, concluída em 10/10/2026. Status: proposta verificável,
sem instalação, build LLVM, geração Wasm/AOT ou alteração remota executados.
O preflight começou na árvore `a58a516c6f72545c317a25a5085367c9ba3ee82e`.
A correção posterior de LTO foi autorizada pelo coordenador desta entrega e
a revisão final precisa apontar a nova árvore integrada, não reutilizar o SHA
anterior como prova da correção.

A atualização de 10/10 está em [proposta-toolchain-decisoes.md](proposta-toolchain-decisoes.md), com PowerShell/Python e a alternativa runtime + compilador 2.4.5. A receita 2.4.0 abaixo é candidata histórica; não executar antes da decisão de versão e da revisão do pacote correspondente.

## Resultado do preflight

A combinação tem origem oficial suficiente para preparar um compilador Xtensa
por fonte. Ainda não existe prova de um binário compatível na bancada. A melhor
rota é construir `wamrc` num Linux doador, instalar o binário com recibo no
CT107 e fazer o CT107 gerar os três artefatos. A issue exige produzir artefatos
no LXC, não exige compilar as bibliotecas LLVM dentro dele. Essa interpretação
deve constar da revisão do aceite.

A issue [#7](https://github.com/haroldocurti/wasmhome/issues/7) estava aberta,
com os três critérios desmarcados e dependência de #6. O aceite formal da #6,
a revisão e a publicação de suas evidências ainda são gates próprios. A integração
de #10 continua necessária antes de #11, sem mudar o escopo desta proposta.

| Ambiente | Evidência somente leitura | Implicação |
|---|---|---|
| CT107 | `wasmhome-bench`, UID 1000, Debian 13.1, x86_64, MAC `BC:24:11:B6:36:F7`, IPv4 `192.168.0.138/24` | A identidade bate com a bancada. |
| CT107 | 1 CPU, RAM 1024 MiB, 998 MiB disponíveis na amostra, swap 0, raiz 24 GiB e aproximadamente 22 GiB livres | Não iniciar LLVM nesse limite. Builds IDF sequenciais ainda precisam de observação de memória. |
| CT107 | Python 3.13.5 e glibc 2.41. Não encontrados no PATH: git, cmake, ninja, cc, c++, pwsh, clang, wasm-ld, wamrc, idf.py | Instalação e validação de dependências permanecem pendentes. `python3-venv` não estava instalado. |
| Proxmox | 6 CPUs, RAM total 15861 MiB, disponível 1732 MiB, swap 4402 MiB ocupada | Há pressão de memória. Não acrescentar carga LLVM nesse host. |
| Proxmox | CT107 running; CT109 stopped. Configuração CT107 confirma CPU/RAM/swap/disco/MAC acima | Nenhum guest foi parado ou iniciado. |
| PC | Windows 11 Pro, 16 processadores lógicos, RAM 27,75 GiB, livre 15,77 GiB; C: livre 27,82 GiB | Mais adequado como doador. O espaço livre é uma amostra, não orçamento garantido do build. |
| WSL | Ubuntu e docker-desktop instalados, versão 2, ambos Stopped | Ubuntu é candidato concreto a doador. Não foi iniciado e seu ambiente Linux ainda não foi inspecionado. |

SSH ao CT usou BatchMode, StrictHostKeyChecking=yes e
`build-session-e0/known_hosts`, cuja confiança foi estabelecida anteriormente
pelo canal Proxmox. A inspeção do host também exigiu a chave já conhecida.
Não foram lidas configurações Wi-Fi, tokens, chaves privadas ou ambiente completo.

## Pins e origens verificadas

| Componente | Pin candidato | Origem verificável e limite |
|---|---|---|
| ESP-IDF | 5.2.6, commit `9ef24e3e2a2c96e720d83c574a3f8699177573da` | [Tag oficial](https://github.com/espressif/esp-idf/tree/9ef24e3e2a2c96e720d83c574a3f8699177573da). O objeto anotado da tag é `e1043bcf5fff341b0fc0f5442b6a8c89a5e40d99`. Fixar também todos os submódulos. |
| GCC IDF, Linux x86_64 | `esp-13.2.0_20250707`, GCC 13.2.0 | [tools.json da revisão IDF](https://github.com/espressif/esp-idf/blob/9ef24e3e2a2c96e720d83c574a3f8699177573da/tools/tools.json) registra a versão recomendada e SHA-256 do pacote. |
| Runtime Espressif | `espressif/wasm-micro-runtime` `2.4.0~1`, commit `8f806e0f2c02a768d2c35044f2964f769612d9bc` | [Registry oficial](https://components.espressif.com/components/espressif/wasm-micro-runtime/versions/2.4.0~1/readme). Hash do componente resolvido `04f25aad2896b5e906397a061d35cce560609bebd3913a4be7e365ecbf2dd9d6`, conferível por `validate_build_config.py --runtime-identity`. É candidato, não pin aprovado. |
| wamrc compatível com o candidato | WAMR 2.4.0, commit `47c7c85ac21f240a878026af603e032065d183fb` | [Tag upstream oficial](https://github.com/wasm-micro-runtime/wasm-micro-runtime/tree/47c7c85ac21f240a878026af603e032065d183fb). O registro Espressif exclui `wamr-compiler/`, portanto precisa de checkout separado. |
| LLVM Xtensa | Espressif `xtensa_release_18.1.2`, commit `344b928e67bfc1d07cb150609c55548e86a9bf19` | [Fonte oficial](https://github.com/espressif/llvm-project/tree/344b928e67bfc1d07cb150609c55548e86a9bf19). SHA consultado no ramo que o helper WAMR 2.4.0 seleciona. O pin é o SHA, nunca o HEAD mutável do ramo. |
| WASI-SDK | Proposta 27.0 Linux x86_64, tag `wasi-sdk-27`, commit `fbdec30656d4099da5c7fd7e8275e4af777ca97e` | [Release oficial](https://github.com/WebAssembly/wasi-sdk/releases/tag/wasi-sdk-27). Escolhido como pacote exato com digest oficial disponível. Compatibilidade com os flags e loader desta bancada ainda precisa do build. Não alegar que é a versão usada pelo CI WAMR 2.4.0. |

URLs de aquisição e digests publicados, ainda sem download local neste preflight:

* GCC: `https://github.com/espressif/crosstool-NG/releases/download/esp-13.2.0_20250707/xtensa-esp-elf-13.2.0_20250707-x86_64-linux-gnu.tar.xz`, 113781196 bytes, SHA-256 `d1f1378394baa3788bf78062e5d041cb14d4858bbb57f86d59e5b83fc9d5525f`.
* WASI-SDK: `https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-27/wasi-sdk-27.0-x86_64-linux.tar.gz`, 124790235 bytes, SHA-256 `b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2`.
* Compilador WAMR genérico oficial: `https://github.com/wasm-micro-runtime/wasm-micro-runtime/releases/download/WAMR-2.4.0/wamrc-2.4.0-x86_64-ubuntu-22.04.tar.gz`, SHA-256 `6c780a652e8afe06bb7c66f5af7356a97e086630d6befb6ce3de20b373ea9111`. Não usar esse pacote como compilador Xtensa. O [workflow da release](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/.github/workflows/release_process.yml) seleciona AArch64, ARM, Mips, RISCV e X86 para LLVM, sem Xtensa.

Não foi identificado um pacote precompilado oficial que forneça, com origem e
compatibilidade comprovadas, as bibliotecas necessárias de LLVM Xtensa 18.1.2
para este `wamrc`. Um pacote Espressif contendo clang/llc não demonstra que
contém as bibliotecas estáticas e `LLVMConfig.cmake` exigidas pelo compilador.

## Decisão do runtime e avisos de segurança

O [compare oficial](https://github.com/espressif/wasm-micro-runtime/compare/47c7c85ac21f240a878026af603e032065d183fb...8f806e0f2c02a768d2c35044f2964f769612d9bc)
mostra cinco commits após upstream 2.4.0. Os arquivos dos loaders e o wrapper
WASI não aparecem modificados. Isso exclui a hipótese de os patches posteriores
terem sido incorporados nesses arquivos pelo pacote Espressif candidato.

| Aviso | Escopo observado | Consequência para a decisão |
|---|---|---|
| [CVE-2025-64704](https://github.com/wasm-micro-runtime/wasm-micro-runtime/security/advisories/GHSA-2f2p-wf5w-82qr) | Classic, opcodes SIMD, correção publicada em 2.4.4 | A carga inteira preliminar não usa SIMD. Exigir macro efetivo e rejeição dos opcodes antes de dispensar o caminho na configuração final. |
| [CVE-2025-64713](https://github.com/wasm-micro-runtime/wasm-micro-runtime/security/advisories/GHSA-gvx3-gg3x-rjcx) | Validação fast; aviso cita 2.4.2, release 2.4.4 traz a correção | B exclui fast. A revisão deve conferir fonte e macros, sem inferir vulnerabilidade universal em 2.4.0 a partir do título. |
| [CVE-2026-54913](https://github.com/wasm-micro-runtime/wasm-micro-runtime/security/advisories/GHSA-frwm-5hpr-2hq2) | `load_init_expr`, classic/fast com ref types, correção em 2.4.5 | O arquivo local conserva `WASMValue cur_value` sem inicialização e o heap type sem validação. Ref types vem desligado no wrapper por padrão, mas o wamrc habilita ref types por padrão. Precisamos conferir ambos os builds; bounds e AOT validator não corrigem esse defeito do loader. |
| [CVE-2026-54914](https://github.com/wasm-micro-runtime/wasm-micro-runtime/security/advisories/GHSA-wj3p-xhqm-pffc) | `poll_oneoff`, correção em 2.4.5 | O wrapper local conserva a validação de apenas um elemento. `sdkconfig.interp` e `.aot` desativam libc WASI, reduzindo exposição no MCU, mas isso deve ser confirmado nos comandos efetivos. O compilador host tem configuração própria. |
| [CVE-2026-54912](https://github.com/wasm-micro-runtime/wasm-micro-runtime/security/advisories/GHSA-jjv3-x3jf-cg49) | Loader fast, correção em 2.4.5 | B exclui fast. Não dispensa a revisão do compilador host nem os outros avisos. |

Os avisos do catálogo ligados ao CLI de sockets, LLVM JIT e uvwasi têm caminhos
distintos. A configuração desta entrega não usa o CLI `iwasm`, JIT nem uvwasi;
registrar essa delimitação e conferir que não foram ligados. Não chamar o pin
seguro só por desativar um desses caminhos.

A página principal do registry Espressif ainda apontou `2.4.0~1` como latest na
consulta. Upstream 2.4.5, commit `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`,
é alternativa a investigar, com as correções citadas. Ela exigiria revisão
explícita do componente ESP-IDF, pins, scripts e gates hoje fixos em 2.4.0.
Não foi provada como substituto direto do pacote Espressif. A proposta é
fechar essa decisão antes de tratar a toolchain como congelada para campanha.
Uma prova E1 com payload próprio e origem controlada pode ser feita apenas com
o risco e os caminhos permitidos registrados, sem equivaler à aprovação da
campanha ou dos ensaios de módulos inválidos.

## Comandos Xtensa e contrato de otimização

O [guia do compilador](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/wamr-compiler/README.md)
usa o helper Xtensa; o [helper Python](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/build-scripts/build_llvm.py)
seleciona o fork e o ramo acima. O [CLI exato](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/wamr-compiler/main.c)
suporta estes probes futuros:

```sh
wamrc --version
wamrc --help
wamrc --target=help
wamrc --target=xtensa --cpu=help
wamrc --target=xtensa --cpu-features=+help
```

Nenhum desses probes foi executado porque o binário ainda não existe. A fonte
[Xtensa.td](https://github.com/espressif/llvm-project/blob/344b928e67bfc1d07cb150609c55548e86a9bf19/llvm/lib/Target/Xtensa/Xtensa.td)
declara CPU `esp32s3` e features windowed para ela; `generic` tem lista de features
vazia. Isso é pista para a revisão ABI, não autorização para mudar o comando
sem observar help, logs e código gerado do binário real. Preservar a CPU atual.

A geração futura usa `--target=xtensa --bounds-checks=1 --disable-llvm-lto -O3`.
Não desativar checks de pilha ou aux stack. Em 2.4.0, o CLI faz a configuração
de stack bounds seguir memory bounds quando não há override. Preservar no MCU
`WASM_ENABLE_AOT_VALIDATOR=1` e `WASM_CONFIGURABLE_BOUNDS_CHECKS=0`.

O preflight encontrou que o comando anterior não desativava LTO interno de
wamrc. O [pipeline oficial](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/core/iwasm/compilation/aot_llvm_extra.cpp)
aplica LTO em AOT por padrão. A correção autorizada acrescentou
`--disable-llvm-lto` ao gerador e o gate agora a exige. Dez testes do runner,
inclusive a rejeição de manifesto sem a flag, passaram sem skips. O parser
PowerShell e `git diff --check` passaram. Isso valida preparação e caminhos
negativos locais; não há execução real Wasm/AOT.

## Doador Linux proposto, sem aumentar o CT107

Usar a distro WSL2 Ubuntu já instalada no PC, em diretório Linux dentro da distro,
sem build no mount NTFS. Antes de qualquer escrita, inspecionar sua identidade,
versão, arquitetura, memória visível e espaço real. Não há necessidade comprovada
de iniciar CT109, mudar recursos de CT107 ou parar guests. Se WSL oferecer memória
ou disco insuficientes, parar antes do build e apresentar os números.

Como orçamento inicial de engenharia, reservar 8 GiB de RAM disponível no doador,
20 GiB de disco livre e apenas uma tarefa de compilação/link simultânea. Esses
valores são gates conservadores da proposta, não requisitos mínimos oficiais ou
garantia de sucesso. A [documentação LLVM](https://llvm.org/docs/GettingStarted.html)
recomenda Release, Ninja, restrição dos targets e `LLVM_PARALLEL_LINK_JOBS=1`
quando há pouca memória. O build por libs, sem clang e sem debug, reduz o volume.
O PC passa a amostra de RAM, mas o espaço dentro da distro permanece desconhecido.

Pacotes do doador a conferir antes de instalar: git, gcc/g++, cmake, ninja-build,
python3, zlib1g-dev, libzstd-dev e ferramentas tar/xz. Registrar versões exatas
e repositório Debian/Ubuntu resolvido; não copiar comandos Ubuntu 20.04 com
`libgcc-9-dev` cegamente para Debian 13. O LLVM alvo é 18.1.2, não o clang WASI.

Receita proposta para o compilador, somente após os gates. Ela não foi executada.
`TASK_ROOT` precisa ser um diretório dedicado dentro de HOME, com caminho real
conferido e sem arquivos de outra entrega. A receita recusa diretórios existentes,
inclusive links simbólicos, e não reutiliza clones ou caches. Uma repetição exige
outro diretório identificado por pins e flags; o anterior permanece preservado.

```sh
set -eu
TASK_ROOT="$HOME/wasmhome-toolchain-2.4.0-xtensa18"
WAMR_SHA=47c7c85ac21f240a878026af603e032065d183fb
LLVM_SHA=344b928e67bfc1d07cb150609c55548e86a9bf19
test "$(uname -m)" = x86_64
printf '%s\n' "$TASK_ROOT"
# Conferir caminho real, identidade e recursos antes do primeiro mkdir/clone.
test ! -e "$TASK_ROOT" && test ! -L "$TASK_ROOT"
mkdir "$TASK_ROOT"
cd "$TASK_ROOT"
git clone --no-checkout https://github.com/wasm-micro-runtime/wasm-micro-runtime.git wamr
git -C wamr checkout --detach "$WAMR_SHA"
test "$(git -C wamr remote get-url origin)" = https://github.com/wasm-micro-runtime/wasm-micro-runtime.git
test "$(git -C wamr rev-parse HEAD)" = "$WAMR_SHA"
git -C wamr diff --exit-code
git -C wamr diff --cached --exit-code
test -z "$(git -C wamr status --porcelain --untracked-files=all --ignored)"
git clone --no-checkout --depth 1 --branch xtensa_release_18.1.2 https://github.com/espressif/llvm-project.git llvm
git -C llvm fetch --depth 1 origin "$LLVM_SHA"
git -C llvm checkout --detach "$LLVM_SHA"
test "$(git -C llvm remote get-url origin)" = https://github.com/espressif/llvm-project.git
test "$(git -C llvm rev-parse HEAD)" = "$LLVM_SHA"
git -C llvm diff --exit-code
git -C llvm diff --cached --exit-code
test -z "$(git -C llvm status --porcelain --untracked-files=all --ignored)"
test ! -e llvm-build && test ! -L llvm-build
test ! -e wamrc-build && test ! -L wamrc-build
cmake -S llvm/llvm -B llvm-build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DLLVM_TARGETS_TO_BUILD=X86 -DLLVM_EXPERIMENTAL_TARGETS_TO_BUILD=Xtensa \
  -DLLVM_ENABLE_PROJECTS= -DLLVM_INCLUDE_TOOLS=OFF \
  -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF \
  -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_DOCS=OFF \
  -DLLVM_ENABLE_BINDINGS=OFF -DLLVM_ENABLE_LIBEDIT=OFF \
  -DLLVM_ENABLE_TERMINFO=OFF -DLLVM_BUILD_LLVM_DYLIB=OFF \
  -DLLVM_PARALLEL_COMPILE_JOBS=1 -DLLVM_PARALLEL_LINK_JOBS=1
cmake --build llvm-build --parallel 1
cmake -S wamr/wamr-compiler -B wamrc-build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DWAMR_BUILD_PLATFORM=linux -DWAMR_BUILD_TARGET=X86_64 \
  -DWAMR_BUILD_WITH_CUSTOM_LLVM=1 \
  -DLLVM_DIR="$TASK_ROOT/llvm-build/lib/cmake/llvm"
cmake --build wamrc-build --parallel 1
./wamrc-build/wamrc --version
./wamrc-build/wamrc --target=help
./wamrc-build/wamrc --target=xtensa --cpu=help
sha256sum wamrc-build/wamrc
ldd wamrc-build/wamrc
```

A variante usa o ponto de integração custom LLVM previsto no CMake oficial,
sem invocar pip --user nem o repackage destrutivo do helper. Não é build provado.
Congelar flags e cache antes de repetir. Não apagar cache automaticamente para
resolver divergência; criar outro diretório identificado por pins/flags.

O recibo do doador precisa guardar SHAs e origins, ausência de mudanças na fonte,
submódulos quando houver, versões do SO/GCC/CMake/Ninja, flags, CMakeCache e
compile_commands com hashes, logs de configuração/build, help dos targets/CPU,
SHA-256/tamanho do `wamrc` e bibliotecas dinâmicas requeridas. Guardar licenças.
Um SHA digitado pelo operador continua sem provar a origem do binário.

Transferir futuramente o binário, recibo e checksums para um staging dedicado no
CT107 usando a chave confiável. Antes do primeiro write remoto, reconfirmar
hostname/UID/IP/MAC/rootfs e identidade do diretório de destino. Conferir o hash
do recibo por canal local e do payload após a transferência. O CT é glibc 2.41;
um binário Ubuntu mais antigo pode funcionar, mas `ldd`, `--version`, targets e
uma compilação real no CT são obrigatórios. Não publicar binário que dependa de
libLLVM não transferida ou de bibliotecas com ABI não verificada. Essa transferência
mantém origem rastreável, sem tornar o compilador determinístico por alegação.

## Instalação e produção futura no CT107

1. Aceitar #6 pelo gate da entrega e fixar a revisão exata da árvore do projeto.
   Revalidar identidade e recursos do CT107 antes de instalar. Usar caminhos
   exclusivos para ferramentas e builds. Não editar DNS, firewall ou Proxmox.
2. Instalar dependências do sistema compatíveis com Debian 13 e registrar suas
   versões. Usar ESP-IDF 5.2.6 no SHA fixo, todos os submódulos e ferramentas
   selecionadas por `tools/tools.json`, verificando hashes publicados.
   O guia [Linux IDF 5.2.6](https://docs.espressif.com/projects/esp-idf/en/v5.2.6/esp32s3/get-started/linux-macos-setup.html)
   descreve `install.sh esp32s3` e `export.sh`. Executá-los em ambiente dedicado.
3. Verificar compatibilidade do Python 3.13.5 com as dependências IDF 5.2.6.
   Não foi comprovada. As constraints oficiais v5.2 incluem `cryptography<42`
   e são adquiridas por URL mutável. Guardar conteúdo/hash das constraints,
   freeze e hashes dos wheels. Se o ambiente falhar, propor um Python userspace
   de versão exata e origem oficial; não trocar IDF silenciosamente.
4. Adquirir WASI-SDK 27.0 pelo URL fixo, verificar SHA-256 antes de extrair,
   guardar pacote, hash e versões de clang/linker. Instalar o compilador doador
   somente após verificar recibo, ABI e hash no CT. Validar targets/CPU no CT.
5. Para executar o gerador PowerShell do repo em Linux, instalar `pwsh` com
   versão exata e pacote oficial de hash conferido. Essa versão ainda não foi
   selecionada. O script exige o comando `python`; fornecer um venv dedicado
   que o exponha. Confirmar resolução real dos executáveis. Uma implementação
   Linux equivalente seria mudança de fonte que exige testes e revisão próprios.
6. Em clone limpo da revisão aprovada, resolver runtime e conferir integralmente
   identidade/hash. Executar `build-artifacts.ps1` com `WASI_SDK_PATH`, `WAMRC`
   e SHA completo do checkout do compilador. O script escreve somente no
   diretório canônico `artifacts/`; não o executar nesta preparação.
7. Gerar A/B/C no LXC, serialmente, cada qual com build dir e SDKCONFIG próprios.
   Usar os arquivos rastreados `sdkconfig.defaults`, `sdkconfig.interp` e
   `sdkconfig.aot`, acrescentando benchmark ativo e ambos selftests desativados
   em defaults isolados da entrega. Clock 240 MHz. B classic exclusivo e C AOT
   exclusivo. Não importar credenciais da bancada para provar compilação.
8. Executar `validate_build_config.py` por cenário, guardar manifestos, hashes,
   símbolos, configuração efetiva por hash e assembly da carga nativa e AOT.
   Inspecionar corpo da transformação e ABI, sem reduzir a aceitação ao header
   do arquivo ou a um `loop` em assembly intermediário. AOT pode precisar de
   objeto adicional `--format=object` e objdump compatível do IDF, mantendo as
   mesmas flags. Não passar o arquivo `.aot` diretamente ao objdump como ELF.
9. Repetir a produção numa segunda árvore/build limpa com os mesmos pins.
   Comparar hashes dos payloads e explicar diferenças não determinísticas,
   caminhos ou datas. Registrar reprodutibilidade funcional e determinismo
   binário como propriedades separadas. Não alegar determinismo sem comparação.

Os builds podem usar Ninja `-j1`. Isso não garante que 1 GiB caiba na ligação.
Se houver OOM ou memória insuficiente, preservar erro/log e interromper antes
de alterar RAM/swap. Gerar nativo/Wasm/AOT apenas no doador não atende ao corpo
atual da #7; ele fornece o compilador, enquanto o LXC precisa gerar os artefatos.

## Critérios de aceite e bloqueios restantes

O aceite da #7 exige as versões instaladas e conferidas no CT, geração real
dos três artefatos da mesma revisão, configuração isolada e evidências de origem,
comandos e hashes. A instalação sozinha não encerra a entrega. A revisão adversarial
Luna xhigh deve comparar estes itens na árvore final. CI, publicação Git,
integração, aceite formal e execução da placa são marcos separados.

Bloqueios de execução imediata: aceite de #6 ainda pendente; decisão do pin
runtime ainda pendente; compilador Xtensa inexistente; doador Linux ainda sem
preflight interno; dependências do CT ausentes; Python/PowerShell Linux ainda
sem combinação fixa comprovada. São lacunas concretas de ambiente, não convite
para ampliar a infraestrutura. A própria prova de checksum B/C é entrega #9.

Continuam dentro do preparo autorizado do plano, depois dos gates e da revisão:
instalar as ferramentas em diretórios dedicados, construir no doador escolhido,
transferir um compilador verificado ao CT107, gerar e validar artefatos no LXC.
Alterar RAM/swap/disk do container, configurar o host, iniciar CT109, mudar
hardware/cenário ou publicar Git exige a autoridade correspondente. Não há
necessidade comprovada de nenhuma dessas mudanças para testar a rota doadora.

Nenhuma instalação, build de toolchain, transferência remota, alteração Proxmox,
flash, acesso COM ou publicação Git foi realizada neste preflight.

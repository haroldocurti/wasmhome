# Suplemento de decisões da toolchain #7

10/10/2026. Somente preparação. Complementa `toolchain-proposal.md` sem alterar
o documento em revisão nem fontes rastreadas. Este agente não instalou ferramentas,
iniciou WSL, compilou, acessou COM ou escreveu remotamente. O coordenador fez o
preflight WSL de leitura registrado abaixo. Os comandos são propostas futuras.

## Doador WSL reconfirmado pelo coordenador

O coordenador informou a inicialização normal da distro Ubuntu já existente,
apenas para preflight, sem instalar pacotes ou iniciar builds. O ambiente é
Ubuntu 24.04.1 LTS, x86_64, com 16 CPUs lógicas, 13053 MiB de memória disponível
e swap de 4096 MiB sem uso. Git, Python 3, tar e xz estão presentes; cmake,
ninja, gcc e g++ estão ausentes.

O `df` da raiz WSL informa aproximadamente 954 GiB virtualmente livres.
O volume hospedeiro C: tinha apenas 27,82 GiB físicos livres no preflight.
O orçamento efetivo é o espaço físico disponível no Windows, que limita o
crescimento do VHDX, não a capacidade virtual do filesystem. Reconfirmar C:
antes do build e durante os checkpoints; uma run que projete ultrapassar o
orçamento deve parar, sem expandir ou mover disco automaticamente. A memória
visível no doador permite investigar a rota de uma tarefa por vez, mas o
consumo real de LLVM continua sem medição. Os recibos desse preflight pertencem
ao coordenador; este suplemento registra a reconfirmação comunicada por ele.

## PowerShell Linux escolhido

Fixar PowerShell **7.6.6 LTS**, Linux x86_64, arquivo portátil:

* URL: `https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/powershell-7.6.6-linux-x64.tar.gz`
* SHA-256 oficial: `ddbc4a2d113bbd46d283cfedcbcd117a70caefd7673f41f2b4e0000badf103bc`
* Tamanho publicado: 75876271 bytes.
* Recibo oficial: `https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/hashes.sha256`, SHA-256 `388b105a0f329ef7ce9739166ca8e86d3fee6d31698137099e0558c9eb63c9f5`.

A [release oficial](https://github.com/PowerShell/PowerShell/releases/tag/v7.6.6)
foi publicada em 08/09/2026. Conferi o digest no API e o mesmo valor no arquivo
`hashes.sha256`; esse recibo está codificado em UTF-16LE, com BOM. O pacote de
75,9 MB não foi adquirido ou executado. A documentação Microsoft
[lista Debian 13 como suportado](https://learn.microsoft.com/en-us/powershell/scripting/install/install-debian?view=powershell-7.6)
e descreve o [método por arquivo portátil](https://learn.microsoft.com/en-us/powershell/scripting/install/alternate-install-methods?view=powershell-7.6).

Instalar futuramente em diretório userspace exclusivo, com checksum antes da
extração. Usar o caminho absoluto do `pwsh`, `-NoProfile -NonInteractive`; não
alterar `/usr/bin` ou repositórios apt para selecionar versão implicitamente.
Dependências nativas de .NET precisam existir no CT e `ldd` deve ficar sem
`not found`. A versão da engine precisa retornar exatamente 7.6.6. Antes do
gerador, testar resolução de caminhos Linux, `ConvertFrom-Json -AsHashtable`,
`Get-FileHash` e o comando `python` do venv dedicado. A disponibilidade dessas
APIs no produto não comprova que o script do projeto já executou em Linux.

## Python 3.13.5 e ESP-IDF 5.2.6

Não encontrei proibição geral de Python 3.13.5 no build core. O
[verificador exato do IDF](https://github.com/espressif/esp-idf/blob/9ef24e3e2a2c96e720d83c574a3f8699177573da/tools/python_version_checker.py)
aceita versões >=3.8, sem teto. O arquivo
[requirements.gdbgui.txt](https://github.com/espressif/esp-idf/blob/9ef24e3e2a2c96e720d83c574a3f8699177573da/tools/requirements/requirements.gdbgui.txt)
exclui Python >=3.13 expressamente, mas GDBGUI é opcional e desnecessário à #7.
Não usar a documentação do instalador IDF atual como prova específica de 5.2.6.

Congelei somente em arquivo ignorado a consulta atual das
[constraints oficiais v5.2](https://dl.espressif.com/dl/esp-idf/espidf.constraints.v5.2.txt):
`espidf.constraints.v5.2.preflight.txt`, 3356 bytes, SHA-256
`082c1e98d4f950e853dc9c773dfcd21bcfe8a95cbe8cd1b0412e25150d421069`.
A consulta ao servidor trouxe `idf-component-manager<=2.5.2`, enquanto uma
versão previamente indexada pelo navegador mostrava `<3`. A URL é mutável;
o recibo precisa identificar os bytes realmente usados no ambiente futuro.

Pontos relevantes da resolução:

* `cryptography>=2.1.4,<42` exige wheel binário. A versão 41.0.7 publica wheel
  `cp37-abi3-manylinux_2_28_x86_64`, aceito pela tag ABI estável em CPython 3.13
  normal sobre glibc 2.41. O metadata declara Python >=3.7. Classifiers antigos
  não listam 3.13; isso não equivale a um limite `Requires-Python`.
  [Metadata da distribuição](https://pypi.org/pypi/cryptography/41.0.7/json).
* `esptool~=4.8` permite 4.x até antes de 5.0, não fixa 4.8.0. A distribuição
  4.12.0 declara Python >=3.7 e lista 3.13 nos classifiers. Ela publica somente
  sdist, de hash `d797f6529d582173508baf0572542ddadf53aae8c92c84ea9f420f242464103c`.
  [Metadata oficial do projeto](https://pypi.org/pypi/esptool/4.12.0/json).

Fiz um dry-run local do pip 26.2.1 com alvo CPython 3.13/Linux, sem instalar
pacotes, com `--ignore-installed`, `--no-cache-dir` e `--only-binary=:all:`.
Ele aceitou o wheel ABI3 de cryptography e parou em esptool, pois a restrição
artificial de somente wheels exclui o sdist 4.x. Não é prova de incompatibilidade
de Python 3.13. O procedimento também roda no host Windows, cujo contexto de
markers não prova o contexto Linux. Não há resultado completo de resolução,
imports ou `idf.py build` no CT. Não confundir esse dry-run com instalação.

Decisão: testar primeiro Python 3.13.5 do CT em venv exclusivo, apenas os
requirements core, com as constraints congeladas. Fazer o resolver real Linux
permitir sdist onde o fornecedor publica apenas fonte, sem relaxar a exigência
binária própria de cryptography/tree-sitter. Guardar relatório de resolução,
freeze, hashes de wheels/sdists e de eventuais wheels produzidos. Depois conferir
`pip check`, imports dos pacotes core, `idf.py --version`, reconfigure e build
nativo limpo. Só esses passos poderão aceitar Python 3.13.5 para esta entrega.
GDBGUI não entra na instalação. Não alterar Python do sistema.

### Alternativa userspace exata

Se a resolução ou execução core falhar por Python 3.13, usar CPython **3.11.17**
da PSF, sem trocar as constraints/IDF para ocultar a falha:

* [Release oficial](https://www.python.org/downloads/release/python-31117/), publicada em 01/10/2026.
* URL: `https://www.python.org/ftp/python/3.11.17/Python-3.11.17.tar.xz`.
* SHA-256 publicado: `bfb74ad39efae27cda510f134ab408e00f9992c56851cfc0b1cdb5646da11599`.
* Recibo Sigstore: `https://www.python.org/ftp/python/3.11.17/Python-3.11.17.tar.xz.sigstore`.

A série 3.11 está em manutenção de segurança, com fontes oficiais e sem
instaladores binários novos. Não chamar um binário de terceiro de oficial PSF.
Para evitar transferir extensões com ABI de outra distro, construir futuramente
no próprio CT, em diretório novo, com uma tarefa e sem PGO/LTO. GCC e headers
de OpenSSL, zlib, bz2, lzma, libffi, sqlite e readline são dependências a fixar.
Instalar com `make altinstall` num prefixo dedicado sob HOME, mantendo o Python
3.13 do Debian. Não há prova de consumo máximo dessa compilação com 1 GiB;
preservar e parar no erro/OOM em vez de aumentar recursos automaticamente.

```sh
# Após verificar hash, recursos, destino e diretórios novos.
./configure --prefix="$HOME/.local/toolchains/cpython-3.11.17" \
  --with-ensurepip=install
make -j1
make altinstall
"$HOME/.local/toolchains/cpython-3.11.17/bin/python3.11" --version
"$HOME/.local/toolchains/cpython-3.11.17/bin/python3.11" -m venv "$HOME/.local/toolchains/idf526-py31117"
```

Verificar `ssl`, `_ctypes`, `sqlite3`, `zlib`, `bz2`, `lzma` e `venv`, além do
mesmo gate core/IDF acima. Registrar logs, versões de bibliotecas e checksums.
Não transformar a existência dessa alternativa em alegação de falha de 3.13.

## Combinação runtime/compilador recomendada para a próxima decisão

Recomendo investigar e validar **runtime upstream 2.4.5 + wamrc 2.4.5**, ambos
no commit `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, com o port ESP-IDF explícito
abaixo, IDF 5.2.6 e LLVM Xtensa Espressif 18.1.2 no SHA
`344b928e67bfc1d07cb150609c55548e86a9bf19`. A recomendação não muda o pin atual
e não declara o novo runtime como drop-in ou já aprovado.

Há quebra objetiva de formato AOT. O
[compare oficial 2.4.0→2.4.5](https://github.com/wasm-micro-runtime/wasm-micro-runtime/compare/47c7c85ac21f240a878026af603e032065d183fb...25bd7eb63e828e4bd242cc9b38d260b4b31c6605)
mostra `AOT_CURRENT_VERSION` passando de 4 para 5; `aot_compatible_version`
passa de aceitar 4/3 a aceitar apenas a versão corrente. Portanto, **não instalar
wamrc 2.4.5 para alimentar o runtime Espressif 2.4.0~1**. Gerar todos os AOT
novamente com o compilador correspondente após qualquer migração.

As correções 2.4.4/2.4.5 cobrem os avisos discutidos na proposta. Ainda exigir
bounds ativos, AOT validator e configuração exclusiva dos cenários. Validador
e checks não substituem os patches de loader/WASI.

### O que difere do port Espressif atual

Comparei as fontes upstream 2.4.0/2.4.5 e os cinco commits do fork Espressif
até `8f806e0f2c02a768d2c35044f2964f769612d9bc`:

| Delta do fork | Situação em upstream 2.4.5 | Decisão proposta |
|---|---|---|
| Stack default ESP-IDF de 6 para 8 KiB, commit `0c60ceb9ff0e7af136b724d4e8a54443ccc3a3a8` | Upstream permanece em 6 KiB | Transplantar esse ajuste explícito preservando os defaults atuais. Nunca substituir `core/config.h` inteiro pela versão 2.4.0, pois isso reverte o formato AOT e pode reverter outros guards. |
| `esp_pthread` com `stack_alloc_caps=MALLOC_CAP_8BIT|MALLOC_CAP_SPIRAM` sob `CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM`, no mesmo commit | Ausente, arquivo upstream idêntico ao 2.4.0 | Transplantar e conferir compatibilidade com headers IDF 5.2.6 e configuração efetiva; não ativar EXT_MEM/PSRAM silenciosamente. |
| Buffered printf, commit `8f806e0f2c02a768d2c35044f2964f769612d9bc` | Ausente em `platform_internal.h` | Preservar o delta do port no pacote de migração. B/C desativam libc builtin, mas o caminho precisa permanecer identificado. |
| App framework/submódulo e wrapper CMake/Kconfig, commits `46774bc53bd83a67c29343154f84d4514e16c452` e `bbb1405e492424c3ac4e975c3fa1596270f9655f` | Wrapper upstream permanece igual ao 2.4.0, sem esses acréscimos | Transplantar os acréscimos do port que preservam a configuração atual, com submódulo fixo. Não introduzir uso funcional novo de app framework na bancada. |
| CI/publicação/metadata de registro | Identificam versão 2.4.0 do pacote original | Não reutilizar uma identidade de registry 2.4.0~1 para conteúdo 2.4.5. Registrar componente privado/vendorizado com versão, upstream, patches e hash próprios. |

O app framework do fork é
`https://github.com/bytecodealliance/wamr-app-framework.git`, commit
`e97860611e371d91d2ef25101b02b29719476a48`, conferido pelo registro do submódulo.
Não obter o HEAD do framework. A integração não pode apresentar o novo pacote
como release publicada pela Espressif.

Comparei bytes de seis arquivos oficiais entre upstream 2.4.0 e 2.4.5. Todos
são iguais: wrapper CMake/Kconfig ESP-IDF, `espidf_thread.c`,
`platform_internal.h`, `invokeNative_xtensa.s` e `aot_reloc_xtensa.c`.
Os hashes 2.4.5 dos dois caminhos de ABI Xtensa são:

* `invokeNative_xtensa.s`: `0d0a058e5b13db7b52b3eb5c3ec6cc614129960f2651697e3118c65bea329434`.
* `aot_reloc_xtensa.c`: `d7a0c90dd597eebce8824c041413735fe2a78da45fe6192a2546a2b7cf870504`.

Isso mostra que os mecanismos de chamada nativa e relocação Xtensa examinados
não mudaram. Não prova compatibilidade de payload AOT antigo, nem execução no
ESP32-S3. O [helper 2.4.5](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/25bd7eb63e828e4bd242cc9b38d260b4b31c6605/build-scripts/build_llvm.py)
continua escolhendo `xtensa_release_18.1.2`. Preservar o SHA LLVM fixo.

Antes de adotar a migração, gerar patchset pequeno e auditável sobre upstream
2.4.5, fixar seu commit/hash, atualizar explicitamente identificação do runtime,
manifestos e gates hoje hardcoded em 2.4.0~1/2.4.0, e revisar os testes negativos.
Executar a matriz E1 inteira novamente. CPU continua sem override até help do
binário real, inspeção windowed ABI e checksum no mesmo ESP32-S3.

## Guards corrigidos na receita futura

Os guards `git diff` anteriores deixam passar arquivos untracked. Antes de
configurar qualquer compilação, exigir árvore fonte limpa inclusive untracked,
origin exato, SHA completo e submódulos fixos. Builds e recibos ficam fora da
árvore de fontes. Fontes ignoradas também podem contaminar CMake; um dry-run de
`git clean -ndx` ajuda a recusá-las, sem remover arquivo algum.

```sh
test "$(git -C "$SOURCE" remote get-url origin)" = "$EXPECTED_ORIGIN"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$EXPECTED_SHA"
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=all)"
test -z "$(git -C "$SOURCE" clean -ndx)"
git -C "$SOURCE" submodule status --recursive
# Conferir cada SHA do submódulo com o recibo e recusar prefixos -, + ou U.
```

Não reutilizar `llvm-build` ou `wamrc-build` existentes. Para esta primeira
execução, criar um diretório de run novo sob a raiz dedicada, já conferida por
caminho real/ownership, e usar `-B` em dois filhos inexistentes. Falhar antes de
qualquer reconfigure se um destino já existir. Repetir a receita cria outra run
e preserva resultados anteriores; nunca apaga ou reconfigura cache desconhecido.

```sh
# TASK_ROOT já precisa ter identidade, caminho real, recursos e ownership conferidos.
RUN=$(mktemp -d "$TASK_ROOT/run.XXXXXXXX")
LLVM_BUILD="$RUN/llvm-build"
WAMRC_BUILD="$RUN/wamrc-build"
test ! -e "$LLVM_BUILD"
test ! -e "$WAMRC_BUILD"
# Usar esses dois caminhos nos cmake -B da receita e guardar o comando inteiro.
```

Uma retomada futura só pode reutilizar run se houver recibo anterior íntegro
que prove SHAs, origins, fonte limpa, flags completas e seus hashes, CMakeCache
e compile_commands, compiladores e versões, `CMAKE_HOME_DIRECTORY`,
`CMAKE_BUILD_TYPE`, generator, targets LLVM X86/Xtensa, ausência de projetos
extras, LLVM_DIR do wamrc e identidade da biblioteca LLVM. Ausência de um desses
itens exige run nova. Verificar apenas que existe `libLLVMCore.a` é insuficiente.

## Gate da próxima execução

PowerShell tem versão/URL/digest fechados. Python 3.13.5 tem caminho core
permitido pelo código oficial e gate real ainda pendente, com fallback exato
3.11.17. A opção de runtime que incorpora correções é 2.4.5 junto com seu
compilador, mantendo o delta de port Espressif e regenerando todos os AOT.
Seu pacote/patchset ainda precisa de implementação e revisão antes de adoção.

O próximo trabalho já autorizado de preparo pode validar essas instalações
userspace e a rota doadora depois dos gates da #6 e da decisão explícita do pin.
Este suplemento não autoriza alteração de Proxmox, RAM/swap, CT109 ou Git
externo. Nenhuma dessas mudanças é necessária só para conferir os pins aqui.

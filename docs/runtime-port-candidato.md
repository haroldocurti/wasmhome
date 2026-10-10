# Port candidato WAMR 2.4.5 para E1

Preparação local em 10/10/2026, no worktree `codex/issue-7-runtime-port`.
Adoção, instalação da toolchain, origem do binário `wamrc`, builds ESP-IDF B/C e
execução física continuam pendentes. Este pacote não encerra #7 nem #9.

## Identidade e escopo

O pacote se identifica como `wasmhome/wasm-micro-runtime`, versão
`2.4.5-wasmhome.1`. Não é uma release do registro Espressif.
[lock.json](../runtime-port/lock.json) fixa origem, SHA completo, patch,
resultados dos cinco arquivos alterados, metadados e digest do pacote.
As fontes completas ficam na pasta ignorada `build-runtime-port/`; o Git do
projeto guarda somente o delta e os verificadores.

| Origem | Revisão fixada |
|---|---|
| [WAMR upstream 2.4.5](https://github.com/bytecodealliance/wasm-micro-runtime/tree/25bd7eb63e828e4bd242cc9b38d260b4b31c6605) | `25bd7eb63e828e4bd242cc9b38d260b4b31c6605` |
| [Port Espressif examinado](https://github.com/espressif/wasm-micro-runtime/tree/8f806e0f2c02a768d2c35044f2964f769612d9bc) | `8f806e0f2c02a768d2c35044f2964f769612d9bc` |
| [App framework](https://github.com/bytecodealliance/wamr-app-framework/tree/e97860611e371d91d2ef25101b02b29719476a48) | `e97860611e371d91d2ef25101b02b29719476a48` |

Digest próprio do pacote: `1f5af199cf16d7774c29e35a7ce471998f01dcc6451a2508901fbaa090ea775c`.
É SHA-256 da concatenação, em ordem lexical, de cada caminho relativo seguido
do SHA-256 textual de seus bytes. Exclui o recibo gerado, inclui metadados e
framework. Esse digest é do port materializado; não é um digest oficial de tar
ou do registro Espressif.

[esp-idf.patch](../runtime-port/esp-idf.patch) preserva o delta do fork sobre
upstream 2.4.0, aplicado e verificado sobre 2.4.5:

- Stack default de 8 KiB no grupo de plataformas embarcadas do fork.
- `esp_pthread` e caps SPIRAM somente sob `CONFIG_FREERTOS_TASK_CREATE_ALLOW_EXT_MEM`.
- `printf` builtin com buffer de 128 bytes.
- Wrapper ESP-IDF e Kconfig do framework, incluindo seu default `y` e stack
  máxima de 131072 bytes. Não presumir que esse default foi medido no firmware.

O entry point ESP-IDF já existe no CMake upstream 2.4.5. Não foi duplicado.
Nenhum outro arquivo do core foi substituído pelo fork antigo. Isso conserva
as correções da release 2.4.5, inclusive os loaders e WASI descritos em
[proposta-toolchain-decisoes.md](proposta-toolchain-decisoes.md). A compatibilidade
do framework antigo com essas APIs permanece um gate de compilação e execução.

## Reprodução da preparação

Requer Git e Python 3.10+ no computador da revisão. Não instala pacotes:

```powershell
python tools/runtime_package.py --acquire
python tools/runtime_package.py
python tools/validate_build_config.py --runtime-identity
python -m unittest discover -s tests -p test_runtime_package.py -v
python -m unittest discover -s tests -p test_e1_runner.py -v
git diff --check
```

A primeira chamada baixa os dois repositórios oficiais, confere origin e HEAD
e exige `status --porcelain --untracked-files=all` e `git clean -ndx` vazios.
Exporta objetos Git com bytes LF, aplica o patch em staging novo e confere o
digest fixado antes de publicar o diretório local. Chamadas posteriores apenas
verificam; não sobrescrevem fonte modificada nem staging incompleto.

O verificador compara cada arquivo upstream ao blob Git do SHA fixado,
confere os hashes dos cinco arquivos do patch, rejeita arquivos extras,
ausentes e symlinks, e reconstrói a identidade independentemente do recibo.
Somente `core/version.h`, regenerado pelo CMake upstream, permite normalizar
CRLF em LF. O único symlink upstream periférico é materializado como seu
conteúdo de blob, de modo igual no Windows e Linux; não afeta o core.
O cache Git também faz parte do material necessário à revisão offline.

## Gates da geração futura

AOT passou de formato 4 para 5. O manifesto agora é
`wasmhome-e1-artifacts-v2`; declara formato 5 e o gate também lê os oito bytes
do cabeçalho AOT. Todo payload antigo precisa ser regenerado. `wamrc` deve
reportar `wamrc 2.4.5` e o SHA declarado deve ser o upstream fixado acima.
A declaração permanece marcada como origem de binário não comprovada.

O compilador poderá usar a árvore materializada em
`build-runtime-port/component/wasm-micro-runtime/wamr-compiler`. Usar somente
um diretório de build novo, fora dessa árvore, e LLVM Espressif
`344b928e67bfc1d07cb150609c55548e86a9bf19` conforme a receita doadora existente.
Não reutilizar CMakeCache desconhecido. Não existe binário LLVM/Xtensa
compatível comprovado nesta entrega, e nenhuma compilação LLVM foi feita.

Após construir e verificar a origem do binário real, gerar com:

```powershell
# WASI_SDK_PATH e WAMRC apontam para ferramentas já verificadas.
./tools/build-artifacts.ps1 -WamrcSourceRevision 25bd7eb63e828e4bd242cc9b38d260b4b31c6605
```

As flags continuam `--target=xtensa --bounds-checks=1 --disable-llvm-lto -O3`.
CPU continua sem override até `--cpu=help` do binário real e inspeção da ABI.
O projeto configura o componente local apenas em B/C; A não adquire nem liga
esse runtime. B/C mantêm validator 1, bounds configuráveis 0 e gates de
macros, fontes compiladas e símbolos no ELF.

Antes de adotar: revisão adversarial do patch e dos gates; compilação ESP-IDF
5.2.6 dos dois modos com configs exclusivas; inspeção Xtensa; prova de origem
do wamrc/LLVM; checksum no mesmo ESP32-S3; matriz E1 inteira novamente.
Os registros físicos anteriores permanecem históricos e não provam este port.

## Verificação local desta entrega

A aquisição real e sua repetição verificaram o digest acima. Os 12 testes
diretamente afetados passaram: três do pacote e nove de E1, incluindo runner
C compilado com MSVC e gate CMake. A suíte geral teve 49 casos: 45 passaram e quatro foram pulados
(testes do codec C), porque a biblioteca não foi fornecida nesta sessão.
Esses skips não são prova do codec. Não houve geração Wasm/AOT, build ESP-IDF,
flash, instalação remota ou alteração dos CT107/CT109, Proxmox, SDKs ou WSL.

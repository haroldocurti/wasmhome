Atualização de 14/10/2026: o candidato atual é `2.4.5-wasmhome.3`.
A revisão `.2` corrigiu os aliases DROM/IROM e sincronizou cache após cópia e
relocação, mas o ensaio físico ainda falhou em AOT com `LoadProhibited`.
O objeto Xtensa desse build omite a gravação do imediato em relocação por
violação de aliasing estrito no helper original. A revisão `.3` usa operações
alinhadas de 32 bits com máscara e deslocamento, preservando bytes vizinhos,
e mantém as proteções e a sincronização do SDK. O helper passou em testes de
alinhamento, endianidade e otimização e em compilação isolada com o GCC Xtensa
real. Ainda exige novos builds A/B/C e repetição na placa; a #9 permanece aberta.
[Diagnóstico e validações](evidence/aot-alias-fix-2026-10-14/README.md).
A reprodução do compilador reutiliza o LLVM original verificado, sem exigir
uma nova compilação de LLVM. Os registros abaixo preservam o histórico `.1`.

# Port candidato WAMR 2.4.5 para E1

Sincronização em 13/10/2026, baseada nas evidências de 12/10: o
[registro de retomada pelo Linux](retomada-linux.md) supera os estados históricos
abaixo. SDKs/venv, LLVM/wamrc R5 compatível com o CT107, geração e repetição
Wasm/AOT e builds de firmware A/B/C foram executados. Consulte os
[recibos E1](evidence/retomada-linux-2026-10-12/e1-scenarios/README.md).
Permanecem pendentes testes locais da revisão recente, revisão/aceite da #7,
atestado independente do compilador e execução A/B/C no ESP32-S3 (#9).
Nenhum firmware foi gravado na retomada de 12/10; compilação não comprova execução.


Estado histórico da preparação local em 10/10/2026, no worktree
`codex/issue-7-runtime-port`: adoção, instalação da toolchain, origem do binário
`wamrc`, builds ESP-IDF B/C e execução física ainda estavam pendentes naquela
data. O avanço posterior está resumido acima; este pacote não encerra #7 nem #9.

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

Digest próprio do pacote: `7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`.
É SHA-256 da concatenação, em ordem lexical, de cada caminho relativo seguido
do SHA-256 textual de seus bytes. Exclui o recibo gerado, inclui metadados e
framework. Esse digest é do port materializado; não é um digest oficial de tar
ou do registro Espressif.

[esp-idf.patch](../runtime-port/esp-idf.patch) contém o delta selecionado na
comparação revisada do fork sobre upstream 2.4.0, aplicado e verificado sobre
2.4.5. A referência do fork é uma trilha de revisão; o acquirer verifica os
objetos upstream/framework e os hashes do delta, sem baixar ou autenticar o
checkout do fork. `port_delta_reviewed_reference` declara esse limite. A
supressão global `-Wno-type-limits` foi removida até haver diagnóstico real
que justifique uma exceção específica. O delta mantido é:

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

Requer Git e Python 3.11+ no computador da revisão. Não instala pacotes:

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

AOT passou de formato 4 para 5. O manifesto é `wasmhome-e1-artifacts-v3`;
declara formato 5 e o gate também lê os oito bytes do cabeçalho AOT. Todo
payload antigo precisa ser regenerado. Lock e recibos JSON recusam chaves
duplicadas, inclusive em objetos aninhados; versão/AOT são confrontados com
os macros do core 2.4.5 e os metadados fixam ESP-IDF `==5.2.6`.

Antes de criar staging, a geração exige:

- WasmHome em revisão Git limpa, inclusive untracked; registra commit, tree
  e hashes dos geradores. A revisão permanece igual até o fim da geração.
- Checkout ESP-IDF oficial, limpo, SHA `9ef24e3e2a2c96e720d83c574a3f8699177573da`,
  tag `v5.2.6`, com submódulos inicializados no pin e limpos.
- Tar oficial WASI-SDK 27.0 Linux x86_64, SHA-256
  `b7d4d944c88503e4f21d84af07ac293e3440b1b6210bfd7fe78e0afd92c23bc2`.
  O SDK extraído é comparado aos arquivos e symlinks desse tar. Não basta
  uma string de versão do clang ou um caminho fornecido pelo operador.
- `wamrc 2.4.5` com binário cujo hash corresponde a recibo completo
  `wasmhome-wamrc-build-v1`. O recibo vincula a identidade do port verificado,
  LLVM Espressif `344b928e67bfc1d07cb150609c55548e86a9bf19`, caches e snapshots
  compile_commands, comandos configure/build, ferramentas host e hashes das
  bibliotecas estáticas listadas no build.ninja. X86/Xtensa, Release, Ninja,
  custom LLVM e diretórios de fonte/build precisam concordar.

Não existe esse recibo real nem binário LLVM/Xtensa verificado nesta entrega.
Sua ausência bloqueia geração antes de escrever. Para o futuro doador, usar
`build-runtime-port/component/wasm-micro-runtime/wamr-compiler`, diretórios
novos externos à fonte, `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON` nas duas
configurações e caminhos absolutos para o executável CMake, `-S`, `-B` e
`--build`. Antes de configurar, registrar que os diretórios não existiam;
preservar os quatro argv completos executados em um JSON de comandos:

```json
{
  "fresh_build_directories": true,
  "commands": {
    "llvm_configure": ["/caminho/absoluto/cmake", "-S", "/fonte/llvm/llvm", "-B", "/run/llvm-build", "...todos os argumentos realmente executados..."],
    "llvm_build": ["/caminho/absoluto/cmake", "--build", "/run/llvm-build", "--parallel", "1"],
    "wamrc_configure": ["/caminho/absoluto/cmake", "-S", "/projeto/build-runtime-port/component/wasm-micro-runtime/wamr-compiler", "-B", "/run/wamrc-build", "...todos os argumentos realmente executados..."],
    "wamrc_build": ["/caminho/absoluto/cmake", "--build", "/run/wamrc-build", "--parallel", "1"]
  }
}
```

O exemplo é um contrato de registro, não um recibo executado. As flags LLVM
continuam na [receita doadora](proposta-toolchain.md), com as mudanças acima.
Após o build real, o registrador confere arquivos locais e escreve recibo novo:

```sh
python3 tools/record_wamrc_build.py --llvm-source /fonte/llvm \
  --llvm-build /run/llvm-build --wamrc-build /run/wamrc-build \
  --binary /run/wamrc-build/wamrc --commands /run/comandos.json \
  --output /run/wamrc-build-receipt.json
```

Esse recibo é **sem assinatura**. O consumidor verifica seu conteúdo e hash
contra o binário transferido, e verifica localmente os fontes materializados,
IDF e WASI. Ele não recompila LLVM/wamrc nem autentica o relato do operador
sobre a criação de diretórios novos. A associação do binário ao build depende
da confiança no doador/recibo; atestação ou reprodução independente continua
pendente. Os snapshots de bibliotecas não substituem inspeção final da ABI.

Depois de obter as provas reais, gerar em PowerShell Linux:

```powershell
$env:WASI_SDK_PATH = '/tools/wasi-sdk-27.0-x86_64-linux'
$env:WAMRC = '/tools/wamrc'
$env:IDF_PATH = '/tools/esp-idf'
./tools/build-artifacts.ps1 -WasiSdkPackage /receipts/wasi-sdk-27.0-x86_64-linux.tar.gz `
  -WamrcBuildReceipt /receipts/wamrc-build-receipt.json
```

A geração recusa symlink/junction/reparse no destino e staging. Gera e valida
um conjunto inteiro novo; só então renomeia o anterior para backup e o novo
para `artifacts/`. São duas renomeações individuais, com pequeno intervalo
sem destino canônico; não é um exchange atômico único. Falha da segunda troca
restaura o anterior, e o journal permite recuperação explícita após interrupção:

```powershell
./tools/build-artifacts.ps1 -RecoverPublication
```

Staging com falha e backup anterior são preservados para inspeção. A recuperação
valida identidade e caminhos; não apaga árvores. Se a interrupção ocorreu antes
de escrever o journal, inspecionar o lock vazio antes de qualquer recuperação.
Não começar outra geração com transação pendente nem leitores concorrentes.

As flags AOT são `--target=xtensa --bounds-checks=1 --disable-llvm-lto --opt-level=3`.
CPU continua sem override até `--cpu=help` do binário real e inspeção da ABI.
O projeto configura o componente local apenas em B/C; A não adquire nem liga
esse runtime. B/C mantêm validator 1, bounds configuráveis 0 e gates de
macros, fontes compiladas e símbolos no ELF.

Antes de adotar: revisão adversarial do patch e dos gates; compilação ESP-IDF
5.2.6 dos dois modos com configs exclusivas; inspeção Xtensa; prova de origem
do wamrc/LLVM; checksum no mesmo ESP32-S3; matriz E1 inteira novamente.
Os registros físicos anteriores permanecem históricos e não provam este port.

## Verificação local desta entrega

A aquisição real e sua repetição verificaram o digest acima. Os testes cobrem
JSON ambíguo, metadados/core divergentes, fontes sujas/untracked, ausência de
prova obrigatória, pins e caches divergentes, falha real de processo gerador,
rejeição de junction, conjunto inválido, troca e recuperação da publicação.
`tools/test-platform.ps1` compilou o codec com MSVC e executou 60 testes,
todos aprovados, sem skips. O recibo ignorado de revisão registra o log e
a revisão congelada; esses testes não constroem LLVM/wamrc nem ESP-IDF B/C.
Não houve geração Wasm/AOT real, build ESP-IDF, flash, instalação remota ou
alteração dos CT107/CT109, Proxmox, SDKs ou WSL.

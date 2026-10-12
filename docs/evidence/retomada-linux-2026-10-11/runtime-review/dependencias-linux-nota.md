# Dependências Linux: decisão de preparo em 10/10

Pode omitir `build-essential`, `libffi-dev` e `libssl-dev` da primeira rota
CT107: compiladores ESP-IDF/WASI-SDK já pré-compilados, wamrc vindo do doador
e extensões Python somente por wheels. Isso é uma redução condicionada aos
gates abaixo, não a declaração de suporte testado do ESP-IDF em Python 3.13.

O [toolchain ESP32-S3 do SHA IDF fixado](https://github.com/espressif/esp-idf/blob/9ef24e3e2a2c96e720d83c574a3f8699177573da/tools/cmake/toolchain-esp32s3.cmake)
usa os compiladores Xtensa; o [Kconfig](https://github.com/espressif/esp-idf/blob/9ef24e3e2a2c96e720d83c574a3f8699177573da/tools/cmake/kconfig.cmake)
executa o gerador Python. Não identifiquei nesses caminhos uma exigência de
GCC host ou dos headers OpenSSL/libffi para gerar firmware com o toolchain
oficial. A [lista abrangente oficial](https://docs.espressif.com/projects/esp-idf/en/v5.2.6/esp32s3/get-started/linux-macos-setup.html)
ainda inclui esses headers; esta rota reduzida precisa de prova real.

A [documentação cryptography 41.0.7](https://cryptography.io/en/41.0.7/installation/)
explica que wheels manylinux dispensam compilador e embutem OpenSSL. Headers,
GCC/Rust e Python-dev seriam necessários ao build fonte, que as constraints
IDF congeladas já proíbem para cryptography. CFFI fonte também exigiria
libffi-dev; seu wheel deve entrar no fechamento verificado. `esptool` 4.12.0
é exceção de fonte legítima: [pyproject](https://github.com/espressif/esptool/blob/v4.12.0/pyproject.toml)
e [setup.py](https://github.com/espressif/esptool/blob/v4.12.0/setup.py) usam
setuptools e não definem extensão C. Seu sdist não justifica instalar GCC.

## Plano mínimo revisável, ainda não executado

1. Reconfirmar CT107 (`wasmhome-bench`, UID1000, IP138/MAC fixada), filesystem
   e recursos. Guardar `dpkg-query` dos pacotes atuais e a simulação APT nova.
2. Preservar a decisão rastreada **“Não alterar Python do sistema”**. A
   simulação anterior de 28 novos/seis upgrades incluía revisão Debian do
   Python e XZ/liblzma; portanto não cabe na decisão vigente. Não executá-la
   sem mudança explícita dessa decisão e revisão do delta completo.
   O novo preflight read-only do root mostrou venv stdlib presente, sem
   ensurepip/pip, e imports ssl/ctypes/lzma funcionando. A simulação em
   `build-session-e0/issue7-no-python-upgrade-simulation.txt` do checkout
   principal mostrou **21 novos, zero upgrades, zero removes** para:

   ```sh
   apt-get install --no-install-recommends git wget flex bison gperf \
     cmake ninja-build ccache libusb-1.0-0 libunwind8
   ```

   `xz-utils 5.8.1-1` já está instalado; não solicitar python3-pip,
   python3-venv nem xz-utils. Reconfirmar a simulação imediatamente antes
   da instalação e bloquear qualquer upgrade/remove não revisado.
3. Rota candidata sem upgrade do Python: criar diretório userspace novo
   com `python3 -m venv --without-pip /caminho/venv`. Bootstrap PyPA somente
   dentro desse venv, com versão, URL e hash fechados e dependências transitivas
   preservadas. Essa etapa ainda requer verificação do bootstrap concreto;
   não baixar `get-pip.py` mutável nem executar pip global. Resolver somente
   requirements core com os bytes congelados das constraints v5.2. Começar
   com `--only-binary=:all: --no-binary=esptool`, `--dry-run --report` e
   `--ignore-installed`. Permitir outro sdist só depois de conferir que é
   Python puro e fixar origem/hash; não instalar headers automaticamente.
4. Guardar fechamento de versões, hashes e wheels produzidos pelo esptool;
   exigir que as extensões sejam wheels compatíveis Linux x86_64/CPython3.13.
   Executar `pip check`, imports core (inclusive cryptography/cffi/tree-sitter,
   Kconfig, esptool e component manager), stdlib ssl/ctypes/lzma, `idf.py
   --version`, reconfigure e build nativo em diretório novo. Conferir deps
   dinâmicas dos pré-compilados e parar se faltar biblioteca real. Isso é o
   gate de Python3.13; seleção de tag ABI3 sozinha não prova imports/build.

Se Python3.13 falhar por incompatibilidade, o fallback fonte CPython3.11.17
userspace já fixado **não cabe na lista de 21 novos sem upgrades**. Compilar no CT exige
GCC/make e headers de OpenSSL, zlib, bz2, lzma, libffi e módulos adicionais
selecionados. Preparar outra simulação com versões e delta completos, sem
trocar `/usr/bin/python3`, sem ampliar RAM/swap. Não usar a simulação ampla
76 novos/14 upgrades automaticamente. Compilar LLVM/wamrc no CT também não
faz parte desta rota reduzida: permanece na receita doadora e precisa de
recibo verificável do binário transferido.

Nenhum APT, pacote, CT, host ou WSL foi alterado nesta análise. O bootstrap userspace e a compatibilidade
real do fechamento Python3.13 e o build IDF no CT ainda não foram demonstrados.

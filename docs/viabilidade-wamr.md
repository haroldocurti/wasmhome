# Preparação da viabilidade WAMR

Status em 09/10/2026: preparação de E1, sem prova de execução de B/C na placa.
As issues #7 e #9 dependem do aceite de #6. Os testes abaixo não encerram essa
dependência nem constituem dados da campanha.

## Versão candidata e fontes

O manifesto do snapshot de 09/10 fixava `espressif/wasm-micro-runtime` em `2.4.0~1`. O
[registro oficial da Espressif](https://components.espressif.com/components/espressif/wasm-micro-runtime/versions/2.4.0~1/readme)
lista ESP32-S3 e Xtensa. O componente local foi resolvido com hash
`04f25aad2896b5e906397a061d35cce560609bebd3913a4be7e365ecbf2dd9d6` e ESP-IDF
5.2.6. Isso comprova resolução, não execução AOT nem aprovação do pin.

As [releases oficiais do WAMR](https://github.com/wasm-micro-runtime/wasm-micro-runtime/releases)
publicam correções posteriores ao 2.4.0. A 2.4.4 registra CVE-2025-64704 no
interpretador clássico envolvendo SIMD e CVE-2025-64713 na validação para fast.
A 2.4.5 registra correções em `load_init_expr`, fast e WASI. Ainda falta comparar
os caminhos afetados com o componente Espressif e as opções efetivas. Não
considerar o pin aprovado para a campanha ou para os ensaios de módulos inválidos.
Mudança de versão requer repetir a matriz dos três modos, preservar as evidências
e registrar a decisão. Não houve alteração silenciosa do pin.

O [guia upstream atual de aplicações](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/main/doc/build_wasm_app.md)
recomenda versões exatamente iguais de `wamrc` e runtime. A revisão
[WAMR-2.4.0 do compilador](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/WAMR-2.4.0/wamr-compiler/README.md)
indica `build_llvm_xtensa.sh` para construir o backend Xtensa. Um binário para
Windows/x86-64 não comprova esse backend. O comando usa `--target=xtensa` e
`--bounds-checks=1`, presentes no
[CLI oficial 2.4.0](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/WAMR-2.4.0/wamr-compiler/main.c).
Não há pressuposto de suporte a `--cpu=esp32s3`.

### Candidato implementado em 10/10

A #6 foi encerrada após integração e revisão. O worktree de preparo de #7
agora contém o [port candidato 2.4.5](runtime-port-candidato.md), identidade
`wasmhome/wasm-micro-runtime` `2.4.5-wasmhome.1`, upstream
`25bd7eb63e828e4bd242cc9b38d260b4b31c6605` e framework fixo. O core completo
2.4.5 é preservado, com cinco arquivos alterados pelo patch Espressif.
O gate exige `wamrc 2.4.5`, o mesmo SHA declarado, manifesto v2 e AOT v5.
A fonte foi adquirida e verificada localmente; compilador binário, builds B/C,
ABI física e aceite do pin continuam pendentes. Os testes e registros de
09/10 abaixo permanecem evidência da árvore anterior.

## Correções preparatórias

O [contrato do loader](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/WAMR-2.4.0/core/iwasm/include/wasm_export.h)
exige buffer gravável, retido até `wasm_runtime_unload`, e alinhamento de quatro
bytes para AOT. `main/main.c` copia o array constante para `os_malloc`, cujo port
ESP-IDF local alinha em oito bytes. Libera ambiente, instância, módulo e buffer,
nessa ordem, inclusive após falha no aquecimento. Cada chamada zera o checksum
antes de executar, para não reutilizar o resultado anterior em um trap.

Os guards de compilação recusam B com fast/AOT e C com interpretador.
A validação independente também compara os macros reais usados para compilar
WAMR, incluindo `WASM_ENABLE_AOT_VALIDATOR=1` e
`WASM_CONFIGURABLE_BOUNDS_CHECKS=0`. O CMake raiz fixa essas opções antes de
avaliar os componentes, pois o wrapper ESP-IDF não as expõe no sdkconfig.
Em C, o validador também exige `aot_validator.c` nas fontes e
`aot_module_validate` no ELF. O modo A exige ausência de símbolos de execução WAMR no ELF, mesmo se o
gerenciador resolveu e compilou o componente. A carga continua sendo o
microbenchmark preliminar de 2.000 iterações com checksum `0xDBB2F0D4`.

`heap_before` do CSV permanece a observação após inicializar o runtime.
O log final adicional registra heap interno após iniciar a plataforma e antes de iniciar o runtime
e após liberar o runner/runtime. Não mede separadamente o custo da plataforma,
PSRAM, memória estática nem o pico durante atualização. Os 30 registros são
chamadas na mesma instância, com uma carga/instanciação compartilhada.

## Ferramentas e evidências

`tools/build-artifacts.ps1` exige WASI-SDK, `WAMRC` e o SHA completo da origem
declarada do compilador. Esse SHA é informado pelo operador e precisa ser
conferido com o checkout da ferramenta e a evidência de compilação do binário.
O manifesto identifica essa declaração como
`operator_declaration_not_binary_origin_proof`. O validador recusa ausência de
SHA completo, versões divergentes e hashes dos executáveis diferentes; isso
comprova a identidade das ferramentas disponíveis, sem provar como foram
compiladas. O script registra versões, hashes dos compiladores, fonte/header,
payloads, arrays e flags em `build-manifest.json`, no diretório canônico
`artifacts/`. Não há opção de saída para outro diretório.
Gera em staging e publica o manifesto por último. Uma falha após verificar as ferramentas invalida o manifesto
anterior, ainda que payloads antigos permaneçam no diretório.
O CMake de B/C executa a validação de integridade antes de compilar e acompanha
manifesto, payloads, arrays e fonte em suas dependências de reconfiguração.
Arrays antigos sem manifesto, ou hashes divergentes, bloqueiam a compilação.
A verificação CMake não exige os executáveis originais dos compiladores;
o validador do build exige seus hashes/versões. O modo A não exige artefatos
Wasm/AOT nem WASI-SDK/wamrc.

O script preserva assembly Wasm e recusa ausência de `loop`. Esse exame é
preliminar; permanece obrigatória a inspeção do código final nativo e AOT para
comprovar que a transformação não foi eliminada. O manifesto marca explicitamente
`generated_not_device_validated` e mantém essa inspeção e execução na placa como
pendências. Não compilar B/C com artefatos sem manifesto validado.

A ausência de `-flto` no WASI clang não desativa o LTO interno de `wamrc`.
O [CLI oficial 2.4.0](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/wamr-compiler/main.c)
expõe `--disable-llvm-lto`, e a
[implementação do pipeline](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/47c7c85ac21f240a878026af603e032065d183fb/core/iwasm/compilation/aot_llvm_extra.cpp)
aplica passes LTO em AOT por padrão. O gerador agora usa a flag explícita junto
de `--target=xtensa`, `--bounds-checks=1` e `-O3`; o validador recusa manifestos
sem essa flag. Não há artefato AOT real gerado que comprove essas opções na placa.
A CPU permanece sem override até consultar o help do compilador efetivamente
construído e verificar sua compatibilidade.

Exemplo de geração após preparar e verificar o compilador compatível:

```powershell
.\tools\build-artifacts.ps1 -WamrcSourceRevision <SHA-de-40-caracteres>
```

Cada build deve usar diretório e `SDKCONFIG` próprios, com defaults comuns e o
arquivo de modo correspondente. Ativar `CONFIG_WASMHOME_RUN_BENCHMARK=y` somente
no firmware de E1, com selftests desativados. O benchmark vem desativado por
padrão; o firmware de diagnóstico anterior não satisfaz E1.

```powershell
python tools/validate_build_config.py --build-dir build-e1-native --scenario native --nm <caminho-do-xtensa-esp32s3-elf-nm.exe> --output build-e1-native/e1-build.json
python tools/validate_build_config.py --build-dir build-e1-interp --scenario interp --nm <caminho-do-xtensa-esp32s3-elf-nm.exe> --output build-e1-interp/e1-build.json
python tools/validate_build_config.py --build-dir build-e1-aot --scenario aot --nm <caminho-do-xtensa-esp32s3-elf-nm.exe> --output build-e1-aot/e1-build.json
```

O validador verifica configuração efetiva, clock de 240 MHz, otimização,
modo no CMake/compilação, hashes, correspondência array/payload e símbolos do ELF.
Compara a versão, hash do pacote e revisão do componente resolvido com o
manifesto. Confere `.component_hash`, `dependencies.lock`, todos os arquivos de
`CHECKSUMS.json` e reconstrói o hash do pacote com o algoritmo do gerenciador
Espressif. Só permite normalização CRLF para LF em `core/version.h`, regenerado
por `configure_file`; o conteúdo canônico ainda precisa corresponder ao hash
publicado. As fontes dos comandos WAMR devem pertencer ao componente conferido.
O relatório B/C inclui o SHA-256 do manifesto exato de artefatos.
O relatório contém hashes da configuração, sem copiar SSID, senha ou demais
valores locais. Preservar o sdkconfig completo apenas no pacote privado.

## Verificação local e pendências

`python -m unittest discover -s tests -p test_e1_runner.py -v` passou dez
testes sem skips. O runner real foi compilado no host com MSVC e APIs simuladas,
cobrindo buffer mutável/alinhado, falhas de alocação/carga/instanciação/ambiente/
export, trap no aquecimento, checksum incorreto, falha de inicialização e trap
em chamada medida. Verificou liberação dos recursos e checksum inválido zerado.
Essas simulações não comprovam o loader real nem memória executável no ESP32-S3.
Os testes adicionais recusam AOT validator desativado, bounds configuráveis
habilitados, fonte compilada fora do componente resolvido, runtime/lock/hash
divergentes, identidade de ferramenta alterada e payloads antigos sem manifesto
no gate CMake. O embedder também recusa palavras reservadas C, como `while`.

A sintaxe PowerShell do gerador passou pelo parser. O validador recusou o build
`build-native-validated` anterior porque o benchmark estava desativado.
WASI-SDK e `wamrc` não foram encontrados na busca local das ferramentas.
A geração real Wasm/AOT e os builds/execuções B/C continuam pendentes, assim
como a toolchain reproduzível no LXC, revisão do pin e aceite formal do servidor
#6. A prova preparatória local de A está registrada abaixo.

O build local `build-e1-native` passou com ESP-IDF 5.2.6, Xtensa GCC
13.2.0, configuração isolada, benchmark ativo, 240 MHz e otimização `-O3` na
unidade `workload/benchmark.c`. O CMake aplica `-O3` depois do default `-O2` do
ESP-IDF nessa unidade. O checker exige que a última flag de otimização seja
`-O3`, e também exige `-O3` efetivo nas flags WASI clang e AOT do manifesto.
Testes negativos recusam ausência, `-O2`, override posterior e response files
cuja otimização não foi verificada. Os demais componentes mantêm seus flags
registrados na configuração efetiva.
O validador independente aceitou o ELF e confirmou ausência de símbolos de
execução WAMR. O componente foi resolvido e compilado pelo gerenciador, mas a
execução WAMR não foi ligada em A. `benchmark.disassembly.txt` preserva a função
`benchmark_run`: o assembly contém `movi a10, 0x7d0`, `loop`, multiplicações,
XORs e rotações no corpo. A carga nativa não virou retorno de constante nesse
build. Em 09/10, esse firmware executou no ESP32-S3 uma sessão com cinco
aquecimentos conferidos pelo runner e 30 chamadas medidas, todas com checksum
`0xDBB2F0D4` e status `ok`. O [registro físico](evidence/execucao-2026-10-09/native/README.md)
preserva log serial, hashes, configuração efetiva identificada por hash,
assembly e verificação offline. Essa é prova local de cálculo em A; o firmware
não obteve IP, registrou quatro falhas de conexão Wi-Fi e não produziu UDP.
Não comprova os serviços, a toolchain da #7, B/C ou uma campanha comparativa.
O firmware anterior foi restaurado e verificado após a captura.

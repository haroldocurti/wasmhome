# Revisão independente — validação local de 13/10/2026

Revisei a entrega de A em `/tmp/wasmhome-luna-validacao-local-20261013.md` e os arquivos brutos no diretório homônimo. A revisão ficou restrita à recontagem do log, conferência dos comandos declarados, hashes e interpretação dos skips/falha. Não reexecutei a suíte e não alterei fontes, testes nem evidências.

## Resultado

A contagem relatada confere com o log: `Ran 60 tests`; 56 passaram, 1 falhou e 3 foram ignorados. A soma fecha em 60. Os quatro casos de `EmbeddedWireTests` aparecem com `ok`, e o hash recalculado de `main/observation_wire.c`, `main/observation_wire.h` e `tests/test_embedded_wire.py` coincide com o registro de A. A biblioteca usada é um ELF x86-64 real, ligada à libc local, com SHA-256 `667985b4aee880ea755bc4abcd263646c44bcf0b6b4765ba935889b0ea6893b9`; o log contém os quatro testes C executados contra a implementação compilada. Isso sustenta a conclusão limitada de que o codec C e os testes Python executáveis neste Linux passaram.

O único teste vermelho é `test_powershell_frontend_requires_origin_proof_before_writes`. A falha ocorre na asserção que exige localizar `pwsh` ou `powershell`; ambos estão ausentes neste host. O teste não chega a iniciar `tools/build-artifacts.ps1`. Portanto a execução não identificou um defeito do script ou na preservação dos artefatos. Ainda assim, a descoberta geral terminou em `FAILED`, então não se deve registrá-la como suíte verde ou aceite de #7.

Os três skips também correspondem ao ambiente observado: junções Windows; CMake em um caminho Windows fixo (`C:\esp\tools-task8\tools\cmake\3.30.2\bin\cmake.exe`); e o teste do runner compilado, que exige o `vcvars64.bat` de uma instalação específica do MSVC. Instalar CMake genérico no Linux não satisfaria o caminho que esse teste consulta. O host não oferece evidência dessas três integrações.

O texto `Unknown option: --target=wasm32-wasi` no meio do log é saída esperada do caso negativo `test_actual_compiler_process_failure_preserves_previous`: o teste inicia um processo inválido e passa ao comprovar erro e recuperação/preservação. O próprio caso termina em `ok`; não é uma segunda falha de compilação da biblioteca C.

## Conferência dos comandos e qualidade do registro

O relatório registra estes comandos, coerentes com o artefato e o log:

```sh
cc -std=c11 -Wall -Wextra -Werror -fPIC -shared main/observation_wire.c -o /tmp/wasmhome-luna-validacao-local-20261013/libobservation_wire.so
WASMHOME_WIRE_LIBRARY=/tmp/wasmhome-luna-validacao-local-20261013/libobservation_wire.so python -m unittest discover -s tests -v
```

A descoberta é diretamente verificável por `unittest-discover.log`. O binário, `file`, `ldd`, hashes e quatro testes do codec também dão evidência convergente de que a biblioteca C foi compilada e carregada. Porém `compile.log` tem zero bytes e `environment.log` não preserva a linha de compilação nem seu código de saída. Assim, o sucesso da compilação está bem apoiado pelo artefato executável e seu uso nos testes, mas a linha e o exit code 0 são registrados no relatório de A, não em uma transcrição bruta independente. Em uma repetição, capturar stdout/stderr e o código de saída de cada comando em um único transcript imutável.

O ambiente anota HEAD `9771e30a3edb667303f441076df77571f616ba18`, Python 3.14.7, GCC 16.2.1, status Git final vazio e hashes que conferi contra a árvore. A suíte inclui teste TCP em loopback (`127.0.0.1`); não há evidência de acesso a um endpoint externo ou ao hardware. A ferramenta `tools/test-platform.ps1` foi revisada, mas esta evidência não diz que ela foi executada.

## Encaminhamento

A frente satisfaz a verificação local limitada do codec C: aprovação independente. Para fechar a lacuna da falha, disponibilizar PowerShell Core e repetir a descoberta completa, mantendo a biblioteca existente e preservando a saída e os códigos de retorno. Para encerrar os skips, executar os três casos no Windows/MSVC/CMake exigidos pelo próprio teste ou registrar formalmente por que cada um permanece sem prova. Depois, ligar o resultado à revisão final exata de #7. Esta evidência, mesmo após resolver a falha ambiental, não comprova build ESP-IDF, atestado independente de `wamrc`, execução WAMR/AOT no ESP32-S3 nem critérios físicos de #9.

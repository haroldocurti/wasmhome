# Validação local e revalidação em 10/10/2026

O Python 3.11.2 do ESP-IDF executou 63 testes sem skips em 09/10. A revalidação
de 10/10, após os ajustes de revisão, executou 64 testes sem skips:

| Comando | Testes | Resultado |
|---|---:|---|
| `tools/test-platform.ps1` | 45 | Passou; codec C e runner de falhas compilados com MSVC. |
| `test_capture_lxc_probe.py` | 9 | Passou; mantém empate de timestamps inconclusivo. |
| `test_verify_lxc_pair.py` | 6 | Passou; recusa nonce divergente, sequência UDP inconsistente, múltiplos pedidos e a captura com empate de timestamps. |
| `test_verify_e1_native.py` | 4 | Passou; recusa vazio, checksum errado, repetição, ordem/boot e status divergentes; falha preserva os derivados anteriores. |

Os três últimos scripts ficam nesta pasta. A suíte principal está em `tests/`.
O log local da suíte principal é `build-session-e0/final-tests.log`, ignorado no
Git. Os verificadores offline de transporte e cálculo foram executados também
contra os registros físicos preservados e passaram. Para repetir a verificação
de transporte, escolher saída nova; o backup privado do app é necessário para
conferir a restauração. Para repetir a verificação nativa, conservar o binário
local com o hash aprovado no inventário.

A conferência nativa padrão é somente leitura. A geração de derivados exige
`--derive-output` com pasta nova e acontece após validar os insumos. O gate de
artefatos AOT agora também exige `--disable-llvm-lto` explícito; o teste negativo
recusa sua ausência. Não houve geração nem execução AOT nesta revalidação.

O build nativo e o build diagnóstico de rede passaram com ESP-IDF 5.2.6. As
evidências de build e de execução na placa estão nos registros próprios, com
seus limites. `git -c core.autocrlf=false diff --check` passou após normalização
dos documentos alterados para LF.

Não há workflow de CI configurado no checkout. Testes locais e execução física
não constituem CI, publicação, integração na `main` ou aceite de issue.

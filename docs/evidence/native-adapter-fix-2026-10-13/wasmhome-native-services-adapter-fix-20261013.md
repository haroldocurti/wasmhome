# Correção da integração do adaptador nativo ESP — 13/10/2026

A compilação `services-v1` no IDF revelou que `receive_request` chamava `wh_read_be` sem o parâmetro de tamanho ao interpretar `target_version`. Corrigi apenas essa chamada para ler os dois bytes big-endian do campo `target_version`; `target_sequence` e demais regras permanecem inalterados.

## Alterações

- Base de trabalho: commit `91f11f715ea7dc2c3a670e55d7f31c7356fdc0f4`.
- `main/native_services.c`: hash antes `5877179b15c186d32f610410b72de72a62cb1926f0ee18599a3780ee51546ca4`; hash depois `f22926936454dc5c11f0541346318d2ec96a39cd4ac698a203bed9c5c9a8d3d9`.
- Novo `tests/test_native_services_adapter_compile.py`: hash `ecb43e4b6e8028a0ed8ca7fd7b7e40450c7ff8af8bff80015e4d8654b51b8103`.
- A adição compila e liga o ramo ESP completo de `native_services.c` com `ESP_PLATFORM`, `CONFIG_WASMHOME_NATIVE_SERVICES` e stubs explícitos de FreeRTOS/IDF. O harness registra o callback instalado pelo adaptador e envia datagramas UDP-v1 reais pelo codec C: versão-alvo 2 deve resultar em rejeição BUSY deste build sem OTA; `0x0102` deve resultar em `UNSUPPORTED_VERSION`. O segundo vetor garante que o adaptador consome os dois bytes do campo uint16. Assim o teste exercita o decode do campo e a resposta do adapter, além de detectar erros de compilação que a suíte anterior, compilando apenas o core host, não alcançava.

## Verificação

- Teste focal do adapter: 1 passou; compilação C estrita com `-Wall -Wextra -Werror -pedantic` e execução das duas versões-alvo.
- Core de serviços: 7 passaram.
- Suíte completa com o codec C, PowerShell, CMake e Ninja em `PATH` explícito da execução: `Ran 82 tests in 2.134s`, OK; um skip esperado, o teste de junction específico de Windows. Os quatro testes do codec C foram executados depois de compilar `main/observation_wire.c` como biblioteca compartilhada temporária.
- Preservei a tentativa inicial sem o diretório portátil de PowerShell no `PATH`, que falhou somente no teste que exige PowerShell, e a repetição intermediária que ainda pulava o gate CMake. A execução final com os diretórios locais de `pwsh` 7.6.6, CMake e Ninja no `PATH` foi aprovada. Nenhuma instalação global ou mudança do PATH do usuário ocorreu.
- `git diff --check`: sem erros. Apenas os dois arquivos listados acima aparecem modificados/não rastreados na cópia compartilhada.

Logs preservados em `/tmp/wasmhome-native-services-adapter-fix-20261013/`: `adapter-compile-focal.log`, `native-core-focal.log`, a primeira tentativa `unittest-full.log`, repetição `unittest-full-pwsh.log`, execução intermediária `unittest-full-codec-pwsh.log`, execução final `unittest-full-all-tools.log`, e `libobservation_wire.so` temporária (SHA-256 `667985b4aee880ea755bc4abcd263646c44bcf0b6b4765ba935889b0ea6893b9`). O log original do build IDF que revelou a falha não foi sobrescrito nem alterado.

Esta verificação host não substitui a recompilação IDF real do firmware corrigido. Nenhuma porta serial foi aberta, nenhum pacote UDP foi enviado à placa e nenhum firmware foi gravado.

# Correção do adapter nativo — 13/10/2026

O build ESP-IDF real da fonte `91f11f7` rejeitou a leitura de `target_version`
porque `wh_read_be` foi chamado sem o comprimento. A correção passa `2`, conforme
o campo uint16 do protocolo. Um teste novo compila e executa o adapter inteiro
pelo ramo ESP_PLATFORM com stubs do SDK, incluindo versões 2, 7 e 0x0102.

O teste focal passou e a suíte com codec C real, PowerShell, CMake e Ninja teve
82 casos: 81 aprovados e uma exclusão específica de junction Windows. A revisão
independente reexecutou o focal e aprovou a correção. Falhas intermediárias e
execuções com ferramentas ausentes estão preservadas; o log final é
`unittest-full-all-tools.log`. Build ESP-IDF corrigido e validação física ainda
precisam ocorrer. Logs de firmware/configurações com credenciais ficam privados.

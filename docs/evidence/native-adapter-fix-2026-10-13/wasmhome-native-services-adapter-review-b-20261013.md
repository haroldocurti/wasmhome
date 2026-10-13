# Revisión B independiente del fix del adaptador nativo — 13/10/2026

Revisé el delta final preparado sobre la base `91f11f715ea7dc2c3a670e55d7f31c7356fdc0f4`. La corrección productiva añade el tamaño `2` a `wh_read_be(data + WH_HEADER_SIZE + 10, 2)`, que decodifica el campo `target_version` de 16 bits en orden de red. La declaración de la función exige el argumento de longitud y el codec valida el mismo campo con una lectura de dos bytes. El delta de producción queda limitado a esta llamada.

El nuevo test compila y enlaza el branch completo de `native_services.c` con `ESP_PLATFORM`, stubs explícitos de FreeRTOS/ESP-IDF y el codec C real. Envía mensajes construidos por `wh_wire_encode`: target version `2` debe ser rechazado como `BUSY` por tratarse de este adaptador sin OTA; `0x0102` debe producir `UNSUPPORTED_VERSION`, ejercitando el byte alto y bajo del campo. Compilé con `-Wall -Wextra -Werror -pedantic` y ejecuté el binario del harness.

Verificação independente: `python3 -B tests/test_native_services_adapter_compile.py -v` passou (1 teste). O relatório de A registra 82 testes na suíte completa: 81 aprovados e 1 skip específico do Windows; não repeti a suíte completa nesta revisão.

Hashes revisados:

- `main/native_services.c`: `f22926936454dc5c11f0541346318d2ec96a39cd4ac698a203bed9c5c9a8d3d9`
- `tests/test_native_services_adapter_compile.py`: `ecb43e4b6e8028a0ed8ca7fd7b7e40450c7ff8af8bff80015e4d8654b51b8103`
- Relatório de A: `d5b4396350bf436ffd88151a883b4c7ba84c74163c9b84b7a3d6e8dba13e4142`

Parecer: correção mínima consistente com o contrato do codec, e o teste final cobre compilação do adaptador e rejeição do valor cujo byte alto é não nulo. Não encontrei bloqueio neste delta. Esta revisão host não substitui build IDF completo nem validação em dispositivo; não alterei arquivos do repositório, não usei serial/UDP e não fiz flash.

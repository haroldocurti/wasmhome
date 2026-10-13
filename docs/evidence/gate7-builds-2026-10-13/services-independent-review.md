# Revisão independente — builds nativos de serviços #11

**Resultado:** os dois builds de preparação estão coerentes com a configuração allowlist pedida para a variante nativa e podem avançar para a etapa física. Este review não valida boot, rede, GPIO, leitura do DS18B20 nem resultado do serviço na placa; os próprios recibos mantêm `services_build_checked_not_device_validated`.

## Identidade e configuração

Os builds v1/v2 usam source commit `7b00b1585e1b27819979a6d82dd4144dc6f19682`, árvore `aec4c9e07d909f42adfa20009c8018775e74e297`; o status Git rastreado/não rastreado está limpo e `services-7b00.bundle` tem `refs/heads/main` nesse commit. Ambos registram ESP-IDF 5.2.6, commit `9ef24e3e2a2c96e720d83c574a3f8699177573da`, alvo `esp32s3`.

Nos dois recibos, a projeção efetiva confirma `WASMHOME_NATIVE_SERVICES=true`, benchmark e os dois selftests/diagnósticos desativados, clock 240 MHz e os pinos esperados: DS18B20/sensor GPIO4, saída de controle GPIO5 e entrada loopback GPIO6. A versão de controle é 1 no build v1 e 2 no v2. Os arquivos `sdkconfig` completos e `sdkconfig.json` estão no CT em mode `0600`; consultei somente os campos allowlist e hashes abaixo, sem ler nem publicar valores de rede.

| Build | Controle | `sdkconfig` SHA-256 | `sdkconfig.json` SHA-256 | `compile_commands.json` SHA-256 | Recibo SHA-256 |
|---|---:|---|---|---|---|
| v1 | 1 | `b2087e278ef75ad79d2a18ae4b68e4985de00750dd13f82ba7b5fae0ef36d6e1` | `94633f51f904e07b3761ca36ac9beedb78d223316273c30f37c860da0eb55150` | `1767b15cc79afc2f92a19f8b3a8b7606106dda63db58d83065a9fdf4d644774f` | `650c57379f71b5639f235db4adbb2501d400a1b746367ba9f6ad137e2b076c7a` |
| v2 | 2 | `52f1979a460049d002fa32b5a0153381bdc19801919a7c180deef228cf65dc8c` | `bb5a508cbdcdd73fc53ab28a375f5be59419e057b426dd39040d43a6e036dab0` | `85dcc620861a6c6d847ed03a07d16929d0ba44d0779fd951425d1484538ba7e8` | `6d4ce7972bc266a2ceabfc2b37cfbb6b3a7feb3355282680040ef8bea8ee0d99` |

Both receipts say `wamr_runtime_source_compiled=false` and list the expected WAMR execution symbols as absent. They say the same native service sources were compiled: `main.c`, `native_services.c`, `platform.c`, `sensor.c`, and `observation_wire.c`; the compile databases independently include the OneWire RMT API/implementation/CRC/device sources.

## SDK, DS18B20 and GPIO source path

`main/idf_component.yml` pins `espressif/onewire_bus` 1.0.2. The resolved `dependencies.lock` records component hash `c4c781940d8bd988432b47ac3ee68479c69e14e01fdfba0b61365b233a3ba5d6`; its component metadata records commit `e84bd3e48864b5fa1402244f095d68fa61d71fa5`. Both build databases contain `onewire_bus_api.c`, `onewire_bus_impl_rmt.c`, `onewire_crc.c`, and `onewire_device.c`.

The pinned source configures OneWire on `CONFIG_WASMHOME_SENSOR_GPIO`, invokes the RMT backend, reads the DS18B20 scratchpad, and checks CRC, configured 10-bit resolution and temperature range. The platform source configures GPIO5 as output, initializes it low, configures GPIO6 as input and reads the loopback after setting the output. The service control code chooses the version-specific threshold from the control version. These are source/build findings; they do not substitute for checking the actual sensor and wiring during the physical run.

## Firmware association and limits

The CT receipts and private transfer manifest point to matching firmware hashes. I independently hashed the local transferred copies; all four hashes match, and each file is mode `0600`:

| Build | ELF SHA-256 | BIN SHA-256 | BIN bytes |
|---|---|---|---:|
| v1 | `28a4ea9f1a1f5de4eb6d732c39cdfbf3f786a4216865e4d1825bc04aa765445e` | `fb0d55f9b319bf91137b34467b4342f21fb37e049db32a662721e6391b02a4f8` | 757,152 |
| v2 | `1f9216151996d71cc7736174642d19c2b2037085c5bf4d3f788e7d671c0f0f03` | `2cba645ebfd3b6fc8c2997a20b569b7c681f81f3c6a3d579faecd5c840511ee7` | 757,152 |

Each BIN fits the confirmed `0x700000` application slot. The sanitized build summary is `/tmp/wasmhome-e1-build-package-20261013-r2/services-builds.json`; its package index has a separate checksum. No serial port, flash command, device reset, UDP stimulus or firmware execution was used for this review.

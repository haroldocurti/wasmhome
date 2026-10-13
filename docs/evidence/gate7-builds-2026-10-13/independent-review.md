# Parecer independente — gate #7, builds E1 A/B/C

**Resultado:** a evidência de compilação e configuração dos três cenários é suficiente para o gate #7. A inspeção encontrou builds separados, pins consistentes, artefatos e firmware associados por hashes, e disassembly do corpo do workload nativo e AOT. O checksum na placa continua separado para o gate físico #9; nenhum firmware foi executado neste review.

## Escopo e método

Fiz revisão somente leitura do CT107 por `pct exec` e das seis cópias de firmware transferidas para a área privada local. Não abri serial, não alterei o guest, os builds, arquivos do repo ou firmware e não consultei valores de configuração que possam conter credenciais. Reexecutei `tools/validate_build_config.py` nos três diretórios, sem `--output`, e obtive exit 0 para native, interp e AOT. Os três validadores retornaram `build_checked_not_device_validated`, como esperado antes do ensaio físico.

A projeção sanitizada de configuração contém apenas campos permitidos; os arquivos completos `sdkconfig` e `config/sdkconfig.json` permanecem privados, mode `0600`. Nenhum SSID, senha ou endereço do observador foi incluído. Logs e recibos integrais também permanecem no CT. A evidência local desta revisão está em `/tmp/wasmhome-gate7-review-r1-20261013/`; não contém ELF/BIN nem configuração integral.

## Proveniência e toolchain

O checkout usado nos builds tem commit `91f11f715ea7dc2c3a670e55d7f31c7356fdc0f4`, árvore `86afd5816215e07f71d04eee0781631eb9dc6cd1` e status Git sem mudanças rastreadas ou não rastreadas. O bundle de origem conserva `refs/heads/main` no mesmo commit. Confirmei também que esse commit existe na árvore local aprovada. O checkout CT não tem remote Git configurado; por isso a identidade é sustentada pelo bundle, pela árvore exata e pela relação com a revisão aprovada, não por uma consulta de remote feita dentro do CT.

ESP-IDF está em 5.2.6, commit `9ef24e3e2a2c96e720d83c574a3f8699177573da`, árvore `e74c4c35593551e612df306248b403a92ec8a403`, remote configurado para `https://github.com/espressif/esp-idf.git`, status limpo e `git clean -ndx` vazio. Os submódulos constam nos recibos e foram conferidos pelo validador. Xtensa GCC é 13.2.0 (`esp-13.2.0_20250707`); WASI-SDK é 27.0, com clang 20.1.8 e SHA `e78c818b321d834a20df5796afa9a9329dc53fd1f0a2597166c7a283b355be77`.

O runtime resolvido em B/C é `wasmhome/wasm-micro-runtime` 2.4.5-wasmhome.1, revisão `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, componente SHA `7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`, lock SHA `348a5addf014587b24468fe55b44444284ee5d212d7f206c394a9659cf6f9669` e AOT version 5. O `wamrc` usado tem versão 2.4.5 e SHA `ff1bf482570dbab67521e0bbb0c682fdbd74f5e784f364eb34bfc9de735861fb`. A reconstrução independente do R5, com comparação dos archives LLVM e AOT de referência, está documentada e revisada em [`docs/evidence/compiler-rebuild-2026-10-13/README.md`](/home/haroldocurti/Documents/wasmhome/docs/evidence/compiler-rebuild-2026-10-13/README.md).

## Configuração e associação dos builds

Os três cenários usam build dir e `sdkconfig` separados. A projeção efetiva confirma alvo ESP32-S3, clock 240 MHz, otimização de desempenho, SPIRAM ativa, benchmark ativo e ambos selftests desativados. As chaves completas não foram expostas; abaixo ficam apenas hashes:

| Cenário | `sdkconfig` SHA-256 | `sdkconfig.json` SHA-256 | `compile_commands.json` SHA-256 | Receipt SHA-256 |
|---|---|---|---|---|
| Native | `919dab6ada7b62053dc5af0d0b70389e7219b1ebcef66dff74b035760c1f6600` | `f1f715b25126a50468329d243ed6a03bb07547252a3745f0fb74dcc846dd411d` | `d0a2ef74423b40baa54d8125066e63c15cd652e8b18fb24c09e740fe1e7dc22a` | `a4ebfc7beda6b3d3db55596f519b39996ecf9186c2cc60a70fc2ec89723a9c5f` |
| Interp | `473b97e2b27001de171dad269ede4bc47a0a47e96f1391adc34f9900d2721af7` | `f5f9f7613deef0b0bd148de64a5cd861cc965ec2c4e6b962a957bb29890a769f` | `b602442a6ff1f8cc78f5c1208bf898e4a234d603f42ccfe0ab1bea39c3f9efa0` | `bcffe4d71ed198a13cb5b0594a6f0cb06a58a3b8c54ab04f47b0f54862efa160` |
| AOT | `6f7391a6eab3fb9cd9226bb9f2cf8ba508c3fa45596dcfbf9b105e1ebe5018a5` | `387bebd96b20f914ae0f4b117a022cf8f17028b86bef1eed884d92470b5a593f` | `dc76f972f47af1dc878a846ea5cfbc9e39614d9150e48bcd31c9deb01f2d5b0e` | `88c18f005092d8dc864582745e0de19b4418b75aad88882e3079617767fda046` |

The validated mode split is correct: native contains no WAMR runtime symbols; interp enables the classic interpreter, with fast interpreter and AOT disabled; AOT enables AOT and disables the interpreter. B/C compile commands select `WASM_ENABLE_AOT_VALIDATOR=1` and `WASM_CONFIGURABLE_BOUNDS_CHECKS=0`. The AOT manifest explicitly enables bounds checks. CMake scenario, runtime source resolution, symbols, embedded payloads, and configuration were checked by the strict validator.

## Artifact, firmware and workload review

WASI clang uses `--target=wasm32-wasi -O3`; the AOT command uses `--target=xtensa --bounds-checks=1 --disable-llvm-lto --opt-level=3`. The manifest says AOT v5 and expected checksum `0xDBB2F0D4`. The validator also confirmed the generated embedded C arrays match their exact payload bytes.

A fresh generation ran in a second source tree at the same pinned commit/tree. The exact argument vector is preserved in `artifact-generation-argv.json`; it exited 0. All five outputs were byte-identical to the source artifacts:

| Payload | Bytes | SHA-256 |
|---|---:|---|
| `benchmark.wasm` | 174 | `d4cc0e280d02a697bb52c391e3b225a0691bc24d14cefb2883e51e085e466847` |
| `benchmark.aot` | 812 | `c4c94777e5f9460b8e60065d5cb89c7e79907cac61720a61f5ebd5467b512d88` |
| `benchmark.wasm.s` | 2,185 | `eab49c09c9c22c1075837f268087e72f536472415abd6812c2292b19a2cf50cf` |
| `benchmark_wasm_embedded.c` | 1,235 | `0c1ad886ae451e8affa974ee79fd0377ab7b77c312039b3c78be331dade5e59b` |
| `benchmark_aot_embedded.c` | 5,272 | `b1e3bc1965b71c4c44a0132cb4cda37f64d521d9a57414949a59d9bde522bb21` |

The AOT workload disassembly was generated from that repeated tree's `benchmark.wasm` (SHA above), using `wamrc --target=xtensa --bounds-checks=1 --disable-llvm-lto --opt-level=3 --format=object`. `benchmark_xtensa.o` SHA is `5f030fce3499b11a85b47f4166c0148347938af218405f7581fdb2ec44cd7b3f`. The dump contains the AOT wrapper's stack/bounds checks and exception path, calls `aot_func_internal#0`, and shows the optimized workload loop bound `0x7d0` (2,000) before return. Its SHA is `aae952bdf4649ffa945e384f7386308048de4261e64978aec520e2c919d1e773`.

The native dump was produced from the linked native ELF with Xtensa objdump's `--disassemble=benchmark_run`. It shows the optimized 2,000-iteration loop and return. Dump SHA is `535deb6bf5050fc1c60e5236a99a8e755a87b1c5deef0165bbab1fd7a5d9f908`.

| Scenario | ELF SHA-256 | BIN SHA-256 |
|---|---|---|
| Native | `14831bd1a804539fa0c21305fd80ca89425c9d67025823e3fea4cfa1198c46dd` | `deecedc20bd7584c2afdb848220c7dda75bb7e65596e6d3a30138642641ac777` |
| Interp | `10773ca54a7d77f7bdb1e920c4b23fae5617474509a8292339f15e2fe526dff4` | `9dc0091a409bde7016ad2be4f3c0ad7a1b1b3a5cfb85a74ec0d4327a5ba60abb` |
| AOT | `4bc5f9802b81642d845afd3c090e1c9e3505c36332016eadce61dbbfc500c876` | `e22380c0e75ef3f09cf20f033550acfddc8706cfc3797142e40479cba6c13bef` |

The private transfer manifest and an independent local SHA pass agree on all six files. The local copies are mode `0600`. Public receipt/config projections, sanitized build command template, disassembly and argv records are preserved under the evidence directory above; raw configs, full logs and firmware binaries are not in that directory.

## Limites

Este parecer aceita a evidência de build da #7, não a execução do produto. O status conservado nos recibos continua `build_checked_not_device_validated`; o checksum `0xDBB2F0D4` ainda precisa ser conferido na placa no gate #9. Os recibos completos não têm assinatura externa. O checkout de origem do CT não conserva remote Git configurado, embora o bundle e commit/tree tenham sido conferidos contra a revisão local aprovada. Nenhuma dessas limitações foi ocultada ou apresentada como resultado físico.

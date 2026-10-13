# Correções após revisão independente — E1 #9 e serviços #11

Data: 13/10/2026. Base observada: `HEAD=0ba2faab16dc9671f89a663eea8e6d0d1f528997`. Este relatório registra a correção dos dois achados de coerência do validador E1 e da retenção de IDs de `UPDATE_TRIGGER`, mais a decisão explícita de contadores. A revisão independente do snapshot corrigido ainda deve confirmar o adaptador final antes de integração/build ESP-IDF.

## Mudanças verificáveis

- O validador `tools/validate_e1_device.py` agora rejeita status E1 final anterior a `after_release`; exige capacidade interna constante na sessão; compara `heap_before`, `heap_after_load`, `heap_after_instantiate` e `heap_min` ao total; impede `heap_min` maior que qualquer amostra de memória livre; e exige que `heap_min` não aumente entre as 30 repetições medidas. Foram adicionados negativos correspondentes em `tests/test_validate_e1_device.py`.
- A cache de tentativas update mantém até 16 UUIDs admitidos em um boot, sem eviction. UUID conhecido continua disponível para replay idêntico e conflito de conteúdo/sequência. UUID novo após saturação retorna `WH_NATIVE_UPDATE_CAPACITY_FULL`; o adaptador registra `native_update_replay_capacity_full` e descarta sem resposta/cache mutation. Os testes host verificam capacidade, replay, conflito conhecido, saturação e ausência de falso conflito para UUID desconhecido.
- Não há high-water global da sequência de entrada: o wire v1 não identifica a sessão do observador e uma regra global poderia rejeitar nova sessão legítima. Sequence zero continua inválida no codec; a monotonicidade dos datagramas novos é responsabilidade do emissor. O serviço sem OTA não atesta anti-replay entre UUIDs distintos. O contrato operacional #11 registra capacidade/saturação como admission control, sem alegar aceitação de tentativa ou sucesso OTA.
- A seção autorizada de contador/replay em `docs/protocolo-observacao.md` distingue uma resposta de serviço nova (próxima sequência TX, corpo/attempt correlacionados) da retransmissão de rede do mesmo datagrama (bytes e sequence preservados).

## Verificação

A suíte integral executada no snapshot corrigido passou com **81 testes: 80 aprovados e 1 skip**, exit code 0. O único skip é `test_windows_junction_target_and_staging_are_rejected`, que requer junction Windows. Log integral: `/tmp/wasmhome-native-fixes-r3-20261013/unittest-final.log`, SHA-256 `e50cee643101af4eb2735175f1228b7a9045efcc315b0ea134f627c5573a7fa7`.

Comando executado, com PATH limitado a esta execução e sem bytecode Python:

```sh
PYTHONDONTWRITEBYTECODE=1 \
WASMHOME_WIRE_LIBRARY=/tmp/wasmhome-p1-20261013-r2/libobservation_wire.so \
PATH=/home/haroldocurti/wasmhome-toolchain-e1-2.4.5-xtensa18-20261012/tools/cmake/bin:/home/haroldocurti/.local/toolchains/wasmhome-pwsh-7.6.6-20261013:/usr/bin:/bin \
/usr/bin/python3 -B -m unittest discover -s tests -v
```

Ambiente no PATH explícito usado pela suíte: PowerShell 7.6.6 (`pwsh` SHA-256 `3c6e4d629571429cf6cc86db94278e2ce17349edbbfe0a0de007bb6bccdbc208`), CMake 4.4.4 e GCC 16.2.1. Ninja 1.13.2 está disponível no toolchain local, mas não foi incluído nesse PATH de teste. O codec real `main/observation_wire.c` foi compilado em `/tmp/wasmhome-p1-20261013-r2/libobservation_wire.so`, SHA-256 `667985b4aee880ea755bc4abcd263646c44bcf0b6b4765ba935889b0ea6893b9`. `git diff --check` passou sem saída.

## Identidade e escopo do snapshot

O diff consolidado contra a base, incluindo arquivos tracked e novos atualmente presentes no worktree compartilhado, mede 106.042 bytes e tem SHA-256 `0443a2c36cc6b8f74277369e1fdf1af65f5ede1955761d14e178b5e47eb26f1d`, preservado em `/tmp/wasmhome-native-fixes-r3-20261013/working-tree-final-r3.diff`. Esse é o estado conjunto do worktree e contém alterações paralelas de outros agentes; não é apresentado como diff exclusivo desta frente.

Hashes principais nesta revisão:

- `main/native_services.c`: `5877179b15c186d32f610410b72de72a62cb1926f0ee18599a3780ee51546ca4`
- `main/native_services.h`: `2125784bf7d52f3399bc85edc29b9968d65088a67adc493f8f3aedcb4a705f3a`
- `tests/test_native_services.py`: `4e4aa4cde8368c9ae4b8cfc63d2d8b9fdf8648d3dbb13188624ad18f03b64ab1`
- `tools/validate_e1_device.py`: `93b77513d265b2a8cf73d04deb8e72c9da7ee54f488878fcc3caa0c9564c45ea`
- `tests/test_validate_e1_device.py`: `f6a38f94400ea492f573af4ee549422f02e3cc443ec31cb4ce9fdd1469367ab9`
- `docs/protocolo-observacao.md`: `ebc6d0cbbdf0707a27bd3ba75b67db7f2941835aba6dca68e11eccf772e7c221`
- `docs/plano-proximos-passos-agentes.md`: `08018d4b6f85a3622c79dee9199e58fe6499044af9f5c977062448bf69e60204`

## Limites

O teste C host compila o núcleo de política/codec real, não o adaptador FreeRTOS/ESP-IDF contra headers reais. A bateria completa prova os gates e cenários Linux aplicáveis; não prova MSVC nem junction Windows. Ainda não foi executado build ESP-IDF de serviços depois do snapshot corrigido, nem houve flash, serial, GPIO, rede física, leitura física DS18B20, ou execução WAMR em placa. A limitação da cache de 16 IDs é deliberada e deve permanecer clara para operação sem OTA; um futuro OTA precisa definir identidade/sessão e retenção de replay apropriadas.

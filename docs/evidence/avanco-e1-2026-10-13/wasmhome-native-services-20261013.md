# Serviços nativos UDP v1 — issue #11

**Estado:** implementação e testes host concluídos; aguardando revisão independente do adaptador ESP-IDF e possível build coordenado. Esta entrega não inclui OTA, download, gravação de firmware nem ensaio físico.

## Base e escopo

- Revisão base: `0ba2faab16dc9671f89a663eea8e6d0d1f528997` (`HEAD` observado antes das alterações desta trilha).
- Alterações desta trilha: `main/native_services.c`, `main/native_services.h`, `tests/test_native_services.py`, `main/main.c`, `main/CMakeLists.txt` e `main/Kconfig.projbuild`.
- A alteração anterior autorizada em `tests/test_e1_runner.py` pertence à frente P1 e está fora do diff escopado abaixo. Também há mudanças simultâneas de outros agentes em `CONTEXT.md` e nos planos; não são parte desta entrega.
- SHA-256 do diff escopado (seis arquivos acima): `b798026003917818546bcbe1397ea5e621a73ca1989c6b12048cd4c133b66062` (37.620 bytes), salvo em `/tmp/wasmhome-native-services-20261013/native-services.diff`.

## Implementação

- Serviços opt-in via `CONFIG_WASMHOME_NATIVE_SERVICES`, desligado por padrão. CMake e compilação recusam cenários interp/AOT e combinação com autotestes de diagnóstico ou benchmark; a fonte do serviço só é incluída no cenário nativo quando a opção está ativa.
- `CONFIG_WASMHOME_CONTROL_VERSION` fixa 1 (limiar 25 °C) ou 2 (30 °C) no firmware. Não há chave `WH_V1`/`WH_V2` nem troca em execução.
- O controle usa histerese total de 1 °C: liga em limiar +0,5 °C, desliga em limiar −0,5 °C e conserva o estado dentro da banda. O domínio de entrada −55…125 °C acompanha o domínio documentado de estímulo do `CONTROL_REQUEST` e do DS18B20.
- A aplicação GPIO chama `wh_gpio_apply` e reporta nível solicitado e nível do loopback lido; a plataforma existente configura a saída baixa antes de iniciar o serviço. A telemetria lê `wh_sensor_latest()` do serviço DS18B20 existente: ID, último valor válido retido, idade saturada e estado da aquisição mais recente, com emissão a cada 100 ms e aquisição física a cada 500 ms.
- Controle deduplica por `sample_id` e temperatura: repetição idêntica reusa o resultado, payload conflitante é descartado e ID anterior removido do cache é marcado stale sem nova execução GPIO. Cache de replay em RAM: 64 respostas de controle e 16 tentativas de atualização.
- O handler valida o datagrama com o codec embarcado UDP v1, trata boot incorreto antes do controle (a plataforma já descarta esse caso) e rejeita pedidos de atualização com `BUSY` quando válidos, pois esta build não tem caminho de OTA/download. Repetições da mesma tentativa recebem a recusa correlacionada; conflito de `attempt_id` recebe `ATTEMPT_CONFLICT`.

## Verificação executada

- `python3 -m unittest discover -s tests -p 'test_native_services.py' -v`: 6 testes host C aprovados. O harness compila o mesmo núcleo de política/telemetria e `observation_wire.c` com GCC 16.2.1, C11, `-Wall -Wextra -Werror -pedantic -pthread`; cobre bordas e vetores distintos v1/v2, replay/conflito/stale, resultados GPIO de mismatch/erro, codec de telemetria/controle/aceite, estado/idade/sentinela do sensor, recusa e deduplicação de update e execução única sob 16 chamadas concorrentes.
- Suíte completa após as mudanças: 66 testes, 65 aprovados, 1 skip, exit code 0; log `/tmp/wasmhome-native-services-20261013/unittest-final.log`, exit code em `/tmp/wasmhome-native-services-20261013/unittest-final.exit-code`. PATH dessa execução incluiu PowerShell portátil 7.6.6, CMake 4.4.4 e Ninja 1.13.2 locais. O gate CMake executou com sucesso. O único skip é o negativo que exige junction Windows; não é declarado como provado no Linux.
- `git diff --check`: exit code 0.
- A rodada anterior ao ajuste final de inclusões também passou 66 testes / 1 skip; a rodada definitiva ocorreu depois do ajuste.

## Limites e decisões pendentes

- Os testes host exercitam as regras e o codec C, não o adaptador FreeRTOS/ESP-IDF; o adaptador ainda precisa de revisão e build ESP-IDF coordenado.
- Não há prova elétrica do DS18B20, loopback/GPIO, rede, temporização em placa, nem prova de execução WAMR real. Nenhum hardware/serial foi acessado nesta entrega.
- A resposta de replay é enviada pelo `wh_observation_send`, que aloca uma nova sequência de saída monotônica, mantendo `attempt_id` e corpo correlacionados. Isso não é retransmissão byte a byte do datagrama anterior; a interpretação deve ser confirmada pelo revisor perante a regra de sequência do protocolo.
- Depois de evictado do cache, um `sample_id` antigo de controle é suprimido por high-water mark, não pode ser replayado. Tentativas de update antigas que saem do cache de 16 entradas ainda não têm tombstone persistente; não geram efeito OTA porque esta build não implementa atualização.

## PowerShell portátil preservado para repetir a suíte

Pacote recuperado por leitura do guest e preservado em `/home/haroldocurti/.local/toolchains/wasmhome-pwsh-7.6.6-20261013`; SHA-256 do tarball: `ddbc4a2d113bbd46d283cfedcbcd117a70caefd7673f41f2b4e0000badf103bc` (igual ao valor histórico conferido). SHA-256 do executável `pwsh`: `3c6e4d629571429cf6cc86db94278e2ce17349edbbfe0a0de007bb6bccdbc208`; `pwsh -Version` informou `PowerShell 7.6.6`. Não foi instalado pacote de sistema nem alterado o PATH do shell do usuário.

Para reproduzir a suíte completa nesta máquina, compile primeiro o codec C real em caminho temporário e use PATH explícito apenas para o processo:

```sh
cc -std=c11 -Wall -Wextra -Werror -fPIC -shared main/observation_wire.c -o /tmp/wasmhome-p1-20261013-r2/libobservation_wire.so
PATH=/home/haroldocurti/wasmhome-toolchain-e1-2.4.5-xtensa18-20261012/tools/cmake/bin:/home/haroldocurti/wasmhome-toolchain-e1-2.4.5-xtensa18-20261012/tools/ninja:/home/haroldocurti/.local/toolchains/wasmhome-pwsh-7.6.6-20261013:/usr/bin:/bin WASMHOME_WIRE_LIBRARY=/tmp/wasmhome-p1-20261013-r2/libobservation_wire.so /usr/bin/python3 -m unittest discover -s tests -v
```

A contagem de skips varia com o estado da árvore e ferramentas: a baseline limpa pré-diff teve 60 testes e 3 skips (CMake indisponível/fora do PATH, runner MSVC indisponível e junction Windows); com a alteração P1 e PowerShell no PATH mas sem CMake, 60 testes e 2 skips; com CMake/Ninja locais, aquela suíte passou com 60 testes e 1 skip. A validação final desta árvore incluiu os seis testes novos: 66 testes e 1 skip. Não inferir cobertura da junction Windows a partir desses resultados.

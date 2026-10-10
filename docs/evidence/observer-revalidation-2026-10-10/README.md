# Revalidação do observador em 10/10/2026

Os testes e as duas calibrações usaram uma exportação por `git archive` do
commit `810b8bea76faf11854d5fe31694dc69c76095a02`, sem alteração nas fontes.
O Python 3.11.2 do ambiente ESP-IDF executou `tools/test-platform.ps1`:
28 testes passaram, sem skips, incluindo o codec C compilado com MSVC.

| Ensaio local UDP | Interrupção conhecida | Erro absoluto | Tolerância prévia | Resultado |
|---|---:|---:|---:|---|
| [Mesmo boot](same-boot/calibration.json) | 1.000 ms | 6 ms | 100 ms | Passou |
| [Reboot simulado](simulated-reboot/calibration.json) | 1.000 ms | 104 ms | 200 ms | Passou |

As tolerâncias são as da ferramenta, declaradas antes da execução. O relógio
é `time.monotonic_ns` desse Python no Windows; sua resolução limita a ordenação
de eventos muito próximos. As métricas não subtraem relógios do PC e do MCU.
Estes ensaios usam sockets em loopback no host. Não constituem ensaio físico,
atualização de artefato, OTA ou campanha.

O [inventário](inventory.json) registra a revisão da fonte, o ambiente e hashes
dos oito arquivos brutos/derivados copiados sem alterar seus bytes. Em cada
pasta, `fixture.jsonl` e `observations.jsonl` preservam o percurso dos datagramas;
`summary.json` e `calibration.json` registram os resultados.

Os resumos host históricos de 02/10, em
`observer-2026-10-02/final-same-boot` e `final-reboot`, não incluem
`valid_controls_by_boot`, presente na saída do código dessa revisão. Eles
continuam preservados como registros históricos; esta revalidação comprova
a saída do pin exato, sem atribuir retroativamente campos aos dados anteriores.
Os ensaios físicos de 02/10 mantêm seu registro próprio.

Integração na `main`, publicação e aceite de dependências são etapas separadas.

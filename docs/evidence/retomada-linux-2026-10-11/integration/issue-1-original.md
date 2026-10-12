## Destination

Implementar e validar no mesmo ESP32-S3 a atualização OTA nativa, a troca de módulo WebAssembly interpretado e a troca de módulo AOT; executar as campanhas normal e de resiliência; e entregar resultados analisados e reproduzíveis para o TCC.

## Notes

- Este mapa acompanha decisões, implementação, campanha e resultados. A execução está explicitamente incluída.
- Ao concluir um ticket, marcar os critérios de aceite efetivamente satisfeitos e remover rótulos de trabalho pendente; manter comentário de resolução com referência à evidência.
- Aplicar as skills Wayfinder e to-tickets e o glossário `CONTEXT.md` em todas as sessões.
- Usar FreeRTOS como base comum. Não há cenário bare-metal.
- Manter dois serviços: telemetria DS18B20 em módulo estável e controle GPIO v1/v2 no módulo substituído.
- Usar UDP binário para observação e disparo, HTTPS para artefatos e JSONL como registro bruto.
- Executar cinco tentativas piloto por cenário e usar 30 por cenário como referência da campanha, confirmada após o piloto.
- Distinguir configuração, build, execução local, execução no dispositivo, atualização e resultado experimental.
- Cada ticket de execução deve terminar com evidência verificável e preservar falhas e timeouts.

## Decisions so far

- [Inventariar e fixar a bancada física](https://github.com/haroldocurti/wasmhome/issues/2) — a placa, sensor e loopback, alimentação, rede, host e condições iniciais estão registrados com limites de evidência explícitos.
- [Fechar o protocolo binário de observação](https://github.com/haroldocurti/wasmhome/issues/3) — contrato UDP v1 documenta cabeçalho de 48 bytes, mensagens, validação e vetores; codec de referência passou nos testes locais. O transporte embarcado da #8 foi validado no dispositivo com o firmware diagnóstico, incluindo envio, recepção e rejeições. Os módulos funcionais e as campanhas permanecem pendentes.
- [Fechar o envelope assinado e a política antirretorno](https://github.com/haroldocurti/wasmhome/issues/4) — usar COSE Sign1, CBOR determinístico, ES256/P-256 e piso monotônico em NVS por campanha, cenário e componente.
- [Definir ensaios válidos de resiliência](https://github.com/haroldocurti/wasmhome/issues/5) — usar traps Wasm reproduzíveis, aborto nativo controlado e ensaios comuns de assinatura, hash e sequência.

## Not yet specified

- Ajuste definitivo do tamanho amostral caso a dispersão do piloto invalide a referência de 30 tentativas por cenário.
- Parâmetros definitivos de timeout e janelas de observação, a congelar após o piloto.

## Out of scope

- Execução bare-metal ou sem FreeRTOS.
- Dashboard, gestão de frotas e múltiplos dispositivos.
- Sensores adicionais ao DS18B20 e migração de estado entre versões.
- Secure Boot e criptografia de flash nesta primeira campanha.
- Generalização dos resultados para sistemas industriais, hospitalares ou críticos.
## Estado da implementação

- [Configurar a plataforma comum ESP32-S3, ESP-IDF e FreeRTOS](https://github.com/haroldocurti/wasmhome/issues/8): concluída e integrada à main pela [PR #27](https://github.com/haroldocurti/wasmhome/pull/27), merge [9d332d6](https://github.com/haroldocurti/wasmhome/commit/9d332d6d33686c47b601f7e734e762a26079c0ea). Revisão sem bloqueadores restantes, 17 testes e build nativo ESP-IDF 5.2.6 aprovados na mesma árvore do merge. Diagnóstico físico confirmou PSRAM, DS18B20, GPIO, Wi-Fi, UDP, recuperação após watchdog e retirada/reconexão dos fios no mesmo boot. [Evidências e limites](https://github.com/haroldocurti/wasmhome/blob/9d332d6d33686c47b601f7e734e762a26079c0ea/docs/evidence/esp32s3-hardware-2026-10-02.md). Reconexão Wi-Fi no mesmo boot e execução dos módulos/campanhas permanecem sem ensaio.

- [Construir e calibrar o observador externo](https://github.com/haroldocurti/wasmhome/issues/10): concluída e publicada no commit [810b8be](https://github.com/haroldocurti/wasmhome/commit/810b8bea76faf11854d5fe31694dc69c76095a02). [Resolução e limites](https://github.com/haroldocurti/wasmhome/issues/10#issuecomment-5946362705). Observador UDP/JSONL passou em 28 testes locais e calibração no host e no ESP32-S3, incluindo pausa conhecida, GPIO físico e recuperação após reboot real. Firmware anterior restaurado. Branch `codex/issue-10-external-observer` ainda aguarda merge na `main` antes de iniciar a implementação dependente; serviços definitivos, OTA e campanhas permanecem nas respectivas issues.


# Mapa offline da flash e do slot OTA (sanitizado)

Leitura completa confirmada pelo manifesto; análise feita offline, sem acesso direto à serial. O candidato de seleção registrado em `otadata` foi confirmado pelo offset de carga que o bootloader reportou após o reset capturado; isso não comprova a saúde do processo depois do boot.

- Placa: ESP32-S3 rev0.2
- Captura UTC: 2026-10-13T12:55:50.421394+00:00
- Flash: 16777216 bytes; SHA-256 `102a383238010aca383878a22584fead6a67b8114f26ff46973799591688e95d`
- Tabela física verificada: offset `0x8000`, SHA-256 `f385526438c35a25ca68d738504ad24852a0b3dbc1b2d8d47740614ee3dca557`

| Partição | Tipo/subtipo | Offset | Tamanho | SHA-256 |
|---|---|---:|---:|---|
| `nvs` | `data/nvs` | `0x9000` | `0x6000` | `af485682398fd0d14404d654af79c097fe5345ee4acbe35b8b9e9cdfd106e996` |
| `otadata` | `data/ota` | `0xf000` | `0x2000` | `8ba3b110139f45443d4f268d1a3373ef99a1718b71d51664531b83ee2d4b91a3` |
| `phy_init` | `data/phy` | `0x11000` | `0x1000` | `102457c71ee90603298f4259315d961b44b16547ed22ef11444d854087e16da3` |
| `ota_0` | `app/ota_0` | `0x20000` | `0x700000` | `67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b` |
| `ota_1` | `app/ota_1` | `0x720000` | `0x700000` | `17a0b2007d7d2b63b065904a3ced14cc1a0c3b104eadfcca65ccf80c8dfac7d9` |

## Registros OTA

O campo `seq_label` foi deliberadamente omitido e nem é lido pelo decoder. O CRC sobre os quatro bytes little-endian de `ota_seq` foi conferido tanto com zlib (semente UINT32_MAX) como com uma implementação bit a bit independente que segue os complementos e polinômio documentados pelo ESP-ROM.

| Cópia | Offset absoluto | ota_seq | Estado | CRC | Válido pelo bootloader |
|---:|---:|---:|---|---|---|
| 0 | `0xf000` | `0x00000001` | `VALID` | `4743989a` (válido) | sim |
| 1 | `0x10000` | `0xffffffff` | `UNDEFINED` | `ffffffff` (inválido) | não |

Segurança reportada para a captura: Secure Boot e Flash Encryption desabilitados. O valor eFuse usado pela política anti-rollback não foi lido.

## Candidato de boot derivado

- Partição: `ota_0` (`ota_0`) em `0x20000`, sequência OTA `1`.
- Projeto: `wasmhome_bench`; versão: `6d7382b-dirty`; ESP-IDF: `v5.2.6`; secure_version: `0`.
- Verificação offline da imagem: passou.

## Confirmação do bootloader

- Captura UART de reset: `2026-10-13T13:08:57.835257+00:00`; arquivo privado `baseline-boot-serial.log`, 26459 bytes, SHA-256 `d61559750c0f1efde7540deb558daaa7637ed5cb5f52e3ed51310948ff9b3a7b`.
- O bootloader informou carregar o app no offset `0x20000`; corresponde ao candidato da tabela: sim.
- O log bruto não é reproduzido. A observação prova apenas o offset informado pelo bootloader naquele reset, não saúde de execução posterior.

## Metadados dos slots

| Partição | Offset | SHA-256 | Projeto | Versão | ESP-IDF | checksum/hash | Conteúdo apagado |
|---|---:|---|---|---|---|---|---|
| `ota_0` | `0x20000` | `67860a5f2a36c67fa36a19ca34badaf795dd003bd1240ebd2fc0dce375a2a48b` | `wasmhome_bench` | `6d7382b-dirty` | `v5.2.6` | valid; SHA valid | não |
| `ota_1` | `0x720000` | `17a0b2007d7d2b63b065904a3ced14cc1a0c3b104eadfcca65ccf80c8dfac7d9` | `—` | `—` | `—` | sem parse | sim |

## Plano da bancada

- Este ensaio grava manualmente uma imagem de teste apenas no slot ativo `ota_0` em `0x20000`, tamanho `0x700000`; não usa fluxo OTA.
- Antes do ensaio, preservar o backup integral validado. Depois, restaurar os bytes originais de `ota_0` desse backup e exigir o mesmo SHA-256 capturado.
- Manter bootloader, tabela de partições, `otadata`, `ota_1` e NVS intactos. Comparar a flash inteira com a baseline; antes da restauração, qualquer diferença só é esperada dentro de `ota_0`; depois, exigir SHA-256 integral igual à baseline.
- O plano não executa nem autoriza escrita por este decoder.

## Limites

- A tabela é encontrada ao varrer offsets alinhados a 4 KiB e validada pelo parser local ESP-IDF 5.2.6 gen_esp32part.py; nenhum offset fixo de tabela ou app é presumido.
- CRC e validade de otadata seguem bootloader_common_ota_select_* do ESP-IDF 5.2.6. A sequência identifica o candidato OTA selecionado pelas cópias válidas.
- A captura UART privada posterior a reset normal informa o offset carregado pelo bootloader e seu hash está registrado; isso não prova saúde posterior do processo nem exclui fallback após essa observação.
- esptool image_info verifica estrutura, checksum e hash anexado quando presente; política eFuse de revisão do chip e anti-rollback não foram estabelecidos independentemente por esta leitura offline.
- NVS e strings arbitrárias do firmware nunca são impressos nem copiados para o relatório sanitizado.

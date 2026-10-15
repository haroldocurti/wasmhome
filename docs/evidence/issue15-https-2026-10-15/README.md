# Evidência — distribuição HTTPS assinada (#15)

**Data:** 15/10/2026 (America/Sao_Paulo)  
**Campanha:** `issue15-bench-20261015`  
**Revisão de origem:** `402849e8a461e85c51c3bec33de160e2c5ed83b8`

## Proveniência

A matriz foi montada com os builds nativos v1/v2, os módulos Wasm e os módulos AOT gerados para `wamr-2.4.5-wasmhome.3`. O manifesto de módulos confirma a revisão acima e o componente WAMR `2.4.5-wasmhome.3`, SHA-256 `7928f0852db4c43b1e2d7ae19ac957d0cddae77e458046612c6100e9e9dbee39`.

A chave P-256 de assinatura foi criada no CT107 e permaneceu no diretório privado do assinador. O serviço HTTPS roda como usuário separado, sem acesso à chave privada. A matriz usa COSE Sign1/ES256 com assinatura low-S, sequência 1 para v1 e 2 para v2 em cada cenário.

## Matriz capturada

Cada linha associa o tamanho e SHA-256 do payload ao tamanho e SHA-256 do envelope COSE recebido por HTTPS.

| Cenário / componente | Versão / sequência | Payload (bytes) | SHA-256 do payload | COSE (bytes) | SHA-256 do COSE |
| --- | --- | ---: | --- | ---: | --- |
| 1 / firmware nativo | v1 / 1 | 747232 | `7b3cc3bb8061c72e4483c16fdac730522739a026ae1232598f23890020d60af6` | 186 | `80a53edc530322f16e1228626e9b6d50786d68d9b086c28e88ca75c24ca7e596` |
| 1 / firmware nativo | v2 / 2 | 747232 | `fc3005152183dba32535709e62ddab964bbfed012b72bfeafd33815faabae8f4` | 186 | `dde47f870551dc9bc0264ab3802aab999029b457a539a920aa8eaa19a08e9418` |
| 2 / GPIO Wasm | v1 / 1 | 328 | `80e885af2f93152efa9f66f04955edb82cbe1920332b32b729b9c48c4307bff2` | 188 | `29725289a9e71dcf5736f822472c3ad2aa01c0d8395a7c517f397fbb8033509f` |
| 2 / GPIO Wasm | v2 / 2 | 328 | `3a972948112397acdaad03c81714a3daf147ecce926f0854341e58a350f3d5c5` | 188 | `804b994d5c5d5a2f220979ad115a2649975279d1f5ac06d8ced767e355af7bc7` |
| 3 / GPIO AOT | v1 / 1 | 1256 | `a9c4c7e440ee37e7b53dbcb6d2e8009dd00e4a6fe278180bb9b9ef485e0a5746` | 209 | `2fe20535c089e97ffb08c91c6650174b1bb82d05d4b8ccf8502205bc7b50f3a8` |
| 3 / GPIO AOT | v2 / 2 | 1256 | `293d78b1af5e0c7e76593d9b8e755692ee311d8427c43d94673ddfd499d5bf53` | 209 | `f0a4a30653f51d43694044690cf48879123bf3561adebd49c3da257569b2cf40` |

O cliente baixou o `index.json` (3.917 bytes, SHA-256 `2d7923583f001e6d53f86fc95ca9a9806b2cd9c4f3bb008fe729674adf7695f1`), a chave pública servida e os seis payloads com seus seis envelopes. `verify-set`, usando uma cópia pinada da chave pública obtida separadamente do CT, validou a campanha, o toolchain AOT, as seis assinaturas, tamanhos e hashes. A chave pública servida coincide byte a byte com o pin independente.

## HTTPS e controles negativos

A captura foi feita no host por `https://wasmhome-bench.test:8443`, com resolução explícita para CT107 e validação TLS ativa pela CA local. Todos os 14 GETs (índice, chave pública e 12 arquivos da matriz) retornaram HTTP 200 com resultado de verificação TLS 0; os eventos registram TLS 1.3. A validação OpenSSL do certificado passou para o nome DNS e para o IP do CT.

Fingerprints públicos e SAN estão em [`public-fingerprints.json`](public-fingerprints.json). A CA e o certificado folha são válidos de 15/10/2026 a 14/11/2026. O certificado folha cobre `wasmhome-bench.test` e `192.168.0.138`.

As respostas negativas observadas foram:

- CA não confiável e hostname TLS incorreto: falha de validação do certificado antes de HTTP.
- `Host` HTTP incorreto: 421.
- caminho não allowlisted: 404.
- método PUT: 405 com `Allow: GET, HEAD`.
- uma conexão iniciada de fora da origem autorizada expirou sem TLS/HTTP e incrementou o contador da regra nftables de descarte.

Os resultados de build, cabeçalhos sanitizados, eventos de servidor, catálogo de downloads, fingerprints e resultados negativos estão nos arquivos JSON/JSONL desta pasta. O endereço do cliente nos eventos foi substituído por `capture-host`. O teste inicial expôs um 421 causado pela colisão com `HTTPServer.server_name`; a correção e a regressão correspondente estão no commit de origem.

A primeira transferência AOT também encontrou MTU incompatível: o CT anunciava 1500, enquanto o caminho de firewall do Proxmox usava 1480. O `net0` do CT107 foi persistido em MTU 1480; depois disso, os GETs AOT foram concluídos e validados. A verificação final confirmou os serviços HTTPS e firewall ativos e a origem não autorizada bloqueada.

## Verificação reproduzível

- `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.test_signed_artifacts tests.test_artifact_https_server -v`: 18 testes passaram. A suíte cobre assinatura/hash/sequência/toolchain inválidos e validação de TLS/Host/método/caminho.
- `python3 -B tools/generate_service_modules.py --verify`: módulos Wasm/AOT verificados contra o manifesto da revisão de origem.
- `python3 -B tools/signed_artifacts.py verify-set --package <captura> --public-key <pin independente> --expected-campaign-id issue15-bench-20261015 --expected-aot-toolchain-id wamr-2.4.5-wasmhome.3`: seis artefatos verificados.

## Limite do aceite

Esta evidência conclui geração, assinatura, verificação no host e transporte HTTPS da #15. Ela não comprova download pelo MCU, integração do verificador no firmware, persistência do piso antirretorno em NVS, rollback nem OTA. Esses critérios permanecem nas issues #16–18 e #22. Nenhuma chave privada, configuração de Wi-Fi, log bruto ou ELF privado foi incluído.

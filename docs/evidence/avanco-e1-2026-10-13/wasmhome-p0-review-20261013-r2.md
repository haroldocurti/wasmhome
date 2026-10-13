# Revisão independente P0 — proveniência 801 e revalidação B

Data: 13/10/2026. Revisão somente leitura dos artefatos preservados em `/tmp/wasmhome-p0-20261013-r2/`, com scratch Git em `/tmp/wasmhome-p0-review-r2.git`. Não editei o guest, manifests originais, builds ou checkout compartilhado; não acessei serial nem placa.

## Parecer

A associação dos artefatos fica demonstrada nos seguintes limites: os registros originais R6 inicial e R7 apontam para `b657a19b98b7947a0b79d02619d903af9e8d2863` / árvore `1a166c0c2cb30a720e57917fc214f591d81a6b29`; o manifest final R6 aponta para `801f9381da1a8ed0895adc88e8cd03f3a1df6707` / árvore `c728d9ed348e7fdefe4dac403a4c42b6916e704a`. Dois manifests novos R8/R9 registram a mesma identidade final e os mesmos cinco payloads. Um build B recente compila até `[1048/1048]` nessa árvore e termina com recibo `build_checked_not_device_validated` depois da preservação do `.pyc` gerado pelo IDF.

A proveniência histórica de `f31158a3…` continua incompleta: não existe log de geração que associe diretamente esse manifest final a um comando/instante. Também identifiquei uma ressalva no bundle: apesar de `git bundle verify` declarar histórico completo, uma clonagem bare direta falha ao percorrer o pai ausente do limite shallow `a7c4f72`. Os commits 801/b657 e suas árvores foram recuperados e verificados em scratch, então a identidade/árvore usada nas repetições é verificável; a alegação de que o bundle contém todo o histórico não é sustentada.

## Fontes e verificação independente

Consultei o relatório P0 original (`/tmp/wasmhome-p0-20261013-r2.md`, SHA-256 `d233b570dd9658131c3db15409db6f857297a16635a439c07d5315c8c6030a64`), `checkout-history.txt`, `source/remote-bundle-verification.txt`, manifests/recibos R6–R9, logs originais, logs de repetição e validação, além do resumo final do build B. Bundle recebido: 1.182.690 bytes, SHA-256 `bbad56f4d599579db41d5d8b9c4258cedc132c0c962dd0b0c532f7fceb0c9e2a`.

A verificação do bundle no host o chama de completo. No entanto, executei `git clone --bare /tmp/wasmhome-p0-20261013-r2/source/p0-r2-source-801.bundle /tmp/wasmhome-p0-review-r2-clone-test`; o comando saiu 128 com `Could not read 8b372147e739e46c8952ab8c7907b6c76a76345b` e `Failed to traverse parents of commit a7c4f72…`. O `checkout-history.txt` registra `a7c4f72` como `(grafted)` e o reflog inicia nesse limite. Num scratch local, importar os objetos do bundle e registrar esse mesmo limite shallow permitiu verificar `b657a19` → árvore `1a166c…` e `801f938` → árvore `c728d9…`. Assim, o problema é a completude do histórico anterior ao limite, não a identidade dos dois commits de interesse.

## Cronologia histórica e associação de conteúdo

- R6 inicial: `generate-r6.log` SHA-256 `963272537e4a85e7e4abe7cae4970a1d1836f2e2b2417630201b77e358773662`; o log e o manifest inicial `48bfdf9bc27c5b091e1dde2ed670053ac99f36044aaadfc8d1d3628abaa0f356` indicam fonte `b657a19…` / árvore `1a166c…`.
- R7: `generate-r7.log` SHA-256 `812be9c62a720c43f4cb20b4fdbe5a9f48575bb8bba746710cfdeaeb01f3da6d`; manifest `001bf954d8bdf66f0f486a913e232e15aed570e21f96e7290173b927384e4495`, também em `b657a19…` / `1a166c…`.
- Manifest final R6: SHA-256 `f31158a3b0ce300c498a49469eddcd0f082c137a61a47b89b581408183faf098`, declarando `801f938…` / `c728d9…`. O log integral R6 preservado termina na geração inicial às 23:21:41; não contém geração posterior correspondente a esse manifest. O reflog situa o commit 801 em 23:31:17. Portanto, a lacuna do comando que produziu o manifest final continua aberta.
- Os cinco hashes de payloads no manifest inicial R6, manifest final R6 e manifest R7 coincidem: WASM `d4cc0e280d02a697bb52c391e3b225a0691bc24d14cefb2883e51e085e466847`, AOT `c4c94777e5f9460b8e60065d5cb89c7e79907cac61720a61f5ebd5467b512d88`, assembly `eab49c09c9c22c1075837f268087e72f536472415abd6812c2292b19a2cf50cf`, C WASM `0c1ad886ae451e8affa974ee79fd0377ab7b77c312039b3c78be331dade5e59b`, C AOT `b1e3bc1965b71c4c44a0132cb4cda37f64d521d9a57414949a59d9bde522bb21`. A comparação das árvores `b657` e `801` mostra uma mudança de arquivo: `tools/validate_build_config.py`, de SHA `11d74820f23387d1fff5286be578155f71cedfe046805652edcf9f65a1ebeaef` para `f3c874ebdf45a8faa9750abf2231c04089b478d52e4b7e398f8fca9eb61b2701`, +9/−1 linhas. Payloads iguais provam igualdade de bytes, não qual execução histórica os gerou.

## R8/R9 novos e B novo

R8 manifest SHA-256 `1a8c85d288cd08eb8bc600fc69c6211c5b7137070c2ee6e8fd92d78a3ec926d1`; log SHA-256 `1a6686d3dc6b9d7d49eb5152295d0370d972df477c9a39f9eace03566fe16c19`. R9 manifest SHA-256 `a62b25141a78da34e06b16ddcb59f93892c65df469b3af41965b06d719f41ea7`; log SHA-256 `2bf61559b2a4f11a96bed4afa3c581f0489d99e03d673f383f2e64c5aedbac22`. A comparação dos JSONs confirma fonte `801f938…` / árvore `c728d9…` e os cinco payloads acima em ambas as gerações. Os manifests diferem em caminho absoluto `workload` presente em `clang_flags`, portanto seus próprios hashes são distintos. Ambos declaram `generated_not_device_validated`.

O build B antigo não deve ser descrito como compilação bem-sucedida em 801: `build-e1-interp-r6.log` terminou às 23:29:25, antes do commit, e o gate indicou `effective runtime macro differs: WASM_ENABLE_AOT`. O log pós-commit `revalidate-e1-interp-r6.log` registra nova configuração CMake e 12 etapas incrementais, incluindo recompilação de `esp_app_desc.c.obj` e `benchmark_wasm_embedded.c.obj`, relink e validação. O ELF após a revalidação manteve SHA `d15565f136a26cf4e9d213533a40dd7d5eba8ad3f0654ab941ac00c4911c5dc5`, e o recibo de 23:32:58 declara manifest final R6 e fonte 801. Isso é uma revalidação/relink incremental posterior, não um build integral fresco em 801.

O novo build B R8 alcançou Ninja `[1048/1048]`. A primeira passagem de validação foi recusada por arquivo Python `.pyc` ignorado gerado pelo IDF; a inspeção registrou que o status Git normal e os dry-runs de limpeza recursivos estavam limpos. O `.pyc` foi preservado fora do IDF, com SHA idêntico `cc91ef8758b2d6c81094e9a0f61ae7bf4dcc735c0b5b5514a5f4095a5cd90480`; depois disso o validador oficial foi repetido e passou. Recibo B SHA-256 `b6d4d77d7a7b5b47cf6d52c256a2a49f24d81a21b19f521a10083715235413b9`, ELF `3dc61cd46339f3aa34f96956db0c592d6e195299cb5a290a151a2d1dbf977ada`, manifest R8 `1a8c85d288cd08eb8bc600fc69c6211c5b7137070c2ee6e8fd92d78a3ec926d1`, estado `build_checked_not_device_validated`.

## A/B/C históricos, configuração e limites

Os recibos históricos A/B/C registram `source_revision=801f938…` / `tree=c728d9…`, IDF `5.2.6` (`9ef24e3e2a2c96e720d83c574a3f8699177573da`, árvore `e74c4c35593551e612df306248b403a92ec8a403`) e o mesmo runtime WASM 2.4.5-wasmhome.1: componente SHA `7d3eba8883eaaefee0457e8593b2a5203050736487fcf62bbf05cd8ce0306251`, revisão `25bd7eb63e828e4bd242cc9b38d260b4b31c6605`, framework `e97860611e371d91d2ef25101b02b29719476a48`, lock `348a5addf014587b24468fe55b44444284ee5d212d7f206c394a9659cf6f9669`, AOT version 5. Os recibos declaram `build_checked_not_device_validated`; os valores privados de sdkconfig não são publicados.

No mapeamento A=native, B=interp, C=AOT: B antigo precisa da ressalva de revalidação incremental acima. O log C/AOT conclui build e traz recibo posterior à revisão 801. O log A/native termina com falha de validação por fonte IDF dirty/untracked/ignored; existe recibo posterior às 23:48 que declara 801, mas não foi preservado log de revalidação bem-sucedida correspondente. Logo, a fonte declarada pelo recibo A é consistente, mas a cadeia de validação do log A é mais fraca e não deve ser apresentada como comprovada pelo log falho. Os ELF hashes são: A/native `e652811f3ab89a504e922dae328dac0489fcf7e7f8f6df429d21f210dc683c52`, B/interp antigo `d15565f136a26cf4e9d213533a40dd7d5eba8ad3f0654ab941ac00c4911c5dc5`, C/AOT `6d5f28516ad2e8fea5282643e67e4badef649c7abb031d004f78fb26e619fa1e`.

Não houve flash ou validação em dispositivo. O recibo B novo mantém pendentes a inspeção de disassembly, checksum no ESP32-S3 e atestação independente do build; P3 permanece pendente. Esta revisão não fecha aceite físico nem a lacuna histórica R6.

## Inventário principal de evidências

- Relatório P0 original: `/tmp/wasmhome-p0-20261013-r2.md`.
- Bundle e verificação: `/tmp/wasmhome-p0-20261013-r2/source/`; SHA do bundle acima.
- Histórico Git: `/tmp/wasmhome-p0-20261013-r2/ct107/git/checkout-history.txt`.
- Logs R6/R7/R8/R9: `/tmp/wasmhome-p0-20261013-r2/ct107/logs/`.
- Manifests/recibos e build B: `/tmp/wasmhome-p0-20261013-r2/ct107/r6/`, `r7/`, `r8/`, `r9/`.
- Validação final B: `/tmp/wasmhome-p0-20261013-r2/ct107/r8/build-e1-interp-p0-validation.txt`.

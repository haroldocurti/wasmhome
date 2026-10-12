# Revisão adversarial das evidências de preparo CT107

Data: 2026-10-10
Escopo: leitura dos scripts, recibos e logs locais já adquiridos em `build-session-e1/`; nenhuma nova conexão SSH, instalação ou comando no CT. Projeto transferido: commit `187ff96279ed8e6d3da14b7663ce5b5e856dff49`, tree `70580c660a5642e9f7226c0d9813b805ee8b3c64`; o log de transferência registra bundle SHA-256 `6e551c247ab1e9549e1bf8eec9df32ec95d9adc3a638e180d63cfb6d2dadede3`.

## Evidências consistentes no snapshot revisado

- O recibo APT mostra 21 pacotes esperados, delta `changed` idêntico ao conjunto esperado, `missing_or_unexpected=[]`, retorno 0 e mesmo Python `3.13.5-2`/SHA antes e depois. SHA-256 de `apt-receipt.json`: `8946b2066f999d04933e4f2b65668c077e7bc34cf5b71c81dd0c41e7e78f0b2b`; do delta: `d48c05aea5dd6699a9c19321a4824d97368db475dfad49d190005961573964e4`.
- O bootstrap usa o wheel pip `26.2.1` com SHA declarado, venv em diretório de usuário e `usersite=false`; Python de sistema mantém versão/hash no recibo.
- Os inventários de resolução e de wheels contêm 57 distribuições cada, mesmas identidades e versões, sem SHA malformado. A única sdist é `esptool 4.12.0` (`d797f652...`); a wheel produzida está marcada local, `py3-none-any`/purelib e tem hash próprio. `core-freeze.txt` tem 58 linhas porque registra também o pip bootstrap. O log registra `pip check` sem problemas e os imports solicitados concluídos. Freeze SHA `b94ef372378057055e676639d74224c85820479787eb727204c3374784c1aac6`; resolução SHA `535820edadaa9a577df7483416b64a15183cd625b70b0d9ff5e273afb9cd69f0`.
- Os recibos registram WASI-SDK 27 e PowerShell 7.6.6 por URL/tamanho/SHA e suas validações observadas (`ldd`, versões, APIs). O recibo IDF fixa origem e commit `9ef24e3e2a2c96e720d83c574a3f8699177573da`, tree, submódulos, hash de `tools.json`, seis pacotes do IDF-tools e a constraint copiada com hash conferido. A resolução e instalação do core foram amarradas ao Python 3.13.5.

## Achado

### [P2] Dependência resolvida durante o build nativo não foi preservada/hashada

`build-native-ct107.log` informa que `dependencies.lock` não existia, que o reconfigure resolveu dependências e escreveu o lock, incluindo `espressif/onewire_bus (1.0.2)` e `idf (5.2.6)`. `main/idf_component.yml` fixa onewire_bus pela versão `1.0.2`; `.gitignore` exclui `dependencies.lock` e `managed_components/`. O script `build-native-ct107.py` salva comandos, mas a verificação final só confere `git rev-parse HEAD`; não preserva/hash do lock gerado, `.component_hash` ou inventário/hash da árvore `managed_components`. Assim, o bundle/commit revisado prova a árvore WasmHome transferida, mas não fecha a identidade do código externo que o configure efetivamente compilou. Antes de relatar o gate nativo como completo, guardar o lock resolvido e evidência hashável da fonte do componente (ou equivalente verificável), junto aos outputs finais. Não exijo assinatura nem segundo build independente.

## Limite do resultado

No snapshot lido, `build-native-ct107.log` ainda estava em `[984/994]`; `native-e1-build-check.json` não existia. Portanto a compatibilidade completa IDF/Python 3.13 e o gate nativo continuam pendentes; isso também não conclui a entrega #7. O warning de `idf_version.txt` é diagnóstico do ambiente, enquanto o log confirma Python 3.13.5 e compilador Xtensa GNU 13.2.0; não o tratei isoladamente como falha. Os recibos de bootstrap/core/IDF são snapshots de etapa com seus próprios campos `pending`, não resultados finais atuais.
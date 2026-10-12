# Revisão adversarial do instalador de dependências CT107

Data: 2026-10-10
Arquivos revisados somente por leitura:
- `build-session-e1/install-ct107-deps.py` — SHA-256 `0d3c4cb0ad5ca94aa18a9bca08d2211270cb360eb1d31ca012cd170b2d2a863f`
- `build-session-e1/ct107-dependency-pins.json` — SHA-256 `27e16a16bde3badb08000b942877d8817e77609b859dba6c7bdb6edd9ce0723c`

Não executei o instalador, SSH, apt nem qualquer mutação do CT.

## Resultado

O plano JSON contém 21 pacotes com versões exatas. O script verifica root/hostname, IP+MAC, margem de disco e Python 3.13.5-2 com hash do executável; simula apt com `--no-install-recommends --no-upgrade --no-remove`; rejeita remoções e exige exatamente os 21 nomes/versões e o resumo de zero upgrade/removal. O mesmo conjunto de opções é usado no apt real. O venv/pip foi informado pelo coordenador como bootstrap separado já testado, sem alterar Python do sistema.

A substituição planejada de `PLAN_INPUT` pelo objeto JSON literal dos pins foi compilada em memória com sucesso; não produzi nem executei o payload final. A igualdade dos pins transferidos precisa ser confirmada antes do envio.

## Achados

### [P2] O delta dpkg pós-instalação é registrado, mas não é validado

Linhas 48, 54–62: o arquivo `dpkg-after.txt` é salvo depois das verificações, mas não é comparado com `dpkg-before.txt`. O sucesso verifica versão/status somente dos 21 pacotes esperados e compara versão/hash de `/usr/bin/python3.13`; não exige que o conjunto final tenha exatamente os 21 novos pacotes e nenhuma outra diferença. Se apt retornar erro, a exceção na linha 54 ocorre antes de gravar snapshot dpkg/Python posterior, apesar de poder haver instalação parcial.

Para sustentar automaticamente a alegação “somente estes 21 novos pacotes”, comparar os dois snapshots antes de escrever `receipt.json`: adições devem ser exatamente `plan['packages']` nas versões pedidas, sem remoções ou alteração de versões/status dos já instalados; capturar estado final também no caminho de erro e nunca declarar sucesso se a comparação falhar. As flags e a simulação reduzem muito o risco operacional, mas não substituem essa pós-condição registrada.

### [P2 condicional] O plano não é autocontido nem vinculado ao pin file

Linha 22 aceita qualquer JSON em `sys.argv[1]`; sem argumento, `PLAN_INPUT` não está definido no arquivo original. As linhas 23 e 44 validam schema, quantidade e igualdade da simulação com o plano recebido, mas não comparam o mapa a `ct107-dependency-pins.json`. O campo `simulation_sha256` na linha 3 do pin file também não é lido pelo script.

A operação descrita resolve a referência vazia substituindo `PLAN_INPUT` pelo objeto JSON literal. Para essa operação única, não é um bloqueador se, antes de enviar, o payload final compilado e seu mapa `packages` forem comparados byte/objeto a este pin file e o SHA-256 final do script transmitido for preservado. Sem essa checagem de payload, um erro de cópia ou argumento alternativo pode selecionar outro conjunto válido de 21 pacotes, ainda passando as verificações internas.

O `simulation_sha256` não é necessário para a validação semântica atual (nomes, versões e contagem do solver são comparados), mas está como dado informativo, não como gate.

## Conclusão da revisão inicial (P2 resolvido no adendo abaixo)

Não encontrei defeito nas verificações de CT/MAC, pins de versão, simulação ou flags de proteção do apt. **Antes de tratar a execução como automaticamente comprovada**, feche a comparação pós-instalação do delta dpkg ou assuma revisão manual explícita dos snapshots antes de declarar sucesso. A substituição de `PLAN_INPUT` deve usar exatamente o objeto do arquivo acima e o payload transmitido precisa ser preservado/hashado. Nada foi instalado; não há resultado real do CT nesta revisão.

A comparação do formato de status é compatível com dpkg-query: `db:Status-Abbrev` é um campo de três caracteres, e `ii ` inclui o espaço final; o formato usado não pede newline. [Manual dpkg-query Debian](https://manpages.debian.org/trixie/dpkg/dpkg-query.1.en.html)

## Rechecagem do delta pós-instalação — 2026-10-10

O script revisado tem SHA-256 `ec4b72bf5c8bdd54b45e07ea082912d8218070ae822d501b0383b56ec90b5d5f`; o pin JSON permanece `27e16a16bde3badb08000b942877d8817e77609b859dba6c7bdb6edd9ce0723c`. A correção substitui o achado P2 anterior: `dpkg_snapshot()` captura `binary:Package`, versão e status antes/depois; o estado posterior e `delta-verification.json` são escritos imediatamente após o subprocesso do apt, inclusive quando retorna erro. O conjunto de registros alterados é comparado às identidades dos 21 pacotes pinned; depois, os nomes/versões/status esperados e a identidade do Python continuam sendo verificados antes do recibo de sucesso.

Não encontrei defeito nesse delta. Em uma falha normal do apt, o snapshot final e o delta ficam preservados antes da exceção. A limitação residual é a falha do próprio `dpkg-query` ao obter o estado final, que impede montar esse registro; não é motivo para presumir sucesso e não ocorreu nesta revisão. O payload final ainda precisa incorporar exatamente o objeto do pin JSON e ter seu hash registrado. Não executei o script, apt, SSH ou CT; nenhuma instalação foi feita.
# Revisão focal de publicação e retomada — 11/10/2026

**Resultado: sem bloqueadores para publicar o handoff e o arquivo de evidências.**

Escopo somente leitura: `docs/retomada-linux.md`, checklist e propostas de toolchain,
recibo de preparo e complemento ELF R4 do CT107, README e índices do arquivo de
retomada, README/inventário do doador, além dos status documentados das issues.
Esta é uma revisão focal da publicação e da preservação, não uma nova revisão
funcional do código nem uma nova execução de hardware ou infraestrutura.

O contexto Git observado foi `HEAD=8b372147e739e46c8952ab8c7907b6c76a76345b`
(tree `56a6f161277d16ba725a536b76230aecf1e120d6`) com
`MERGE_HEAD=187ff96279ed8e6d3da14b7663ce5b5e856dff49`; havia documentação pendente
de integrar. Esse pin identifica o estado verificado durante a revisão, não um
commit final das alterações documentais.

Conferi os 229 registros de `SHA256SUMS`: nenhum arquivo ausente ou com hash
divergente. Os 168 itens copiados pelo `source-map.json` totalizam 9.529.853
bytes; os hashes e tamanhos conferem tanto no arquivo quanto nas origens ainda
disponíveis. Os links relativos dos oito documentos focais resolveram sem
faltas. `donor/verify_preservation.py` retornou
`preservation_hashes_and_R3_index_verified`, com 57 arquivos de checksum e 48
originais copiados (203.919 bytes). Nenhum padrão de token de acesso, chave de
acesso AWS, bearer token ou bloco de chave privada foi encontrado no arquivo.

O registro separa claramente os estados: #6/#8/#10 integradas/fechadas; #7 e #9,
LLVM/wamrc real, geração e reprodução de A/B/C, e execução física continuam
pendentes. O build nativo CT107 é descrito como build, com disassembly conferido
no complemento R4; não é apresentado como checksum na placa. O README e o mapa
explicam a exclusão dos binários gerados, backup local e configuração local de
SSH. Os diretórios Windows são identificados como históricos. CT110 aparece
somente como proposta arquivada; não há alegação de criação, execução ou parada
de guests. A conciliação entre 90 tentativas de referência e 90 slots propostos
permanece explicitamente pendente, sem mudança silenciosa do denominador.

O recibo do doador limita a revisão R3 à janela de spawn do Runner e identifica
os testes de infraestrutura como mocks; não atribui aos testes prova de LLVM,
wamrc, ABI, AOT ou execução A/B/C. A perda do parecer intermediário R2 e o
caráter unsigned dos hashes também estão declarados. Não encontrei discrepância
material entre o handoff, os complementos e os limites da evidência preservada.

Não executei as suites principais ou de evidência nesta revisão focal. O
coordenador informou que a árvore de publicação passou 60 testes MSVC e 21
testes de evidência, sem skips, com logs locais; essa validação é distinta do
escopo desta conferência documental e de preservação.

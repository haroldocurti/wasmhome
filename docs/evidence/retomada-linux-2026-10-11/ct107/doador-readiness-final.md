# Proposta CT110: preparo local revisado, execução pendente

10/10/2026. Candidato `187ff96279ed8e6d3da14b7663ce5b5e856dff49`, tree
`70580c660a5642e9f7226c0d9813b805ee8b3c64`; nenhuma fonte rastreada foi alterada
pelas receitas privadas do doador.

- [x] Configuração concreta: CT110, Debian13.1, 2 CPUs, 8192MiB RAM, swap0,
  disco32GiB DevServer, DHCP/vmbr0, unprivileged, onboot0, estado final parado.
- [x] Fonte/template conferidos e identidade fixada; diretórios/logs exclusivos.
- [x] Supervisor e guard de host/CT implementados; sem create/start automático.
- [x] Fechar os dois achados R1: limpeza pós-spawn e whitelist de configuração.
- [x] Sol e coordenador executaram a mesma suíte Linux R3: 28 testes passaram.
- [x] Coordenador conferiu todos os 40 arquivos do índice R3, sem divergência.
- [x] Luna re-revisou o fechamento dos dois achados iniciais em R2.
- [x] Fechar no código e nos testes o P2 adicional da consolidação: cobertura
  antes do Popen e máscara temporária INT/TERM até registrar a referência do
  filho; teste real SIGINT/SIGTERM comprovou término do job e seu filho nested,
  preservando um processo independente e restaurando máscaras do pai/filho.
- [x] Receber os pareceres da revisão de requisitos/padrões R2 e da correção
  focal R3: os três achados concretos foram fechados nos escopos revisados.
- [ ] Definir a fonte de RAM e medir >=10GiB MemAvailable reais antes do CT.
- [ ] Obter autorização humana específica para o novo CT110 e qualquer outro
  guest que o usuário escolher interromper temporariamente.
- [ ] Criar/iniciar o novo CT, simular/pinar suas dependências e demonstrar a
  supervisão em operação real antes da carga LLVM.
- [ ] Compilar wamrc, verificar recibo/ABI no CT107 e produzir/reproduzir A/B/C.

Índice do pacote privado no worktree `build-session-e2/proposal-index.json`:
SHA-256 `1696cd5604c1fc86f9a688acb01df004012d774d419dec78185e9bc06fe73b79`.
R1/R2 preservados nos índices e logs anteriores. A conclusão consolidada R2 em
`revisao-ct110-supervisor-r2-consolidada.md`, SHA-256
`f813a80d207b0108dca2b0e08e7900309ac52c50c071bad46da8697917e68693`,
registra o P2 adicional. O parecer focal R3 em
`revisao-ct110-supervisor-r3.md`, SHA-256
`1c90927a2da041ac28a0c899bbcf9d6f6206908ba83c8b2345e7f902364c2775`,
conferiu a correção e seus novos hashes, sem achado residual nessa janela.
Isso não prova operação real do supervisor ou libera infraestrutura.
O parecer intermediário anterior à
consolidação foi sobrescrito e não teve seus bytes originais restaurados;
o texto reconstruído é explicitamente marcado como não original e não é usado
como prova ou parecer atual. Os recibos nativos e dados físicos não foram alterados.
R1 e seus achados/logs/índice foram preservados. A contagem final é 28 testes;
o P3 de contagem citado durante a revisão veio de snapshot anterior e foi
retirado após a conferência dos bytes congelados, sem alterar o pacote.

Snapshot inicial de capacidade: Proxmox com 2,15GiB disponíveis contra o gate
proposto de 10GiB, DevServer com189,54GiB livres. A leitura final somente leitura
em `proxmox-donor-final-resources.json` mostrou 2,41GiB disponíveis e 5,48GiB
de swap usada; CT110 continua sem configuração criada. O gate de RAM ainda
falha e precisa ser repetido perto da criação/início. Limites atribuídos às VMs não provam
memória recuperável. Nenhum outro guest foi interrompido, e CT110 não foi criado.
Os gates são estimativas de engenharia, não mínimos oficiais nem garantia de
que o LLVM caiba. A supervisão foi testada localmente, não em um CT110 real.

Build nativo no CT107 concluído e revisado conforme
`progresso-toolchain-ct107.md` e `revisao-native-ct107.md`. Cópia ELF completa
R4 e disassembly local coincidente registrados em `native-final-ct107/elf-r4/`.
Isso não comprova checksum físico ou B/C, nem aceita #7/#9. Nenhum push, merge,
deploy, flash ou nova coleta física foi executado nesta preparação.

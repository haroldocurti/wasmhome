# Revisão adversarial — proposta de doador CT110

Data: 2026-10-10  
Candidato consultado: 187ff96279ed8e6d3da14b7663ce5b5e856dff49, tree 70580c660a5642e9f7226c0d9813b805ee8b3c64.  
Escopo: build-session-e2/doador-proposta.md, ct110-plan.json, proposal-index.json, approval-template.json, scripts de guard/doador, preflights preservados e contratos docs/proposta-toolchain.md, docs/proposta-toolchain-decisoes.md, docs/provisionamento-lxc.md. Somente leitura; nenhum comando SSH, instalação ou ação Proxmox executado. Não revisei o build nativo/recibos da coleta de #7.

## Conclusão para a decisão de recurso

A configuração proposta (CT 110, 2 CPUs, 8192 MiB, swap 0, rootfs de 32 GiB em DevServer, unprivileged, onboot 0, um compile/link simultâneo) é uma configuração de engenharia plausível para a rota LLVM, com gates conservadores explicitamente tratados como proposta, não como mínimo oficial nem garantia de sucesso. A especificação documentada de LLVM recomenda build Release/Ninja e limitar o link em host de pouca memória; a proposta usa isso e reconhece que ainda não mediu o pico real do LLVM/Xtensa.

O bloqueio atual é recurso físico, não falta de disco. O snapshot Proxmox em build-session-e1/proxmox-donor-preflight-20261010.txt registra 16.631.762.944 bytes totais, 2.306.097.152 bytes MemAvailable (2,15 GiB) e 5.694.406.656 bytes de swap usados (5,30 GiB). O gate antes de criar e iniciar exige 10 GiB realmente disponíveis (8 GiB do CT mais margem de 2 GiB). Mesmo a leitura otimista de 2,15 GiB disponíveis mais ~2,67 GiB cobrados nos cgroups dos CTs listados fica muito abaixo do gate; a proposta corretamente não trata memory.current como memória recuperável. Os limites atribuídos às VMs não são consumo efetivo nem autorização para interrompê-las.

DevServer tinha 198.749.958 KiB livres, cerca de 189,54 GiB, acima do gate de 48 GiB (32 GiB para rootfs mais 16 GiB de reserva). Portanto o disco comporta o plano no snapshot. Isso não resolve a insuficiência de RAM.

WSL também não é uma alternativa pronta nesta amostra: o donor-preflight.json informa 13,87 GiB livres fisicamente em C:, abaixo do gate de 20 GiB; a rede WSL estava em networkingMode None, sem rota/DNS para GitHub. Os ~953 GiB livres dentro do filesystem virtual não substituem espaço físico. O gate de memória do Windows passava, mas disco e rede não.

## Gatilhos separados

- Capacidade real: CT110 não passa hoje o gate de RAM no host haroldo. O preflight precisa ser repetido próximo de qualquer criação/início; não se deve inferir folga a partir dos limites das VMs ou do espaço em disco.
- Autorização humana: ct110-plan.json está proposal_blocked_by_host_memory_not_authorized_to_create; approval-template.json está pendente e declara que não autoriza criar/iniciar CT110 nem executar o doador. Uma pergunta anterior sobre outro LXC não equivale a essa autorização.
- Supervisão técnica: a própria proposta declara que o supervisor externo de lease/renovação e interrupção controlada ainda precisa ser implementado e revisado antes do perfil lxc110. A disponibilidade de RAM e uma autorização posterior não dispensam esse gate.
- Adequação da configuração: os limites de memória/disco em si são coerentes com uma tentativa serial e cautelosa, mas continuam sem prova de que o build LLVM caiba ou termine. Não há base para reduzir o gate, reaproveitar o limite de CT107, parar serviços ou mudar swap automaticamente.

## Pendência do pacote ignorado

Na leitura desta rodada, proposal-index.json e donor-guard-tests.log eram anteriores aos scripts atuais. O índice lista donor_wamrc.py como SHA 2645c11c.../19.470 bytes e proxmox_donor_guard.py como 06057bf4.../7.254 bytes; os arquivos lidos tinham d9239d67.../19.791 bytes e c39d5a83.../7.522 bytes. Mtime dos scripts: 20:39:16Z; do índice: 20:35:32Z; do log dos dez testes: 20:34:56Z. O coordenador informou que Sol já foi acionado para congelar e repetir testes. Assim, os hashes e testes do índice/log antigos não cobrem os bytes atuais; é necessária nova conferência de índice e testes depois do freeze. Isso não invalida a pergunta de alto nível sobre a origem da RAM, mas impede tratar o pacote atual como scripts já testados/revisados para execução.

## Pergunta concreta recomendada

“Para continuar a preparação, você autoriza que eu avalie uma fonte de RAM no próprio haroldo (com impacto e guests nomeados antes de qualquer mudança), ou prefere que eu prepare uma proposta separada para outro host que satisfaça os gates? Hoje há 2,15 GiB disponíveis contra 10 GiB exigidos; nenhum guest será interrompido por inferência. A criação/início do CT110 continuaria dependendo de autorização própria e da implementação/revisão do supervisor técnico.”

Não recomendo pedir agora autorização para criar/iniciar CT110: o gate de memória falha, o supervisor ainda está pendente, e a alternativa WSL também falha disco/rede nesta amostra. Nenhuma dessas pendências é resolvida pela conclusão do build nativo em CT107.
# Conferência técnica da issue #6

10/10/2026. Conferência do coordenador contra os sete critérios atuais da
[issue #6](https://github.com/haroldocurti/wasmhome/issues/6). Todos têm evidência
documentada para revisão técnica; a reserva DHCP é confirmação humana, com
IP/MAC efetivos observados. Publicação, integração e aceite estão pendentes.
O [parecer adversarial](revisao-adversarial.md) registra os pins, correções e
limites da revisão Luna xhigh. Este documento não encerra a issue.

| Critério | Evidência e resultado | Limite |
|---|---|---|
| Capacidade, template/hash e configuração efetiva | [CT107 original](../lxc-107-2026-10-01.txt), [preflight da reconstrução](lxc-preflight.txt) e [criação CT109](lxc-rebuild-create.txt). Configuração reconfirmada antes de criar o CT separado. | Amostra de capacidade para sistema-base; não garante build LLVM. |
| LXC iniciado, sistema-base, DNS e rede para dependências | Registro CT107 original e [bootstrap CT109](lxc-rebuild-bootstrap.txt), com [identidade conferida](lxc-rebuild-identity.txt). | Não prova HTTPS/COSE de artefatos. |
| Acesso por chave, permissões e diretórios | Registro CT107 original e [SSH operacional CT109](lxc-rebuild-ssh-after-reboot.txt). Sem credenciais publicadas. | Acesso operacional não autoriza alterações do Proxmox. |
| Reboot e persistência | Registro CT107 original; no CT109, [antes](lxc-rebuild-before-reboot.txt), [reboot gracioso](lxc-rebuild-after-reboot.txt) e SSH/persistência posterior. | Foram preservadas as divergências do instrumento e do ifupdown2; não houve mudança de outros guests. |
| Caminhos LXC, ESP32-S3 e PC | [Plataforma PC↔ESP32](platform/summary.json), [positivo ESP32→CT107](lxc-positive-r2/summary.json), [log do leitor](lxc-positive-r2-server.jsonl) e [derivação verificável](pair-verification.json). TCP/HTTP com nonce pareado e UDP no mesmo boot; origem divergente recusada no negativo. | Primeira captura permanece inconclusiva. O negativo não tem nonce pareável. Não é prova dos serviços definitivos. |
| Endpoint estável | Usuário confirmou em 09/10 reserva DHCP de `192.168.0.138` para `BC:24:11:B6:36:F7`; IP/MAC efetivos foram conferidos na sessão. | Confirmação humana, sem inspeção do painel DHCP. Reconfirmar antes de #15. |
| Reconstrução em CT separado | CT109 `wasmhome-bench-rebuild`, IP `192.168.0.171` e MAC `BC:24:11:73:7F:32`, com configuração autorizada. [Registro completo](../../proposta-reconstrucao-lxc.md) e [estado final](lxc-rebuild-final.txt). | CT109 ficou parado; não usou o endereço reservado do CT107 nem o CT108. |

O [encerramento do instrumento](README.md) comprova remoção do leitor,
ausência de listener18080, restauração do app original e diagnóstico posterior.
Em 10/10, consulta SSH somente leitura reconfirmou CT107 `running` e CT109
`stopped`. Nenhum novo flash ou reboot foi executado nessa consulta.

A [validação local](validacao-local.md) tem 68 testes sem skips e verificadores
offline aprovados. CI não está configurada. A #7 continua pendente, assim como
execução Wasm/AOT, serviços, atualizações e campanhas.

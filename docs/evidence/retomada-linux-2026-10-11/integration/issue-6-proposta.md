## Parent

[Obter resultados da campanha WasmHome em três cenários](https://github.com/haroldocurti/wasmhome/issues/1)

## What to build

Disponibilizar o sistema-base reproduzível do servidor da bancada. Toolchain e builds pertencem à #7; assinatura e distribuição HTTPS pertencem à #15.

## Acceptance criteria

- [x] Revalidar capacidade do host e registrar template/hash, CTID, sistema, CPU/RAM/swap, disco/storage, rede e configuração efetiva.
- [x] Criar e iniciar o LXC; comprovar sistema-base, DNS e acesso à rede necessário para obter dependências.
- [x] Configurar e testar acesso operacional por chave, permissões e diretórios de trabalho, sem publicar credenciais.
- [x] Reiniciar somente o novo container e comprovar persistência de configuração, acesso e arquivo de teste.
- [x] Comprovar os caminhos entre LXC, ESP32-S3 e PC observador, registrando endpoints, protocolos, direções e resultados.
- [x] Estabilizar o endpoint do servidor por reserva DHCP ou configuração de rede aprovada, antes da configuração HTTPS da #15.
- [x] Documentar reconstrução e demonstrá-la em CTID separado, sem sobrescrever o container de trabalho ou reutilizar seu endereço reservado.

## Escopo dos testes de rede

Usar ferramentas de diagnóstico ou firmware mínimo com fonte e versão preservadas. Testar pedido UDP observador → dispositivo, mensagem iniciada dispositivo → observador e acesso dispositivo → LXC em endpoint temporário identificado. Ping ou SSH ao host não substituem esses testes. A implementação completa do UDP embarcado é da #8 e o observador calibrado é da #10; HTTPS/COSE é da #15. Manter o aceite pendente se o hardware não estiver disponível.

## Configuração efetiva em 01/10/2026

CT 107 `wasmhome-bench`, Debian 13.1 amd64, não privilegiado, `nesting=1`, 1 núcleo/limite 1 CPU, RAM 1024 MiB, swap 0, disco 24 GiB em `local-lvm`, `vmbr0`, DHCP `192.168.0.138`, MAC `BC:24:11:B6:36:F7`, `onboot=0`. Acesso como `wasmhome` por chave; administração via `pct exec` no Proxmox. O usuário confirmou este PC como observador, na mesma rede local do ESP32-S3.

Em 01/10, o acesso do PC à sub-rede Proxmox por Tailscale não provava comunicação do ESP32. Na sessão física de 09/10, o observador usou Ethernet2, IPv4 `192.168.0.190`, índice 11; o ESP32 usou `192.168.0.145`. O diagnóstico UDP e o pedido TCP/HTTP ESP32→CT107 foram registrados separadamente.

## Blocked by

- [Inventariar e fixar a bancada física](https://github.com/haroldocurti/wasmhome/issues/2), concluída.

## Estado

Sistema-base e critérios técnicos concluídos e integrados pela [PR #29](https://github.com/haroldocurti/wasmhome/pull/29), revisão 8b372147e739e46c8952ab8c7907b6c76a76345b. [Registro da sessão](https://github.com/haroldocurti/wasmhome/blob/8b372147e739e46c8952ab8c7907b6c76a76345b/docs/evidence/execucao-2026-10-09/README.md) e [reconstrução](https://github.com/haroldocurti/wasmhome/blob/8b372147e739e46c8952ab8c7907b6c76a76345b/docs/proposta-reconstrucao-lxc.md).

PC↔ESP32 passou no diagnóstico UDP. O positivo aceito ESP32→CT107 usou TCP/HTTP em `192.168.0.138:18080`, nonce pareado e continuidade UDP no mesmo boot; uma origem divergente foi recusada. A primeira captura com empate de timestamps permanece inconclusiva. A reserva DHCP de `.138` para `BC:24:11:B6:36:F7` foi confirmada pelo usuário, sem inspeção do painel DHCP.

O CT109 separado foi criado com a configuração autorizada (Debian13.1, 1CPU, RAM1024MiB, swap0, disco24GiB DevServer, DHCP e hostname `wasmhome-bench-rebuild`). SSH, permissões e persistência após reboot passaram; o CT ficou parado. O firmware original foi restaurado e verificado; leitor temporário removido e porta18080 sem listener. Em 10/10, uma consulta somente leitura reconfirmou CT107 running e CT109 stopped.

Revisão adversarial Luna xhigh e 68 testes locais sem skips registrados. CI não está configurada. A #7 continua aberta: instalação/toolchain, builds Wasm/AOT e checksum nos três cenários não são critérios concluídos por esta issue. HTTPS/COSE e campanhas permanecem nos tickets próprios.

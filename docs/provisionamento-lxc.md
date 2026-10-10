# Provisionamento do servidor da bancada

Status em 09/10/2026: CT 107 criado em 01/10 com autorização do usuário.
Sistema-base, acesso por chave, persistência, transporte diagnóstico com o
ESP32 e reconstrução separada no CT 109 comprovados localmente. O complemento
ao final registra as evidências novas. Capacidade e configuração de rede devem
ser reconfirmadas antes de ampliar o ambiente; publicação e aceite formal
permanecem etapas próprias.

## Inspeção de 01/10/2026

Leitura via SSH ao host `haroldo`, entre 20:51 e 20:53, horário de São Paulo.
Revisão local consultada: `b14b9a2129075e7665f400f7a54b1e2e2a14da6d`.

| Item | Resultado observado |
|---|---|
| Proxmox | 9.1.6, kernel 6.17.13-1-pve |
| Memória | 15.861 MiB totais; 2.096 MiB disponíveis na amostra |
| Swap do host | 8.191 MiB totais; 6.866 MiB usados |
| Pressão de memória | PSI some/full com médias de 10, 60 e 300 segundos em zero |
| Atividade de swap | Quatro intervalos de 1 segundo de vmstat sem entrada/saída de swap; a primeira linha é média desde o boot |
| Storage local-lvm | rootdir habilitado; cerca de 47,1 GiB disponíveis, dados 63,85%, metadados 3,30% |
| Storage DevServer | rootdir habilitado; cerca de 191,5 GiB disponíveis, dados 12,56%, metadados 1,15% |
| Storage Webserver | rootdir habilitado; cerca de 150,9 GiB disponíveis, dados 31,10%, metadados 1,74% |
| Storage local | Templates e backups; não anuncia rootdir |
| Template existente | local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst |
| SHA-256 do template | ca6dffb91c3239fedc30af2c11389198140de1f1fc3d58082f3d9f4239b92e1b |
| Próximo ID indicado | 107, ainda não reservado |
| Rede do host | vmbr0, 192.168.0.99/24, gateway 192.168.0.1 |
| Firewall Proxmox | pve-firewall reportou disabled/running; não havia arquivos de regras em /etc/pve/firewall |

A ocupação de swap não demonstra, isoladamente, pressão ativa. As amostras
curtas também não comprovam folga para compilar LLVM/wamrc. Dimensionar o
provisionamento básico separadamente da toolchain da issue #7.

## Topologia e pendências

Este PC tinha IP Wi-Fi `192.168.63.79`. A rota para `192.168.0.99` usa Tailscale,
pela rota de sub-rede `192.168.0.0/24`. No Proxmox, a rota de retorno para
`192.168.63.79` aponta ao gateway `192.168.0.1`.

O usuário confirmou nesta sessão que este PC será o observador e ficará na
mesma rede local do ESP32-S3. Isso fixa a escolha da máquina; ainda falta
registrar o IP do ESP32 e testar seu caminho até o LXC. A leitura acima
é a topologia atual do PC, não uma prova de associação do dispositivo.

O acesso SSH comprovado ao host pode envolver tradução de endereço pelo
roteador Tailscale. Ele não demonstra que o LXC ou o ESP32 podem iniciar
datagramas UDP para o endereço Wi-Fi deste PC. Não usar esse acesso como aceite
da rede experimental.

Antes de fechar o aceite de conectividade:

- Reconfirmar o endereço deste PC observador na rede experimental.
- Confirmar IP e associação Wi-Fi do ESP32-S3 por diagnóstico do dispositivo.
- Preferir observador e ESP32 na LAN da bancada. Se usar roteamento entre redes,
  registrar rotas, tradução de endereços e testar as direções efetivas.
- Fixar endereço/hostname do servidor antes de configurar o HTTPS da issue #15.
  DHCP inicial serve para provisionamento; uma reserva ou endereço aprovado deve
  estabilizar o endpoint. Não escolher IP estático só porque não responde a ping.

## Configuração efetiva

| Parâmetro | Configuração |
|---|---|
| CTID | 107 |
| Nome | wasmhome-bench |
| Sistema | Debian 13 amd64, template e hash registrados acima |
| Isolamento | Container não privilegiado; nesting=1 para systemd 257; sem passthrough |
| CPU | 1 núcleo e limite de 1 CPU |
| RAM e swap | 1.024 MiB de RAM; swap do container em zero |
| Disco | 24 GiB no local-lvm |
| Rede inicial | eth0 em vmbr0, IPv4 por DHCP, IPv6 desabilitado no sistema-base |
| Inicialização automática | Desabilitada enquanto o dimensionamento estiver em avaliação |
| Acesso inicial | Console Proxmox ou pct exec no host |
| Acesso SSH | Chave pública aprovada e usuário operacional, configurados e testados antes do aceite |

Essa configuração permite preparar o sistema-base com consumo limitado. Não é uma
promessa de capacidade para build. Se a issue #7 exigir expansão, registrar a
nova configuração e reavaliar a capacidade do host antes de aplicá-la. Não
desligar nem redimensionar guests existentes como consequência automática.

## Pré-verificação e criação

Executar no host Proxmox. A criação exige aprovação da configuração proposta.
O CTID 107 agora está ocupado pelo servidor criado. Para reconstrução, escolher
outro ID disponível. Interromper se o ID escolhido estiver ocupado, o hash divergir, o storage não estiver
ativo ou a capacidade tiver piorado. Os comandos foram conferidos contra
`pct help create` instalado no Proxmox 9.1.6. A criação inicial sem nesting foi
executada e corrigida após diagnóstico de falhas de credenciais e mounts.

```sh
pveversion
free -m
vmstat 1 5
cat /proc/pressure/memory
pvesm status
lvs -o vg_name,lv_name,lv_size,data_percent,metadata_percent
pct list
qm list
pvesh get /cluster/nextid
sha256sum /var/lib/vz/template/cache/debian-13-standard_13.1-2_amd64.tar.zst
```

Com as verificações satisfeitas e a criação autorizada:

```sh
pct create 107 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname wasmhome-bench --ostype debian --arch amd64 \
  --unprivileged 1 --features nesting=1 --cores 1 --cpulimit 1 --memory 1024 --swap 0 \
  --rootfs local-lvm:24 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,ip6=manual,firewall=1 \
  --onboot 0 --start 0 --timezone America/Sao_Paulo
pct config 107
pct start 107
pct exec 107 -- cat /etc/os-release
pct exec 107 -- ip -brief address
pct exec 107 -- ip route
pct exec 107 -- cat /etc/resolv.conf
pct exec 107 -- df -h /
```

O indicador firewall=1 da interface não comprova proteção: o firewall do host
foi observado desabilitado. Registrar a política efetiva; não ativar regras
globais ou modificar o firewall de outros guests sem avaliar seu impacto.

## Acesso e estado inicial

O script [bootstrap-lxc-base.sh](../tools/bootstrap-lxc-base.sh) configura
usuário `wasmhome`, SSH por chave e diretórios. Executá-lo como root dentro do
container, fornecendo `WASMHOME_PUBLIC_KEY_BASE64` com a chave pública aprovada.
Ele atualiza índices apt e instala pacotes; as versões observadas devem ser
preservadas, pois os repositórios podem mudar. O script substitui o arquivo de
chaves do usuário dedicado; revisar a lista antes de reaplicá-lo se houver
outros operadores. Administração permanece via `pct exec` no host.

Preparar usuário operacional, chave SSH pública aprovada e diretórios separados
para código/builds, artefatos e logs. Registrar usuário, permissões, IP, DNS,
gateway e procedimento de acesso. Testar login por chave da máquina operacional.
Não registrar senhas, chaves privadas ou conteúdo de credenciais nas evidências.

Preservar configuração do CT, identificação do template, inventário de pacotes,
estado dos serviços e capacidade antes/depois da criação. Se pacotes adicionais
forem instalados, registrar versões e comandos. O template sozinho não reconstrói
as modificações posteriores.

## Critérios de aceite propostos para a issue #6

- [x] Revalidar capacidade do host e registrar template/hash, CTID, sistema,
      CPU/RAM/swap, disco/storage, rede e configuração efetiva do LXC.
- [x] Criar e iniciar o container; comprovar sistema-base, DNS e acesso à rede
      necessário para obter dependências.
- [x] Configurar e testar acesso operacional por chave, com permissões e
      diretórios de trabalho documentados, sem publicar credenciais.
- [x] Reiniciar somente o container criado e comprovar persistência da
      configuração, do acesso e de um arquivo de teste.
- [ ] Identificar observador e ESP32 e comprovar conectividade nos caminhos
      necessários, com endpoints, protocolos, direções e evidências registradas.
- [ ] Estabilizar o endpoint por reserva DHCP ou configuração aprovada antes
      de configurar HTTPS na issue #15.
- [ ] Documentar reconstrução e demonstrá-la em CTID separado, sem sobrescrever
      o container de trabalho ou reutilizar seu endereço reservado.

Para rede, usar ferramentas de diagnóstico ou firmware mínimo com fonte e
versão preservadas. Registrar um pedido UDP do observador ao dispositivo e uma
mensagem iniciada pelo dispositivo ao observador; testar acesso do dispositivo
ao LXC com um endpoint temporário identificado. O teste temporário não é aceite
de HTTPS/COSE nem comprovação de serviço funcional. Ping isolado não cobre esses
caminhos. Se o hardware não estiver disponível, deixar esse critério pendente.

A implementação completa do UDP embarcado permanece na issue #8, o observador
calibrado na #10 e o HTTPS com artefatos assinados na #15. A #6 não depende de
concluir essas implementações para realizar testes mínimos de transporte.

## Reconstrução e evidências

Usar o mesmo template verificado e repetir os passos registrados em CTID novo
e disponível, com identidade de rede própria. Reaplicar a configuração inicial
e os passos de acesso; repetir testes de sistema-base e persistência. Preservar
os resultados e eventuais divergências. Não incluir exclusão de guests no
procedimento de reconstrução. Limpeza do CT de teste é uma ação posterior.

Guardar evidências datadas de configuração, sistema-base, recursos, pacotes,
serviços, testes de rede, reinício e reconstrução. Distinguir resultado no host,
no container, no observador e no dispositivo. Finalizar com comentário de
resolução que vincule essas evidências, marcar apenas critérios comprovados e
atualizar o mapa da issue #1.

## Resultado do provisionamento

- CT 107 em execução, hostname `wasmhome-bench`, IPv4 DHCP `192.168.0.138`.
  MAC `BC:24:11:B6:36:F7`. A reserva DHCP não foi feita.
- O boot inicial sem nesting falhou em 17 unidades; journald apresentou
  `243/CREDENTIALS` e AppArmor negou mounts de credenciais. O shutdown gracioso
  falhou; somente o novo CT foi parado com `pct stop`. Após `nesting=1`, systemd
  reportou `running` e zero unidades em falha. Após o ajuste IPv4 descrito
  abaixo, o reboot gracioso também foi comprovado.
- O login como `wasmhome` com a chave pública deste PC funcionou antes e depois
  do reboot. A chave privada permaneceu no PC. A chave de host ED25519 foi
  obtida pelo canal SSH do Proxmox e fixada em arquivo known_hosts temporário
  local; fingerprint `SHA256:4kFD5riX3fxXmTohPAF20mgedsAvjOlLA9/jjY6TnHQ`.
- SSH efetivo: `PermitRootLogin no`, `PasswordAuthentication no`,
  `KbdInteractiveAuthentication no`, `PubkeyAuthentication yes`.
- `/srv/wasmhome/builds`, `artifacts` e `logs` pertencem ao usuário dedicado,
  modo 750. A escrita foi verificada após reboot.
- O boot ID mudou de `2fc5981b-e95d-4f03-8c1c-50fda0d8e18c` para
  `19ee6aa1-5c5b-474c-beee-e911e14466d7`; o arquivo
  `logs/persistence-check.txt` manteve `wasmhome-initial-state-v1`.
- DNS resolveu `deb.debian.org`; apt obteve índices/pacotes e curl recebeu
  HTTP/2 200 em `https://deb.debian.org/debian/` com verificação TLS padrão.
  Esse é HTTPS de saída, não o servidor HTTPS experimental da issue #15.
- Pacotes principais: openssh-server `1:10.0p1-7+deb13u4`, curl
  `8.14.1-2+deb13u5`, ca-certificates `20250419`. Locale ajustado a `C.UTF-8`.
- Após o primeiro reboot, networking.service permaneceu em inicialização por
  DHCPv6 disparado pela rede, apesar de IPv4 funcionar. IPv6 foi desabilitado
  somente no CT por `/etc/sysctl.d/90-wasmhome-ipv4.conf`; foi necessário parar
  e iniciar o CT para sair do job pendente. Em seguida, systemd reportou
  `running`, nenhum job pendente e zero unidades em falha. O bootstrap inclui
  essa configuração para reconstrução.
- Após o ajuste, o reboot gracioso mudou o boot ID de
  `c9c8f00b-4a75-40ec-aef4-993e2c6fc2ef` para
  `8f0f6e8c-a348-4804-996b-8c204eb1df3b`. Na verificação final às 21:10,
  SSH, arquivo persistente, `systemctl is-system-running` e HTTPS de saída
  passaram. SHA-256 da evidência: `d22a139f4a189fc31eaf78e68a26391035503df85f7a0d81e0972a091115afc8`.
- Depois do reboot, o host reportou 2.082 MiB disponíveis e 6.838 MiB de swap
  usados. Thin pool local-lvm: dados 64,86%, metadados 3,33%. A criação avisou
  que a soma dos discos virtuais, 146 GiB, excede o pool de 130,27 GiB, e que
  a expansão automática não está habilitada. Monitorar ocupação real antes de
  builds/expansões; não foi alterada a política global do pool.

Configuração, pacotes, rede e estado após reboot foram preservados em
[evidência do CT 107](evidence/lxc-107-2026-10-01.txt). Nenhum guest existente
foi parado ou redimensionado. Firewall global e roteamento não foram alterados.
No encerramento do registro de 01/10, o PC observador estava confirmado pelo
usuário, mas ainda faltavam teste a partir do ESP32, estabilização do endpoint
e demonstração de reconstrução em CT separado. Não houve instalação da
toolchain, build ou resultado experimental nessa etapa.

## Complemento de 09/10/2026

O [registro da sessão](evidence/execucao-2026-10-09/README.md) preserva a prova
diagnóstica PC↔ESP32 e ESP32→CT 107. O acesso iniciado pelo ESP32 usou TCP/HTTP
temporário em `192.168.0.138:18080`, com nonce e origem conferidos nos dois
lados. O negativo com origem esperada divergente produziu recusa no servidor
e `response_mismatch` no dispositivo, sem interromper o UDP no mesmo boot.
Isso comprova transporte; HTTPS/COSE e serviços da campanha têm critérios
próprios nas issues posteriores.

O usuário confirmou a reserva DHCP de `192.168.0.138` para a MAC
`BC:24:11:B6:36:F7`; o painel DHCP não foi inspecionado. A reconstrução
autorizada foi executada no CT 109, `wasmhome-bench-rebuild`, com IP distinto
`192.168.0.171`, 1 CPU, 1.024 MiB RAM, swap 0 e disco de 24 GiB em DevServer.
Sistema-base, SSH e persistência após reboot passaram. A
[proposta e o resultado](proposta-reconstrucao-lxc.md) registram a configuração,
os logs e as divergências do instrumento e do ifupdown2. O CT 109 ficou parado
e o CT 107 permaneceu em execução. Revisão final, publicação e aceite formal
da issue #6 seguem como etapas próprias. A toolchain no LXC pertence à #7.

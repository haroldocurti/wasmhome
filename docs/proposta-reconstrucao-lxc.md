# Reconstrução separada do servidor da bancada

Proposta preparada em 09/10/2026 para o critério restante da issue #6.
Configuração autorizada pelo usuário nesta sessão e criação executada. O CT 109
recebeu `192.168.0.171` e MAC `BC:24:11:73:7F:32`; bootstrap, reboot gracioso,
persistência e acesso operacional por chave passaram. O CT de teste foi parado
ao final, conforme autorizado. A configuração abaixo registra
a proposta aprovada. O CT 107 está operacional em `192.168.0.138`. O usuário
confirmou a reserva desse endereço para sua MAC; a sessão não consultou o painel DHCP.

## Configuração proposta

| Parâmetro | Valor |
|---|---|
| CTID | 109, livre na consulta de 09/10; reconfirmar imediatamente antes |
| Hostname | `wasmhome-bench-rebuild` |
| Sistema e template | Debian 13.1 amd64, `local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst` |
| SHA-256 do template | `ca6dffb91c3239fedc30af2c11389198140de1f1fc3d58082f3d9f4239b92e1b` |
| Isolamento | Não privilegiado, `nesting=1`, sem passthrough |
| CPU | 1 núcleo, limite de 1 CPU |
| RAM e swap | 1.024 MiB, swap 0 |
| Disco | 24 GiB em `DevServer` |
| Rede | `vmbr0`, IPv4 por DHCP, MAC própria gerada pelo Proxmox |
| Autostart | Desabilitado |
| Acesso | Usuário `wasmhome`, mesma chave pública local já aprovada para o CT 107 |
| Encerramento do ensaio | Parar o CT de teste após preservar a evidência; exclusão fora deste procedimento |

O storage difere do CT 107. A inspeção atual encontrou aproximadamente 191 GiB
livres em `DevServer`, e aproximadamente 45 GiB em `local-lvm`. Registrar essa
diferença na evidência de reconstrução. A amostra do host mostrou cerca de
1.511 MiB disponíveis, swap de 8.191 MiB com 4.081 MiB usados e PSI de memória
em zero. Isso autoriza avaliar o sistema-base; não comprova capacidade para
compilar LLVM ou dimensionar a toolchain.

## Procedimento após autorização

1. Reconfirmar capacidade, PSI, template/hash, storages, ID livre e ausência
   de locks. Interromper se houver pressão ativa ou deterioração da capacidade.
2. Criar somente o CT 109 com os parâmetros acima. Registrar `pct config 109`.
3. Iniciar o CT e confirmar hostname, MAC, lease DHCP e endereço próprios.
   Verificar ausência de conflito de IP com outros equipamentos ativos.
   Interromper
   antes do bootstrap se receber `192.168.0.138`, se o ID/hostname divergir
   ou se a identidade do CT de trabalho tiver mudado.
4. Executar a revisão registrada de `tools/bootstrap-lxc-base.sh` no CT novo,
   como root. Definir `WASMHOME_EXPECTED_HOSTNAME=wasmhome-bench-rebuild` e
   fornecer a chave pública aprovada em `WASMHOME_PUBLIC_KEY_BASE64`.
5. Registrar OS, pacotes, DNS, rota, acesso HTTPS de saída, permissões e serviços.
   Testar SSH por chave usando a identidade do host conferida pelo canal Proxmox.
6. Reiniciar somente o CT 109. Comprovar mudança do boot ID, persistência do
   arquivo de teste, SSH e serviços sem unidades em falha.
7. Preservar resultados e divergências. Parar o CT 109 e reconfirmar o estado
   do CT 107. Não instalar toolchain nesta prova do sistema-base.

Comando de criação a executar no host Proxmox, depois das verificações:

```sh
pct create 109 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
  --hostname wasmhome-bench-rebuild --ostype debian --arch amd64 \
  --unprivileged 1 --features nesting=1 --cores 1 --cpulimit 1 \
  --memory 1024 --swap 0 --rootfs DevServer:24 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp,ip6=manual,firewall=1 \
  --onboot 0 --start 0 --timezone America/Sao_Paulo
```

## Aceite e limite

Os [registros da criação](evidence/execucao-2026-10-09/lxc-rebuild-create.txt),
[bootstrap](evidence/execucao-2026-10-09/lxc-rebuild-bootstrap.txt),
[reboot gracioso](evidence/execucao-2026-10-09/lxc-rebuild-after-reboot.txt),
[SSH após reboot](evidence/execucao-2026-10-09/lxc-rebuild-ssh-after-reboot.txt) e
[estado final](evidence/execucao-2026-10-09/lxc-rebuild-final.txt) preservam os
resultados. O boot ID mudou de `47512c48-88a6-4882-b9cd-85db57f74f33` para
`a7d85f54-eaa0-4fc2-b0f8-a03d7daf8db7`, com arquivo persistente, serviços sem
unidades em falha, diretórios graváveis pelo operador e saída HTTPS. O CT 107
permaneceu em execução. O lease e a associação IP/MAC do CT 109 estão registrados.

O primeiro comando de criação terminou a criação, mas a inspeção final teve um
erro de fim de linha no argumento de `ip`. A consulta subsequente confirmou a
identidade, sem recriar o CT. Um comando auxiliar de IP no início do bootstrap
teve erro de quoting de `awk`; a leitura independente do IP já havia confirmado
`192.168.0.171`, e a [verificação explícita da identidade](evidence/execucao-2026-10-09/lxc-rebuild-identity.txt)
reconfirmou hostname, MAC e IP. Esses erros do instrumento foram preservados;
não são resultados de campanha. Após o bootstrap foi realizado um stop/start
do CT de teste, seguido pelo reboot gracioso e pelo aceite registrados acima.

A [inspeção de rede](evidence/execucao-2026-10-09/lxc-rebuild-network-diagnosis.txt)
registrou uma mensagem de ifupdown2 sobre tipo `bytes` no caminho IPv6, embora a
unidade tenha terminado com código zero, IPv6 esteja desabilitado e os testes
IPv4/SSH/HTTPS tenham passado. Preservar essa divergência na reprodução; o ensaio
não altera o pacote ifupdown2 nem a rede do host.

A prova é a reconstrução do sistema-base em identidade separada. A issue #6
também exige o caminho ESP32→LXC e estabilização do endpoint. Esses critérios
precisam de evidências próprias. HTTPS com CA, COSE e artefatos assinados
pertencem à issue #15. Não encerrar a issue #6 somente porque o CT novo iniciou.

# Doador LLVM/Xtensa: proposta para revisão

Preparação em 10/10/2026. O candidato `187ff96279ed8e6d3da14b7663ce5b5e856dff49`,
tree `70580c660a5642e9f7226c0d9813b805ee8b3c64`, passou pela re-revisão do runtime.
Os scripts desta pasta são propostas ignoradas, sem instalação, build ou ação
de infraestrutura executados. A pergunta sobre outro LXC ainda não autoriza criar,
iniciar ou parar guests.

## CT110 proposto

| Campo | Valor |
|---|---|
| ID / hostname | 110 / `wasmhome-toolchain-donor` |
| Sistema | Debian13.1, template `local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst` |
| CPU / RAM / swap | 2 CPU / 8192MiB / 0 |
| Disco | 32GiB em DevServer |
| Rede | eth0, DHCP, bridge vmbr0; MAC/IP registrados após criação |
| Isolamento | unprivileged1, sem nesting/features extra |
| Boot e estado final | onboot0; finalizar somente o novo CT110 parado, preservando outputs |
| Usuário do build | wasmhome, UID1000, sem chaves privadas do host |
| Paralelismo | uma compilação e um link por vez |

[ct110-plan.json](ct110-plan.json) fixa os campos e gates. O template documentado
em `docs/provisionamento-lxc.md` foi conferido no arquivo real do host `haroldo`:
129710398 bytes, SHA256
`ca6dffb91c3239fedc30af2c11389198140de1f1fc3d58082f3d9f4239b92e1b`.
O digest coincide com a prova histórica #6; é identidade do arquivo local,
sem alegar autenticação independente da origem upstream. `template-preflight.txt`
preserva stat/hash. Os guards before-create e before-start repetem essa conferência
e falham se o arquivo, tamanho ou digest mudar.

**O disco cabe, mas a RAM bloqueia a execução.** O snapshot do root aponta
DevServer livre189,54GiB e raiz local50,55GiB. O host tem15,49GiB totais, somente
**2,15GiB disponíveis** e5,30GiB de swap usados. O gate exige **10GiB realmente
disponíveis antes de criar/iniciar**: 8GiB para o doador +2GiB de margem.
Configurar um limite de8GiB não cria essa memória disponível.

CTs100/101/105/106/107/108 somam cerca2,67GiB em memory.current; CT109 está parado.
O valor inclui cache/cobrança de cgroup, não significa memória integralmente
recuperável nem autoriza interrupção. Mesmo a soma otimista com2,15GiB livres
não alcança8GiB+margem. VMs102 `hermes`,103 `Webserver` e104 `DevServer` estão
running, com limites6112/8096/8192MiB: limites não são consumo liberável medido.
A fonte real de RAM precisa ser decidida e autorizada separadamente, ou usar
outro host. Não parar Webserver ou qualquer serviço por inferência.

Fontes preservadas no checkout principal:
`build-session-e1/proxmox-donor-preflight-20261010.txt`,
`proxmox-guest-memory-preflight.json`, `proxmox-vm-donor-preflight.txt`.
A leitura complementar local está em `proxmox-guest-memory.txt`.

## Sequência e guards propostos

`proxmox_donor_guard.py` é somente leitura. Confere hostname haroldo, ID110 livre,
DevServer ativo com48GiB livres mínimos, memória real, configuração exata do
novoCT, MAC atribuída, CT109 parado e estados dos demais guests.
Aceita somente as chaves arch/cores/hostname/memory/net0/onboot/ostype/rootfs/
swap/unprivileged, com arch amd64. Net0 admite exclusivamente os campos fixados
e defaults veth/firewall0/link_up; rootfs é volume vm-110-disk-N próprio, size32G,
sem opções extra. Qualquer mpN, device, hook, lxc.*, net adicional, features,
unknown, opção de montagem ou tag/rate/IP extra falha. Esses defaults ainda
devem ser confrontados com o config real do futuroCT; divergência não autoriza
relaxar o guard ou mudar guests automaticamente.

1. Resolver a fonte de RAM sob autorização própria; registrar guard before-create,
   template/digest e revisão deste pacote. Reconfirmar ID110 livre imediatamente
   antes do write. Não usar force, substituir guest ou ampliar disco.
2. Se houver autorização explícita do novoCT, a configuração proposta é:

   ```sh
   pct create 110 local:vztmpl/debian-13-standard_13.1-2_amd64.tar.zst \
     --hostname wasmhome-toolchain-donor --cores 2 --memory 8192 --swap 0 \
     --rootfs DevServer:32 --unprivileged 1 --onboot 0 \
     --net0 name=eth0,bridge=vmbr0,ip=dhcp
   ```

   Esse bloco é uma proposta; não foi executado. Não inclui senha ou chave privada.
3. Guard before-start com baseline before-create exige CT110 stopped, configuração
   exata, host10GiB e outros guests iguais. Só então iniciar **110**, se autorizado.
   Verificar Debian13, usuário, filesystem e rede; simular o fechamento APT no
   novoCT antes de instalar. A simulação Ubuntu abaixo não vale para Debian13.
4. `supervise_ct110.py` implementa a renovação do lease a cada10s, pelo canal
   Proxmox confiável, com `ct110_supervisor_receiver.py` como endpoint UID1000
   no novoCT. O guard não altera nenhum guest. Falha para de renovar; lease
   ausente/stale>30s interrompe somente o build pertencente ao script. A integração
   foi verificada localmente por testes; ainda depende de revisão Luna e de
   prova operacional no futuroCT antes de tratar sua execução como comprovada.
5. Se um gate falhar, o script interrompe primeiro somente seu próprio process
   group. O guard não executa stop em nenhum CT/VM. Lease indisponível também
   interrompe o build, sem concluir que o host está seguro. Sob autorização do
   novoCT, o supervisor pede desligamento normal somente110 após conferir
   ID/hostname/configuration SHA; isso nunca autoriza tocar outros guests. Se o
   desligamento normal falhar, preservar estado/evidência e pedir intervenção,
   sem pct stop forçado, destroy ou mudança de recursos automática.
6. Preservar/verificar binário, recibo e logs. Sob autorização limitada ao novoCT,
   desligar normalmente somente110 e verificar guard final:110 stopped,109 stopped,
   configuração/outputs preservados e demais guests no estado anterior. Falha
   não autoriza parada forçada ou mudança de outro guest. Não destruir110.

Durante a execução, o host mantém2GiB disponíveis e crescimento de swap máximo
128MiB sobre o baseline. Dentro do CT de8GiB, exigir inicialmente6GiB disponíveis
(o SO também ocupa memória), reserva2GiB durante build, disco inicial20GiB e
reserva6GiB. São gates de engenharia, não mínimos oficiais ou garantia contra OOM.

## Build e recibo preparados

`donor_wamrc.py` oferece perfis wsl/lxc110; default é apenas preflight. Execute
exige revisão/recibo com hash correspondente, bundle Git completo da revisão
fixada e run nova em HOME Linux/ext4. `approval-template.json` continua pendente;
nunca equivale a autorização de infraestrutura.

O bundle futuro do candidato evita usar o gitdir Windows pelo Git Linux:

```powershell
git bundle create build-session-e2/wasmhome-187ff96.bundle codex/issue-7-runtime-port
```

O doador verifica commit/tree após checkout e registra hash do bundle. O acquirer
revisado materializa WAMR2.4.5/framework com origins/pins/limpeza. LLVM usa origem
`https://github.com/espressif/llvm-project.git`, SHA
`344b928e67bfc1d07cb150609c55548e86a9bf19`, com fetch exato e source limpo,
incluindo untracked/ignored. Diretórios llvm-build/wamrc-build precisam ser novos;
cache real é confrontado antes do build. Não reutilizar clone/build incompleto.

Release/Ninja, targets X86 + experimentalXtensa, projetos/runtimes extra vazios,
tools/tests/examples/bench/docs/bindings OFF, bibliotecas opcionais OFF, dylib/LTO
OFF; compile_commands ON, ferramentas com caminhos absolutos, compile/link jobs1
e cmake--build--parallel1. TableGen necessário ao LLVM pode compilar. Os flags são
suportados pelo [CMake no SHA fixado](https://github.com/espressif/llvm-project/blob/344b928e67bfc1d07cb150609c55548e86a9bf19/llvm/CMakeLists.txt)
e pelo [módulo de jobs/LTO](https://github.com/espressif/llvm-project/blob/344b928e67bfc1d07cb150609c55548e86a9bf19/llvm/cmake/modules/HandleLLVMOptions.cmake).
O [wamrc2.4.5](https://github.com/bytecodealliance/wasm-micro-runtime/blob/25bd7eb63e828e4bd242cc9b38d260b4b31c6605/wamr-compiler/CMakeLists.txt)
liga LLVM_AVAILABLE_LIBS sem dylib; não basta construir libLLVMCore.a isolada.

O script chama o `tools/record_wamrc_build.py` revisado e preserva argv completos,
caches/compile_commands, tools/libs/binaryhash, help/ldd, logs e resources.jsonl.
O recibo segue unsigned/dependente do doador. Conferência separada, ABI no CT107,
geração B/C e provas físicas continuam gates próprios. Não aceitar #7/#9 pelo build.

O monitor interrompe somente seu process group: SIGINT/15s, SIGTERM/10s e
SIGKILL/5s como último recurso de seus próprios processos. Não reinicia jobs,
expande recursos ou limpa arquivos. Um pico entre amostras pode causar OOM.

## WSL amostrado e limites da preparação

Ubuntu24.04.1/x86_64, UID1000, Python3.12.3,16 CPUs, memória disponível aprox13GiB,
swap0 usada, raiz virtual aprox953GiB. WindowsC: tinha **13,91GiB físicos livres**
na amostra20:22Z; a reconfirmação20:35Z deu13,87GiB físicos e8,33GiB de RAM
disponível no Windows. Espaço virtual do VHDX não substitui
disco físico. O gate20GiB físicos falha; não reduzi-lo automaticamente sem medir
o consumo real deste perfil. `donor-preflight.json` contém a leitura estruturada.

Git/python/tar/xz/curl/ar/ranlib existem; gcc/g++/cmake/ninja faltam. A simulação
gcc/g++/cmake/ninja-build mostrou **39 novos, zero upgrades/removes**; build-essential
elevava a45 novos. Nada instalado. WSL apresentou mirrored0x8007054f e fallback
networkingModeNone, sem rota default/resolução github.com. Não reparar rede,
.wslconfig, adaptadores ou firewall por esta receita. Uma rota offline completa
exigiria pacote de fontes/objetos e revisão separados; não foi implementada.

Os 28 testes de guards/supervisor passaram no Python WSL, sem toolchain/build ou
provisioning. CT110 ainda não existe, seu perfil não foi executado, e a árvore
Git rastreada permanece limpa. Hashes em proposal-index.json. Propostas/scripts
ainda dependem de revisão Luna, definição real de RAM e autorização do novoCT.

Os testes verificam: disco físico apesar do espaço virtual; swap do CT; gate
de RAM host apesar do disco; rejeição de ID110 existente; CT109 parado; sinal
restrito ao process group criado; lease stale; digest do template incorreto;
estado final110 stopped preservando os demais; JSON de aprovação com duplicadas;
supervisor sem autorização; transporte SSH/JSON LF; interrupção seguida de
shutdown normal único; falha de shutdown sem retry/force; PID reutilizado.
Um teste Linux sinaliza um Python descartável em process group próprio e
comprova que outro processo descartável continua vivo. Os fluxos SSH/lease e
shutdown usam mocks: não provam compilação, consumo máximo nem operação remota.
A tentativa inicial de rodar os dez testes Linux no Python Windows falhou em
dois por APIs Linux; a suíte R3 final28/28 foi executada no Linux. Logs/índices
R1 e R2 são preservados, e os logs/recibo R3 identificam a revisão atual.

## Supervisor externo: reprodução e limites

O default de `supervise_ct110.py` imprime a proposta e não faz SSH. O modo
execute exige a confirmação explícita do ciclo de vida do novoCT e inputs
concretos. Ele não cria/inicia o guest, não instala dependências e não transfere
a fonte/bundle: esses passos pertencem à preparação autorizada do futuroCT.
Para revisão local da versão congelada:

```powershell
python -B build-session-e2/supervise_ct110.py
wsl -d Ubuntu -- python3 -B -m unittest discover -s /mnt/c/Users/inomi/.codex/worktrees/issue7-runtime-port/wasmhome/build-session-e2 -p 'test_*.py' -v
```

Após autorização dos recursos, RAM real10GiB, criação/start separados e revisão
destes scripts, a interface concreta proposta é:

```powershell
python -B build-session-e2/supervise_ct110.py --execute --authorized-ct110 `
  --baseline build-session-e2/ct110-before-start.json `
  --log build-session-e2/ct110-supervision-run-001 `
  --control /home/wasmhome/wasmhome-control-e1 `
  --builder /home/wasmhome/toolchain-proposal/donor_wamrc.py `
  --builder-sha256 <SHA256-donor_wamrc.py-do-proposal-index.json> `
  --run /home/wasmhome/wasmhome-donor-e1 `
  --source-bundle /home/wasmhome/toolchain-proposal/wasmhome-187ff96.bundle `
  --review-approval /home/wasmhome/toolchain-proposal/donor-review-approval.json `
  --review-receipt /home/wasmhome/toolchain-proposal/donor-review.md
```

O baseline before-start deve ser guard real preservado, com configuração/MAC
do novoCT e demais guests, e não JSON preenchido manualmente para passar o gate.
O `approval-template.json` continua pendente e não passa o gate do builder.
Os paths de fonte/bundle/recibo acima são destinos propostos; nenhum arquivo
foi transferido ao host/CT por esta preparação. Um known-hosts dedicado existente
pode ser fornecido via `--known-hosts`; sem isso, OpenSSH usa sua base confiável
normal, sempre BatchMode/StrictHostKeyChecking=yes, sem aprender chave nova.

Windows Python usa subprocess argv e um único comando remoto com quoting POSIX;
JSON stdin em bytes UTF8/LF evita o CR de pipelines PowerShell. O host executa o
guard em memória e o endpoint apenas via pct exec110/runuser wasmhome. Lease é
gravado por substituição atômica no controle novo, UID1000, sem symlinks.
Supervisor/script/endpoint e bundle/recibos têm hashes preservados em logs.

O endpoint cobre imediatamente todas as operações após Popen com cleanup,
inclusive leitura da identidade e gravação de process.json. Um pidfd prende
a referência de kernel do filho criado. UID1000/PGID próprio/start_ticks
confirmados permitem sinalizar seu grupo; identidade indisponível permite só
o líder pinado, sem inferir um grupo por PID. Se o próprio pidfd falhar, waitid
WNOWAIT confirma o filho ainda não reapado, no endpoint single-threaded sem
reaper concorrente, antes de sinalizar apenas ele. Processo já encerrado é
reapado sem sinal. Cleanup pede SIGINT/45s, depois TERM/45s e KILL/5s somente
nesse alvo próprio se necessário; isso não adiciona force/stop de CT.
Uma falha de disco pode impedir spawn-cleanup.json, mas a tentativa de escrever
esse recibo ocorre depois do cleanup. Os quatro testes R2 usam processos Linux
descartáveis reais para falha atomic_json, identidade, pidfd e filho short-lived;
em todos, outro processo independente permanece vivo.

O Runner cobre também a janela assíncrona de seu próprio spawn. O try começa
antes de Popen; INT/TERM são bloqueados até a referência do filho ser atribuída.
Restaurar a máscara no pai ocorre dentro do try de cleanup, portanto um sinal
pendente chama interrupt com o filho já conhecido. O filho restaura a máscara
original antes do exec e não herda esse bloqueio. Como preexec_fn pressupõe
processo single-threaded, o coordenador existente confere main thread e um único
task em /proc/self/task antes do spawn; não cria threads. Não é um novo gate de
aprovação, e falha dessa premissa encerra antes de criar o job.
Dois testes R3 enviam INT/TERM reais entre o retorno interno de Popen e a
atribuição externa da referência, com job e filho nested já iniciados. Comprovam
encerramento dos dois, processo independente vivo e máscaras restauradas.

Ao falhar gate/SSH/lease, SIGINT só alcança o líder identificado por PID, PGID,
UID, start_ticks e argv. O Runner responde interrompendo seu próprio grupo de
compilação/link. O supervisor espera45s e registra interrupção não confirmada
se necessário. Em seguida, com identidade110/config e demais estados ainda
iguais, pede `pct shutdown 110 --timeout 60`; verifica final stopped. Não chama
stop/force/destroy, não encerra outros guests e não mata SSH supondo que isso
encerre o compilador remoto. Se identidade/estados ou shutdown falharem, preserva
os logs e requer intervenção humana; não declara estado final comprovado.

Tempos10s/30s são target/prazo: latência da leitura/SSH reduz a margem, e o
builder também para ao detectar lease stale. O host pode sofrer OOM entre
amostras. Partição SSH pode impedir SIGINT e shutdown; lease stale protege o
builder cooperante enquanto ele ainda executar. Recibo/lease são unsigned e
confiam no canal Proxmox, nos arquivos revisados e no usuário do doador. Nenhuma
assinatura nova ou dupla compilação é imposta. Prova real dessa integração,
ABI do binário transferido e execução B/C permanecem futuras, sem aceite #7/#9.
Cleanup não garante resposta de processo em estado de kernel não interrompível,
nem resolve falha de shutdown/host/SSH. Nesses casos os logs preservam a falha,
sem declarar que todos os processos ou o novoCT foram parados. Se outro guest
mudar estado ou o config110 mudar, o identity guard também impede shutdown
automático; não restaura estados de guests por conta própria.

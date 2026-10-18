# Observador da #20 no CT107, sem privilégios administrativos

O laptop conserva flash, restauração da baseline e captura UART. O CT107 executa
o observador UDP como `wasmhome`, com Python padrão, e pode hospedar uma cópia
do servidor HTTPS como processo desse usuário. Os listeners atuais em 8443 e
8444 pertencem a serviços do sistema (UID 999) e devem permanecer intactos; o
preparo observado encontrou 8446 livre para o novo processo. Estagiar pacote,
certificado e chave TLS sob o home de `wasmhome`. O laptop inicia conexões SSH
de saída; a telemetria da placa passa diretamente ao CT107. Não é necessário
abrir UDP/3333 no firewall do laptop. Este backend não instala pacotes, muda
firewall, inicia containers ou altera firmware, baseline e chaves autorizadas.

O acesso SSH estrito já foi conferido no CT como `wasmhome` (UID 1000), sem
`sudo`. A chave dedicada e o arquivo `known_hosts` estão provisionados nos
caminhos locais abaixo para uso unattended pelo runner. Os testes locais do
runner não acessam serviços nem hardware. A integração física e a recuperação
após falhas ainda precisam de validação antes de uma campanha válida.

## Identidade provisionada e preflight

O endpoint LAN confirmado nesta sessão é `192.168.0.138/24`, hostname
`wasmhome-bench`, usuário `wasmhome`, UID 1000, Python 3.13.5 e SSH/22.
Reconfirmar esses valores antes de cada nova sessão. O endereço
`100.89.142.40`, informado para Proxmox/Tailscale, **não é** o endereço do
observador a compilar no ESP. A opção `--bind-ip` precisa ser fornecida
explicitamente com o IPv4 LAN do CT107; o worker rejeita endereço indefinido,
loopback, multicast e a faixa de overlay `100.64.0.0/10`. O IP do ESP é outro
valor, obtido do inventário/captura já existente ou da concessão DHCP.

O acesso unattended usa uma chave SSH dedicada **já provisionada**. A identidade
privada local é `/home/haroldocurti/.ssh/wasmhome-ct107-hil` (modo 0600); sua
chave pública está instalada na conta `wasmhome` do CT, UID 1000, com a opção
`restrict`. A chave do host CT107, verificada via Proxmox, está em
`/home/haroldocurti/.ssh/wasmhome-ct107_known_hosts` (modo 0600). A conexão
estrita já foi confirmada como `wasmhome`, sem `sudo`; não gere outra chave nem
reinstale a existente.

Use estes caminhos nos comandos locais:

```bash
HIL_KEY=/home/haroldocurti/.ssh/wasmhome-ct107-hil
HIL_KNOWN_HOSTS=/home/haroldocurti/.ssh/wasmhome-ct107_known_hosts
```

O cliente usa `BatchMode=yes`, `StrictHostKeyChecking=yes`, identidade e
`known_hosts` explícitos, `IdentityAgent=none` e nenhuma senha ou host global.
Não aceitar automaticamente uma nova chave de servidor nem confundir a chave
do CT com a do Proxmox; mudança de fingerprint precisa ser reconciliada.

O diretório padrão remoto é `~/.local/share/wasmhome-hil`, privado, modo 0700,
pertencente a `wasmhome`. Outro caminho pode ser escolhido com `--remote-root`,
mas precisa permanecer dentro do home do usuário e sem links simbólicos. A
conta deve ter espaço para logs e os arquivos da sessão. Python 3.10+ e um
Linux com `/proc`/`fcntl` são necessários no CT; para HTTPS, também OpenSSL.
O laptop precisa das dependências de captura serial já usadas pelo runner.

## Verificação anterior, somente leitura

Esta conferência opcional reconfirma identidade, endereços, rota e espaço. Não
abre serial, não envia UDP à placa e não inicia o observador ou o servidor. A
conexão SSH estrita já foi verificada; executá-la novamente não é requisito para
usar a identidade provisionada.

```bash
HIL_DEVICE_IP=IPV4_LAN_CONFIRMADO_DO_ESP
HIL_SSH=(-T -o BatchMode=yes -o StrictHostKeyChecking=yes
  -o "UserKnownHostsFile=$HIL_KNOWN_HOSTS" -o GlobalKnownHostsFile=/dev/null
  -o IdentitiesOnly=yes -o IdentityAgent=none -i "$HIL_KEY")
ssh "${HIL_SSH[@]}" wasmhome@192.168.0.138 "python3 - '$HIL_DEVICE_IP'" <<'PY'
import ipaddress, json, os, pathlib, pwd, shutil, socket, subprocess, sys
device = str(ipaddress.IPv4Address(sys.argv[1]))
assert os.geteuid() == 1000
assert pwd.getpwuid(os.geteuid()).pw_name == 'wasmhome'
assert socket.gethostname() == 'wasmhome-bench'
addresses = json.loads(subprocess.check_output(['ip', '-4', '-j', 'address', 'show']))
assert any(a.get('local') == '192.168.0.138'
           for link in addresses for a in link.get('addr_info', []))
route = json.loads(subprocess.check_output(['ip', '-4', '-j', 'route', 'get', device]))
print(json.dumps({'uid': os.geteuid(), 'host': socket.gethostname(),
                  'python': sys.version, 'addresses': addresses, 'route_to_esp': route,
                  'home_free_bytes': shutil.disk_usage(pathlib.Path.home()).free}, indent=2))
PY
```

Uma rota existente não comprova recepção UDP. A prova funcional continua sendo
telemetria e controles recebidos do boot correto. Se o CT ou a rede também
bloquearem esse tráfego, o worker não contorna essa restrição: a qualificação
falha e o trigger fica bloqueado.

## Firmware, HTTPS e uma campanha nova

Mudança de rede ou host observador encerra a sessão anterior. Criar novos
`campaign_id` e `session_id`, preservar os slots antigos e manter a baseline
integral aprovada. O runner **não** restaura a flash, grava overlays, muda
sdkconfig, compila, assina pacotes ou inicia HTTPS; essas etapas continuam
no preparo já aprovado da bancada.

O [orquestrador HIL](orquestrador-hil-issue20.md) envolve o runner com lock da
bancada, preparação dos overlays v1, restauração integral e leitura de
verificação entre slots. Ele usa um journal privado para bloquear repetições
após interrupção e recuperar a baseline sem relançar o observador.

1. Escolher e registrar o novo UUID de campanha antes de preparar os artefatos.
   Usar a revisão `issue20-v1.0`, `campaign_type="pilot"`, N=6 e uma semente
   nova. Os limites de medição permanecem congelados.
2. Nos sdkconfig privados dos perfis A/B/C, configurar:

   ```text
   CONFIG_WASMHOME_OBSERVER_IPV4="192.168.0.138"
   CONFIG_WASMHOME_UDP_PORT=3333
   CONFIG_WASMHOME_UPDATE_SERVER_IPV4="192.168.0.138"
   CONFIG_WASMHOME_UPDATE_SERVER_NAME="wasmhome-bench.test"
   CONFIG_WASMHOME_UPDATE_SERVER_PORT=8446
   CONFIG_WASMHOME_UPDATE_CAMPAIGN_ID="ID_DO_PACOTE_OTA_ASSINADO"
   ```

   Preservar a CA, chave pública COSE, identidade AOT, mapa de partições,
   política antirretorno e os demais valores aprovados. Não incluir chaves
   privadas no firmware. O endpoint UDP é compilado: o firmware só recebe
   controles originados desse mesmo IPv4 e porta. A configuração exige
   **rebuild e boot do novo firmware**; não é uma troca dinâmica do observador.
3. Recompilar os firmwares v1 necessários e o firmware nativo v2. A OTA nativa
   instala outra aplicação completa, portanto v2 também deve apontar para o
   CT. Gerar novamente envelopes/pacote assinados para a nova campanha, com
   os hashes efetivos; nunca editar um payload depois da assinatura. Os
   módulos só podem conservar seus bytes se a própria entrada de build não
   mudou; seus manifests ainda precisam corresponder à campanha/versão.
4. Transferir ao home de `wasmhome` o pacote imutável, certificado e chave TLS,
   chave pública COSE e os scripts `artifact_https_server.py` e
   `signed_artifacts.py` da revisão registrada. Conferir seus hashes. O
   observador sincroniza seus próprios quatro scripts automaticamente; ele
   não transfere o pacote HTTPS, a chave TLS ou a chave privada de assinatura.
   A chave privada COSE permanece fora do CT. A chave TLS deve ter modo 0600,
   e o certificado deve satisfazer o DNS SAN `wasmhome-bench.test`, a CA
   compilada e a validade temporal. Conservar a sincronização SNTP do ESP.
5. Estagiar o pacote, certificado e chave TLS sob o home de `wasmhome`. Iniciar
   uma instância separada do servidor aprovado em uma sessão normal desse
   usuário, na porta não privilegiada 8446, com log exclusivo da nova campanha.
   Os listeners existentes em 8443/8444 são serviços do sistema, pertencem ao
   UID 999 e devem permanecer intactos. O preparo observou 8446 livre;
   reconfirmar antes de iniciar. Exemplo no CT, usando caminhos de arquivos
   staged sob o home de `wasmhome`:

   ```bash
   umask 077
   python3 /home/wasmhome/HIL/tools/artifact_https_server.py \
     --bind 192.168.0.138 --port 8446 \
     --server-ip 192.168.0.138 --server-name wasmhome-bench.test \
     --package /home/wasmhome/HIL/package \
     --certificate /home/wasmhome/HIL/tls/server.pem \
     --private-key /home/wasmhome/HIL/tls/server.key \
     --signing-public-key /home/wasmhome/HIL/package/signing-public.der \
     --expected-campaign-id ID_DO_PACOTE_OTA_ASSINADO \
     --expected-aot-toolchain-id wamr-2.4.5-wasmhome.3 \
     --log /CAMINHO_PRIVADO_NO_CT/log-exclusivo-da-campanha.jsonl
   ```

   Reservar a porta para esta campanha; não encerrar serviços anteriores para
   obter a porta sem identificar sua finalidade. Se a porta estiver ocupada,
   escolher outra porta não privilegiada no preparo e recompilar todos os
   endpoints correspondentes. Não substituir HTTPS por `http.server` nem
   desligar validação de certificado.
   Registrar o caminho exato de `--log` no recibo privado e usar o mesmo em
   `--remote-server-log`. O preflight automático do orquestrador rejeita um
   servidor ausente, de outro pacote OTA ou com log diferente antes de tocar
   a placa. Ele usa apenas SSH e leituras em `/proc` sob `wasmhome`; não requer
   `sudo`, instalação de serviço, mudança de grupos ou firewall.
6. Criar o manifesto privado `artifacts.json` no formato já aceito pelo
   `update_campaign.py plan`: `schema_version=1` e lista `artifacts`, contendo
   `artifact_id`, `scenario` (`native`, `interp`, `aot`), caminho efetivo e
   SHA-256 para cada entrada. Incluir os novos arquivos realmente preparados;
   não reutilizar hashes ou paths de builds antigos. O planejamento normaliza
   caminhos e confere os hashes e a cobertura dos três cenários.

Exemplo reproduzível para registrar os novos metadados e criar o plano, depois
de preencher o manifesto dos artefatos preparados. Os arquivos abaixo são
privados. Ajustar o ambiente e a identificação da bancada antes da coleta.

```bash
HIL_PREP=/CAMINHO_PRIVADO/issue20-nova-sessao
HIL_ARTIFACTS="$HIL_PREP/artifacts.json"
HIL_SEED=issue20-ct107-nova-sessao-r1
HIL_DEVICE_IP=IPV4_LAN_CONFIRMADO_DO_ESP
HIL_SCHEDULE_ID=UUID_NOVO_DA_AGENDA_HIL
HIL_ARTIFACT_CAMPAIGN_ID=ID_DO_PACOTE_OTA_ASSINADO
python3 - "$HIL_PREP" "$HIL_DEVICE_IP" "$HIL_SCHEDULE_ID" "$HIL_ARTIFACT_CAMPAIGN_ID" <<'PY'
import ipaddress, json, pathlib, sys, uuid
root = pathlib.Path(sys.argv[1])
device = str(ipaddress.IPv4Address(sys.argv[2]))
root.mkdir(mode=0o700, parents=True, exist_ok=True)
campaign_id = str(uuid.UUID(sys.argv[3]))
metadata = {'protocol_revision': 'issue20-v1.0', 'campaign_type': 'pilot',
            'campaign_id': campaign_id, 'session_id': str(uuid.uuid4()),
            'artifact_campaign_id': sys.argv[4],
            'bench': {'observer_host': 'wasmhome-bench', 'observer_ipv4': '192.168.0.138',
                      'https_ipv4': '192.168.0.138', 'https_port': 8446,
                      'device_ipv4': device, 'serial_port': '/dev/ttyACM0'},
            'environment': {}}
with (root / 'metadata.json').open('x') as stream:
    json.dump(metadata, stream, indent=2)
    stream.write('\n')
print('metadata criada para a agenda HIL')
PY
HIL_CAMPAIGN_ROOT="$HIL_PREP/campaign"
python3 tools/update_campaign.py plan --output "$HIL_CAMPAIGN_ROOT" \
  --metadata "$HIL_PREP/metadata.json" --artifacts "$HIL_ARTIFACTS" \
  --n 6 --seed "$HIL_SEED"
python3 tools/update_campaign.py preflight "$HIL_CAMPAIGN_ROOT" \
  --output-dir "$HIL_PREP/preflight" --minimum-free-bytes 1073741824
```

O `preflight` acima é offline; não usar `--probe-serial` ou `--probe-network`
para essa conferência. Antes do slot real, seguir o protocolo de identidade,
baseline, restauração e overlay aprovado. A baseline aceita não é substituída
pelo novo firmware de aplicação.

**Executar somente quando o preparo físico estiver aprovado:** o comando abaixo
abre a serial uma vez e inicia o único slot indicado. Conferir o primeiro slot
na agenda efetivamente gerada e substituir `HIL_SLOT_ID` pelo valor dessa
agenda; o exemplo não reordena slots. `HIL_TARGET_SEQUENCE` deve ser a sequência
positiva realmente assinada para v2 e compatível com o piso restaurado.

```bash
HIL_SLOT_ID=SLOT_ID_DA_AGENDA
HIL_TARGET_SEQUENCE=SEQUENCIA_V2_DO_PACOTE_ASSINADO
python3 tools/update_campaign.py run-slot "$HIL_CAMPAIGN_ROOT" "$HIL_SLOT_ID" \
  --device-ip "$HIL_DEVICE_IP" --serial-port /dev/ttyACM0 \
  --bind-ip 192.168.0.138 --udp-port 3333 --device-port 3333 \
  --seconds 150 --trigger-after 60 --target-sequence "$HIL_TARGET_SEQUENCE" \
  --period-ms 100 --timeout-ms 300 --max-sample-age-ms 1000 \
  --remote-observer wasmhome@192.168.0.138 \
  --remote-known-hosts "$HIL_KNOWN_HOSTS" --remote-identity "$HIL_KEY" \
  --remote-server-log /CAMINHO_PRIVADO_NO_CT/log-exclusivo-da-campanha.jsonl
```

`--remote-server-log` refere-se ao CT e recebe os mesmos controles de
continuidade/rotação já existentes no runner. Sem essa opção, `--server-log`
continua sendo um arquivo no laptop. O worker não gera eventos HTTP fictícios;
ausência ou vazio são classificados conforme trigger enviado e evidência.

## Sessões, cancelamento e recuperação

O runner copia `remote_observer.py`, `external_observer.py`, o codec e
`update_campaign.py`, com todos os parâmetros da API `run()`. A CLI antiga do
observador, isoladamente, não expressa as janelas completas da #20 e não deve
substituir o backend. O worker recusa outros parâmetros: 60 s anteriores,
30 controles/30 telemetrias válidas, mesmo boot, 100/300/1.000 ms,
60 s de prazo e 30 s posteriores. A qualificação ocorre no CT imediatamente
antes do único trigger.

Cada slot recebe UUID remoto próprio. `start.claim` é persistido antes do
lançamento; qualquer repetição retorna o estado existente e nunca cria outro
worker. O diretório local do slot também impede reexecução. Uma preparação
interrompida deixa uma pasta recusada para nova preparação; não a apagar para
repetir um estado potencialmente ambíguo.

Antes de qualquer trigger, a captura UART precisa registrar bytes reais. O
runner espera até 5 s pelo primeiro byte depois de abrir a serial e considera
a captura sem saúde quando o último bloco de bytes tem 2 s ou mais. O limite de
2 s foi definido a partir dos metadados das capturas anteriores: os fluxos
native/interp tiveram maior intervalo entre blocos de 407,149 ms (212 blocos /
11.098 bytes no native e 6.289 blocos / 376.137 bytes no interp); a janela de
2 s dá quase 5 vezes essa margem observada. Os primeiros bytes chegaram 6,255
e 11,181 ms após a abertura. Se a UART ficar sem bytes além do limite, o health
check cancela a confirmação remota; o observador registra `instrument_abort`,
preserva evidências e não repete o slot. A mesma regra vale depois do trigger:
uma pausa UART longa pode encerrar a observação antes do fim do pós-window, e
esse slot exige adjudicação, sem novo trigger automático.

No backend remoto, cada `start` e renovação começa com um desafio único criado
no CT e datado pelo relógio monotônico do CT. Só depois de receber esse desafio
o laptop amostra a idade do último byte UART e envia o valor junto com o token
de uso único. O CT mede o RTT do desafio no próprio relógio monotônico e exige
`idade_uart + RTT < 2 s`; relógios monotônicos de máquinas diferentes nunca
são subtraídos. Desafios expirados, substituídos ou já consumidos são
recusados. Essa sequência garante que a amostra serial usada para autorizar o
trigger ocorreu depois do timestamp do CT.

Em novas coletas, o laptop abre um único `ControlMaster` SSH por slot antes de
`prepare`. O socket fica em um diretório temporário local exclusivo, curto e
0700. `challenge`, `heartbeat` e coleta dos arquivos usam essa mesma conexão,
com a identidade e o `known_hosts` explícitos já exigidos. Isso evita repetir
o handshake SSH a cada renovação; o desafio de uso único, a amostragem UART
posterior ao desafio e o limite de 2 s permanecem iguais. Os clientes do
socket recusam o fallback para uma nova conexão. A primeira perda ou falha
de RPC torna o transporte inutilizável para o restante da captura, mesmo que
o processo master ainda exista; a lease remota expira normalmente. O master
é encerrado e seu diretório removido ao sair, inclusive em falhas. A recuperação
posterior continua restrita a `status`/`recover`, sem renovar ou relançar.
Se o sistema não confirmar o encerramento mesmo após `kill`, o cliente registra
erro de cleanup incompleto e remove o socket; não considera a captura concluída
nem abre outra conexão automaticamente.

Se o worker terminar enquanto o cliente pede o próximo desafio, o servidor
retorna o estado terminal e o manifesto selado, sem criar nonce ou lease.
Publicação do manifesto/estado terminal e RPCs compartilham o mesmo lock, para
que uma renovação não atravesse a publicação terminal usando estado antigo.
O cliente então arquiva o resultado sem tratar essa corrida como perda de
transporte. Essa mudança vale apenas para planos e snapshots novos: preservar
os dados e o código arquivado da r7. Testes offline verificam o ciclo do
transporte e os bloqueios, mas não demonstram a margem de latência na bancada.

A validade da lease também começa no timestamp do desafio: o prazo termina no
menor entre `desafio + 3 s` e `desafio + 2 s - idade_uart`. Assim, a lease
expira de forma conservadora, sem margem fixa para SSH e sem estender o prazo a
partir da aceitação tardia do pedido. O cliente verifica a saúde serial e a
thread antes de iniciar e antes de cada renovação. Uma falha de saúde cancela a
lease; depois da primeira falha SSH o cliente não renova, inclusive após
reconexão. O observador confere a lease a cada ciclo e novamente antes do
trigger. No backend local, consulta diretamente o estado da captura serial.
Uma falha de captura gera `instrument_abort` e preserva resumo e dados
disponíveis.

Mesmo com esse limite, saúde UART e envio UDP não são atômicos entre máquinas:
uma falha recém-ocorrida pode ainda não ter chegado ao CT enquanto a última
lease estiver válida. O prazo da lease fica limitado pelo timestamp do desafio
e pelo limite de frescor, além do atraso do worker até a próxima verificação.
Uma falha após o envio não desfaz o trigger e nunca autoriza repetição
automática. Falhas instrumentais continuam exigindo a classificação prevista
no protocolo; timeout de rede sozinho não prova falha instrumental.

O worker só publica o manifesto depois de fechar observações, resumo e logs
do servidor. O cliente confere tamanho/SHA-256 dos arquivos e o hash que vincula
configuração e snapshots de código, baixa em blocos para staging e incorpora
cada arquivo sem sobrescrever evidência existente. A captura UART permanece
aberta até acabar a supervisão/coleta. Nenhuma evidência remota é apagada.

Após perda do cliente, usar o snapshot local de código e o recibo exclusivo do
slot. Os comandos abaixo fazem somente consulta/recuperação; não abrem serial,
não renovam a confirmação e não iniciam workers:

```bash
HIL_SLOT_DIR="$HIL_CAMPAIGN_ROOT/raw/slots/$HIL_SLOT_ID"
python3 "$HIL_SLOT_DIR/remote-code/remote_observer.py" status \
  "$HIL_SLOT_DIR/remote-session.json"
python3 "$HIL_SLOT_DIR/remote-code/remote_observer.py" recover \
  "$HIL_SLOT_DIR/remote-session.json"
```

Se o worker ainda não encerrou, a recuperação recusa copiar arquivos abertos.
Se o processo morreu, o CT reiniciou ou o disco falhou antes de selar os
arquivos, manter o estado como não resolvido e preservar a pasta para
investigação; este primeiro corte não reconstitui automaticamente um manifesto
de processo morto. `recover` não inventa `slot.json`, resultado de despacho ou
resultado experimental. Uma falha normal do runner deixa
`terminal-error.json` com recibo dos arquivos disponíveis; um encerramento
abrupto pode impedir esse recibo e exige conferir os dados já persistidos.

## Relógios e proveniência

`observations.jsonl` e `summary.json` usam `observer_ct107`, com hostname e ID
de boot do host. A primeira entrada da UART declara `serial_laptop`; o slot
registra ambos os domínios. O firmware conserva seus próprios timestamps por
boot. Correlacionar os fluxos por `boot_id`, `attempt_id` e sequência; nunca
subtrair monotônicos de hosts diferentes. Tempos de SSH e download de logs
não entram na latência da atualização.

As novas opções e a correção da captura serial alteram o SHA-256 de
`tools/update_campaign.py`. Um plano existente fixa `git_commit` e
`script_sha256` e será recusado depois dessa mudança. Criar o plano novo após
estabilizar a revisão e os artefatos; qualquer commit posterior muda a revisão
e também exige outro plano. Preservar o snapshot `remote-code` e seus hashes
para recuperação, mesmo que a árvore de desenvolvimento avance. O preparo
documentado não inclui commit, push, rebuild, reboot ou operação na placa.

# Distribuição HTTPS assinada — issue #15

A publicação contém seis pares payload/COSE: versões v1 e v2 dos cenários nativo,
Wasm interpretado e AOT. O manifesto comum fixa ESP32-S3, API base 1, campanha,
sequência, limites, tamanho e SHA-256 do payload. A sequência 1/2 é por cenário;
ela não implementa nem persiste o piso antirretorno no MCU.

## Chaves e papéis

- A chave P-256 de assinatura COSE é criada e usada somente pelo usuário
  `wasmhome` no CT107. A chave privada não sai do diretório privado do assinador.
- O perfil ES256 usa assinatura `R || S` de 64 bytes com `S` normalizado para
  low-S, para obter uma representação canônica.
- O serviço HTTPS roda como `wasmhome-http`, sem acesso ao diretório do assinador.
  Ele valida toda a matriz e as assinaturas antes de iniciar, e só então serve
  os caminhos imutáveis do `index.json`.
- A chave privada da CA TLS fica no host de captura. O CT recebe apenas o
  certificado público da CA e a chave TLS folha necessária ao servidor; a chave
  folha é `0600` para `wasmhome-http`. O cliente valida a CA e o SAN DNS/IP.
- A chave pública de assinatura é instalada separadamente em
  `/etc/wasmhome/pki/issue15/signing-public.der`, como `root:wasmhome-http` e
  `0440`. O pacote inclui uma cópia pública, que precisa coincidir com esse pin.

## Instalação do servidor

Instale o código em `/opt/wasmhome/issue15` como `root:root`, sem escrita para o
serviço. Promova o pacote completo e já verificado para
`/srv/wasmhome-http/issue15/package`, propriedade `root:wasmhome-http`, com
diretórios `0550` e arquivos `0440`. O assinador monta o conjunto em seu espaço
privado; não compartilhe esse diretório com o serviço.

Copie as unidades `wasmhome-issue15-artifacts.service` e
`wasmhome-issue15-firewall.service` para `/etc/systemd/system/` e
`issue15-firewall.nft` para `/etc/wasmhome/issue15/firewall.nft`. A regra nftables
permite a porta TCP 8443 somente ao host de captura `192.168.0.13`; atualize a
origem antes de iniciar se o host mudar. Depois de validar os caminhos e
permissões de certificado, chave e diretórios, carregue as unidades e inicie
`wasmhome-issue15-artifacts.service`. O systemd cria o diretório de log como
`wasmhome-http`; o processo não tem capabilities e mantém o restante do sistema
de arquivos somente para leitura.

O serviço fixa `wasmhome-bench.test`, `192.168.0.138`, campanha
`issue15-bench-20261015` e `wamr-2.4.5-wasmhome.3`. Alterar qualquer identidade
exige construir e validar novamente o pacote e atualizar a unidade. A porta
serve somente `GET`/`HEAD`; outros métodos retornam `405` e `Allow: GET, HEAD`.
Os eventos são gravados em `/var/log/wasmhome/issue15/http.jsonl`.

## Cliente e verificação

Use a CA pública e a resolução local do nome, sem desativar a validação TLS:

```sh
curl --fail --show-error --cacert bench-ca.crt \
  --resolve wasmhome-bench.test:8443:192.168.0.138 \
  https://wasmhome-bench.test:8443/issue15-bench-20261015/index.json
```

Baixe também os payloads, envelopes COSE e a chave pública para a captura. Rode
`tools/signed_artifacts.py verify-set` com a chave pública pinada fora do pacote,
`--expected-campaign-id issue15-bench-20261015` e
`--expected-aot-toolchain-id wamr-2.4.5-wasmhome.3`. Preserve bytes, hashes,
fingerprints públicos, headers e eventos de download na evidência sanitizada.
Não publique chaves, configurações de Wi-Fi, logs brutos que possam conter
identificadores de rede, nem os ELF privados.

Esta entrega comprova geração, assinatura, validação e transporte HTTPS no host.
Ela não comprova download pelo MCU, validação integrada ao firmware, persistência
NVS, rollback ou atualização OTA; esses critérios permanecem nas issues #16–18 e
#22.

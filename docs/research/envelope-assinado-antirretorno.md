# Envelope assinado e política antirretorno

Status: decisão fechada para implementação e campanha experimental.

Data da decisão: 2026-09-09.

## Decisão

Os três cenários usarão o mesmo envelope lógico. O manifesto será o `payload`
embutido de um `COSE_Sign1` com tag 18, codificado em CBOR determinístico. A
assinatura será ES256: ECDSA sobre NIST P-256 com SHA-256. O artefato será
transferido separadamente e ligado ao manifesto por tamanho e SHA-256.

O dispositivo aceitará apenas uma sequência estritamente maior que o maior
valor já aceito no namespace `(campaign_id, scenario, component)`. O valor será
um inteiro sem sinal de 64 bits persistido em NVS. Esta regra é comum à imagem
OTA, ao módulo Wasm e ao módulo AOT; os mecanismos de validação e recuperação
posteriores permanecem próprios de cada formato.

## Contrato do envelope

O arquivo de envelope usa o tipo de mídia `application/cose` com
`cose-type="cose-sign1"`. Sua forma é:

```cddl
WasmHomeEnvelope = #6.18(COSE_Sign1)

; RFC 9052. protected é o CBOR serializado de {1: -7, 4: kid}.
COSE_Sign1 = [
  protected   : bstr,
  unprotected : {},
  payload     : bstr .cbor WasmHomeManifest,
  signature   : bstr .size 64
]

WasmHomeManifest = {
  1  : 1,                 ; manifest_version
  2  : tstr,              ; campaign_id
  3  : 1 / 2 / 3,         ; scenario: native / wasm / aot
  4  : 1 / 2 / 3,         ; artifact_kind: esp_app / wasm / wamr_aot
  5  : tstr,              ; component: "firmware" ou "gpio-control"
  6  : tstr,              ; release_version, rótulo exibível
  7  : uint,              ; sequence, limitado a uint64
  8  : uint,              ; payload_size, limitado a uint32 no MCU
  9  : bstr .size 32,     ; payload_sha256
  10 : tstr,              ; target: "esp32s3"
  11 : uint,              ; base_api_version
  12 : tstr / null         ; toolchain_id; obrigatório para wamr_aot
}
```

Regras de codificação e validação:

1. O manifesto e a estrutura `Sig_structure` seguem os requisitos centrais de
   codificação determinística do CBOR: comprimentos definidos, representação
   mínima de inteiros, chaves ordenadas pela codificação e nenhuma chave
   duplicada. Floats, tags adicionais, campos desconhecidos e valores fora dos
   tipos/ranges acima são recusados.
2. O cabeçalho protegido contém somente `alg = -7` (ES256) e `kid`. O cabeçalho
   não protegido é vazio. `external_aad` é a string de bytes vazia. O `kid` é
   uma `bstr` de 8 bytes formada pelos oito primeiros bytes do SHA-256 da chave
   pública em DER `SubjectPublicKeyInfo`. Ele seleciona uma chave, mas a
   combinação aceita de chave, curva P-256 e algoritmo ES256 é fixada pela
   configuração do firmware; colisão ou chave ausente causa rejeição.
3. A assinatura COSE tem 64 bytes, `I2OSP(R, 32) || I2OSP(S, 32)`. Assinatura
   ASN.1/DER não é aceita no envelope.
4. `scenario` e `artifact_kind` devem formar um par válido: `(1,1)`, `(2,2)` ou
   `(3,3)`. `component` deve ser `firmware` no cenário nativo e `gpio-control`
   nos cenários WAMR. A telemetria não é substituída nesta campanha.
5. `campaign_id`, `target`, `base_api_version` e, em AOT, `toolchain_id` devem
   coincidir exatamente com a configuração compilada no firmware. Isso impede
   que um artefato autêntico seja usado no cenário, componente ou ABI errado.
6. `release_version` é informativo. A ordem é definida exclusivamente por
   `sequence`; versões semânticas não participam da comparação antirretorno.

COSE fornece uma forma assinada padronizada e evita que o projeto invente a
serialização de ECDSA. A estrutura `COSE_Sign1` e sua `Sig_structure` são
definidas pela [RFC 9052](https://www.rfc-editor.org/rfc/rfc9052.html#section-4.2),
e ES256/P-256 e o formato `R || S` pela
[RFC 9053](https://www.rfc-editor.org/rfc/rfc9053.html#section-2.1). As regras de
CBOR determinístico vêm da
[RFC 8949](https://www.rfc-editor.org/rfc/rfc8949.html#section-4.2.1).

## Ordem obrigatória de validação

O MCU processará cada atualização nesta ordem e encerrará no primeiro erro:

1. limitar o tamanho do envelope e decodificar CBOR em modo estrito;
2. exigir tag, cabeçalhos, campos, tipos, ranges e combinações do contrato;
3. localizar a chave pública aprovada por `kid` e verificar COSE/ES256;
4. conferir alvo, cenário, componente, API e toolchain;
5. exigir `sequence > accepted_floor` para o namespace;
6. baixar o payload para uma área inativa, contando bytes e calculando SHA-256;
7. conferir `payload_size` e `payload_sha256` em tempo constante quando a API
   criptográfica usada oferecer essa garantia;
8. executar a validação própria do formato e o teste de saúde;
9. ativar pelo procedimento específico do cenário;
10. tornar persistentes o novo piso e o estado final.

WAMR não substitui as etapas 1 a 7. `wasm_runtime_load()` retorna falha para um
módulo que não pode carregar, mas recebe um buffer gravável que deve permanecer
válido até `wasm_runtime_unload()`. Para AOT, o runtime também rejeita versões de
pacote incompatíveis, e a documentação recomenda a mesma versão de `wamrc` e do
runtime em produção. Além disso, o build da campanha deve habilitar
`WAMR_BUILD_AOT_VALIDATOR=1`, pois o próprio WAMR declara que, por padrão,
considera o arquivo AOT válido e não forjado. Fontes primárias:
[API de incorporação do WAMR](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/cd0497e26a7355973948f1fd903b049003cf0e85/core/iwasm/include/wasm_export.h#L3180-L3220),
[compatibilidade AOT](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/cd0497e26a7355973948f1fd903b049003cf0e85/doc/build_wasm_app.md#aot-compiled-module-compatibility-among-wamr-versions) e
[opção AOT Validator](https://github.com/wasm-micro-runtime/wasm-micro-runtime/blob/cd0497e26a7355973948f1fd903b049003cf0e85/doc/build_wamr.md#aot-validator).
Esses links fixam a revisão upstream consultada; a versão adotada pela bancada
será congelada pelo ticket de toolchain.

## Sequência, persistência e campanha

O estado persistente de cada namespace contém, em um único blob versionado:

```text
state_version, accepted_floor, active_slot, active_hash,
pending_sequence, pending_slot, pending_hash, pending_manifest_hash,
phase, generation, crc32
```

Toda mudança exige `nvs_set_blob()` seguido de `nvs_commit()` e leitura de volta.
O ESP-IDF afirma que alterações NVS só estão asseguradas em armazenamento após
`nvs_commit()`; em alimentação instável ainda existem falhas raras de erase e
recuperação. Por isso, a implementação também habilitará verificação de erase,
manterá dois slots de payload e testará perda de energia no piloto. Consulte a
[documentação NVS](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/storage/nvs_flash.html#nvs-commit).

Estados permitidos: `IDLE -> STAGED -> VALIDATED -> ACTIVATING -> IDLE`. O piso
só avança quando a nova versão passou pelo teste de saúde. Um pacote com
sequência igual é replay e é recusado. Uma falha anterior à ativação preserva o
payload e o piso ativos. Um erro posterior pode reativar o último slot saudável,
mas nunca reduz `accepted_floor`; recuperação local não é autorização para
instalar novamente um pacote antigo.

Só uma atualização pode estar pendente por vez. Enquanto houver `pending`, um
novo disparo recebe `busy`; ele não pode substituir o estado em recuperação. No
boot, uma sequência igual a `pending_sequence` é tratada somente pelo fluxo de
recuperação local, não como nova instalação. `accepted_floor` significa a maior
sequência que concluiu ativação e teste de saúde; uma tentativa abortada antes
desse ponto pode ser refeita com o mesmo envelope depois de limpar `pending`.

Na campanha, restaurar a lógica v1 também é uma atualização autenticada. Para
cada namespace, a tentativa `n` usa uma sequência nova para v1 e outra maior
para v2 (por exemplo, `2n` e `2n+1`). Reutilizar os mesmos bytes de payload é
permitido, mas cada envelope contém a sequência daquela instalação e recebe uma
assinatura própria. Isso mantém a proteção ativa nas 30 tentativas.

## Recuperação por cenário

### Imagem OTA nativa

O download usa a partição OTA inativa e as APIs de OTA do ESP-IDF. O projeto
habilitará rollback com confirmação pela aplicação. Antes de selecionar a nova
partição, persiste `pending`. No primeiro boot, a aplicação executa o teste de
saúde, chama `esp_ota_mark_app_valid_cancel_rollback()` e então confirma o piso
em NVS. Se houver reset antes da confirmação, o bootloader volta à imagem
anterior; o piso ainda não avançou. Se faltar energia entre a confirmação da
imagem e o commit final, o registro `pending` permite reconciliar o manifesto da
imagem em execução, o slot e a identidade compilada da versão, e concluir o
commit sem aceitar pacote de sequência menor. Se o bootloader retornar à imagem
anterior, o firmware limpa `pending` sem avançar o piso e registra o rollback.

O fluxo segue o rollback controlado pela aplicação documentado pelo
[ESP-IDF](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/ota.html#app-rollback).
O suporte de assinatura nativa do ESP-IDF pode validar imagens OTA mesmo sem
Secure Boot. Ele não será habilitado na campanha principal, pois acrescentaria
uma segunda assinatura somente ao cenário nativo; o envelope COSE será a camada
criptográfica comparável dos três cenários. A validação estrutural normal da
imagem pelo fluxo OTA permanece obrigatória
([Secure OTA Updates Without Secure Boot](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/ota.html#secure-ota-updates-without-secure-boot)).

### Wasm interpretado e WAMR AOT

O payload é gravado no slot modular inativo. O gerenciador verifica o envelope,
hash, loader WAMR, instanciação, imports permitidos e teste funcional enquanto o
módulo GPIO anterior permanece disponível. Depois persiste em um único estado a
seleção do novo slot e o novo piso; só então troca a referência ativa em um ponto
seguro entre requisições. Uma queda antes do commit reinicia no slot anterior;
uma queda depois do commit carrega o novo slot. O módulo de telemetria não é
parado por esse procedimento.

Se o novo módulo falhar após a ativação, o gerenciador pode voltar ao slot
saudável anterior e registra a falha, mantendo o piso avançado. Uma nova
instalação exige sequência maior.

## Chaves e TLS da bancada

- O LXC gera e mantém a chave privada ES256 fora do Git, com acesso restrito ao
  usuário de assinatura. A campanha registra o fingerprint SHA-256 da chave
  pública e dos certificados, nunca a chave privada.
- O firmware contém uma lista fechada de chaves públicas P-256 aprovadas,
  indexadas por `kid`. A primeira campanha usa uma chave. Rotação futura exige
  firmware-base novo que contenha, durante a transição, a chave antiga e a nova.
- Uma CA local exclusiva da bancada assina o certificado do servidor HTTPS. O
  certificado da CA é embutido no firmware e a verificação de nome do servidor
  permanece ativa. As opções de ignorar certificado não entram nos builds da
  campanha. O ESP-TLS aceita uma CA fornecida em buffer e falha por padrão se
  nenhum método de verificação for configurado
  ([ESP-TLS](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/protocols/esp_tls.html#tls-server-verification)).
- A chave TLS e a chave de assinatura são diferentes. HTTPS autentica o servidor
  e protege o transporte; COSE permite verificar o artefato depois do download e
  preserva a autenticidade mesmo se ele vier de cache ou armazenamento distinto.

## Alternativas rejeitadas

**JSON com concatenação própria ou assinatura de campos separados.** Rejeitado
por exigir regras locais de espaços, ordem, Unicode e representação de números.
COSE com CBOR determinístico já especifica os bytes assinados.

**Assinar somente o SHA-256 do payload.** Rejeitado porque cenário, componente,
ABI, tipo e sequência poderiam ser trocados sem invalidar a assinatura.

**Usar apenas HTTPS.** Rejeitado porque TLS autentica a sessão, não cria um
artefato verificável em repouso nem implementa antirretorno.

**Usar somente a assinatura de imagem do ESP-IDF.** Rejeitado porque não cobre
o mesmo envelope para `.wasm` e `.aot`. Um ensaio complementar pode avaliá-la,
mas ela fica desabilitada na campanha comparativa principal.

**Piso global único.** Rejeitado porque a mesma placa alternará entre três
cenários durante a campanha. Namespaces distintos evitam interferência entre as
sequências, enquanto os campos assinados impedem troca cruzada.

**Antirretorno por eFuse nesta campanha.** Rejeitado como mecanismo comum: o
ESP-IDF o vincula à `secure_version` da aplicação, limita a quantidade de
avanços e grava estado irreversível. Ele não governa módulos WAMR. O mecanismo
está documentado em
[Anti-rollback do ESP-IDF](https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/system/ota.html#anti-rollback).

## Alegações permitidas e limites

Ao cumprir este contrato e os ensaios de corrupção, assinatura e sequência, o
trabalho poderá afirmar que, na bancada, os três cenários recusaram envelopes
não autenticados, payloads com hash divergente e sequências antigas, e que se
recuperaram conforme o estado observado.

O contrato não comprova segurança contra acesso físico ou escrita direta na
flash. Sem Secure Boot e flash encryption, um atacante físico pode substituir o
firmware-base, a chave pública ou o estado NVS. A verificação de assinatura sem
Secure Boot protege sobretudo contra atualização remota forjada; a própria
Espressif registra esse limite. Também não há transparência de logs, revogação
online, relógio confiável, confidencialidade do payload ou rotação automática de
chaves. Esses itens ficam fora da campanha definida.

NVS é a persistência escolhida, mas sua segurança diante de perda de energia é
uma hipótese a ensaiar, não um resultado já demonstrado neste projeto. Da mesma
forma, os loaders do ESP-IDF e do WAMR comprovam validade estrutural e
compatibilidade apenas dentro das versões fixadas; o teste funcional continua
obrigatório antes da ativação.

# Protocolo pré-registrado da campanha — issue #20

**Revisão:** `issue20-v1.0`, 16/10/2026
**Estado:** parâmetros congelados antes do piloto; execução física pendente. A
substituição da imagem-base aprovada, sem alterar os demais parâmetros, está
registrada no [adendo de 17/10/2026](evidence/issue20-pilot-2026-10-16/baseline-approval-addendum-2026-10-17.md).
**Base:** revisão do contrato existente, evidência física das issues #16–#18 e calibração da #19. O piloto avaliará se a bancada e a coleta cumprem este protocolo; não será usado para escolher um limite diferente depois de observar resultados.

Este documento fixa as regras da campanha normal e do piloto. Ele não registra uma execução física. A situação da bancada e o inventário de evidências estão em [registro do piloto #20](evidence/issue20-pilot-2026-10-16/README.md).

## Origem da imagem-base

A imagem-base originalmente selecionada foi o snapshot privado integral de
16/10/2026 com SHA-256
`b66c24a3514be56702c1cf7f8d2116e44d5fe0475e3c3c67b65850258eb18ae3`. Sua
origem, tamanho e relação com snapshots históricos estão registrados na
[auditoria da imagem-base](evidence/issue20-pilot-2026-10-16/backup-audit.md).
Esse hash foi substituído, somente para a execução autorizada, pela captura
integral aceita em 17/10/2026 descrita no adendo acima. Todos os limites,
critérios de aceite e procedimentos desta revisão permanecem congelados.
Os hashes `4fcc…` e `b00…` são de sessões anteriores e não substituem essa
imagem: o primeiro identifica outro dump integral de 14/10; o segundo identifica
um backup de apenas 1 MiB da partição de aplicação.

Antes de qualquer escrita, confirmar a identidade da placa, flash de 16 MiB e
ler a flash atual integralmente. O hash lido deve coincidir com
`b66c24a3514be56702c1cf7f8d2116e44d5fe0475e3c3c67b65850258eb18ae3`. Se não
for possível obter essa leitura ou houver qualquer divergência de identidade,
tamanho ou hash, interromper sem gravar e reconciliar a baseline com evidência
nova. Não selecionar silenciosamente um snapshot histórico alternativo.

## Escopo e resultados

- Comparar A (OTA nativo), B (WAMR interpretado) e C (WAMR AOT) no mesmo ESP32-S3, com a mesma rede, servidor HTTPS, observador, carga funcional e contrato de controle.
- Manter a telemetria DS18B20 a cada 500 ms, o estímulo/observação funcional a cada 100 ms, idade máxima observada de amostra de 1.000 ms e a regra de histerese já aprovada.
- Usar `slot_success` em todos os slots planejados como resultado operacional primário. Publicar em separado a taxa de sucesso após aceite entre tentativas aceitas com instrumentação válida. Falha de despacho não será descrita como atualização aceita.
- Reportar cada cenário, os pares por bloco, perdas, timeouts, respostas tardias, dispersão e censura. O piloto e sua análise ficam em diretório/campanha próprios, sem incorporar linhas ou estimativas na campanha normal.

## Limites temporais e janelas congelados

| Regra | Valor congelado | Aplicação |
|---|---:|---|
| Aquisição DS18B20 | 500 ms | Conversão assíncrona; cada leitura válida mantém ID, timestamp e estado |
| Observação/controle | 100 ms | Período comum dos cenários A/B/C |
| Idade máxima da amostra | 1.000 ms | Acima disso a amostra não comprova leitura atual |
| Timeout de resposta funcional | 300 ms | Um único valor para A/B/C; cada `CONTROL_RESULT` deve chegar até 300 ms depois do pedido |
| Timeout de `UPDATE_ACCEPTANCE` | 300 ms | Sem aceite correlacionado pontual, registrar apenas o resultado de despacho |
| Estabilização e janela anterior | 60 s | Depois de restaurar v1 e concluir qualquer reconexão/reset; exige pelo menos 30 controles corretos e 30 mensagens de telemetria válidas |
| Prazo de atualização | 60 s | Do envio de `UPDATE_TRIGGER` ao recebimento da primeira resposta v2 correta; o `CONTROL_RESULT` correspondente também deve cumprir seu timeout individual de 300 ms |
| Janela posterior | 30 s | Depois da primeira resposta v2 correta; se não houver sucesso em 60 s, observar mais 30 s a partir do prazo terminal e registrar qualquer recuperação como tardia |
| Duração máxima da captura | 150 s a partir do primeiro boot observado, mais eventual espera até observá-lo e até 301 ms de drenagem | 60 s de janela anterior, 60 s até o prazo terminal e 30 s de janela posterior; se houver sucesso antes, encerrar 30 s após a primeira resposta v2 correta |

Os três resultados da calibração #19 levaram 6,624 s (AOT), 7,123 s
(interpretado) e 16,303 s (nativo) do disparo à primeira resposta v2 correta.
Cada caminho tem só uma tentativa física aceita; portanto, esses números não
estimam cauda nem dispersão. O prazo comum de 60 s é um limite operacional
conservador, 3,68 vezes o maior desses três tempos observados. Expirar o prazo
é resultado da atualização, não motivo de invalidação.

O limite funcional de 300 ms é mantido, sem ajuste por cenário, por ser o
candidato já exercitado pelo observador e haver baseline físico sem timeout na
calibração da #19. O prazo de 60 s mede o percurso completo do disparo até o
recebimento da primeira resposta v2 correta. Essa resposta também precisa estar
correlacionada a um pedido de controle cuja própria latência não exceda 300 ms.
Os timeouts vistos durante atualizações permanecem resultados do serviço. A
campanha mede sua taxa; não os remove nem aumenta o limite seletivamente. O
limite de recuperação de 30 s da bateria de resiliência é separado e não altera
este prazo de atualização.

## Preparo e sequência de cada tentativa

1. Identificar placa, fonte, adaptador serial, endereço de rede, host observador,
   servidor, temperatura ambiente, revisão de firmware, configurações efetivas,
   hashes e espaço de logs. Guardar a captura bruta fora do repositório.
2. Antes do primeiro slot, identificar a placa e o adaptador pelo inventário do
   sistema, consultar o ID do chip e o tamanho da flash com a ferramenta
   apropriada sem gravar e ler a flash inteira. Confirmar a imagem-base
   aprovada de 16 MiB definida no adendo de 17/10/2026, SHA-256
   `00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`.
   Se não for possível comprovar que a leitura integral atual coincide com ela,
   parar e reconciliar o estado antes de gravar. Guardar o recibo de backup.
3. Só iniciar gravação depois de confirmar que o ID do chip, o tamanho da flash,
   o arquivo de backup autorizado e seu hash correspondem à placa esperada. Se
   qualquer identidade/estado for incerto, parar. Restaurar a imagem-base
   integral e verificar a leitura completa antes de cada novo slot. Isso
   restaura firmware v1 e o piso antirretorno junto com a
   mesma NVS; não apagar NVS isoladamente, reutilizar estado desconhecido nem
   desabilitar a proteção. O piso precisa ser lido/confirmado como no baseline.
4. Abrir serial e observador antes da janela medida. A abertura serial pode
   acionar DTR/RTS e reiniciar a placa; qualquer reinício/conexão necessária
   ocorre antes da janela anterior. Não reabrir a serial durante a captura.
   Confirmar `boot_id`, v1, hashes, DS18B20, GPIO5→GPIO6 e endpoint do servidor.
   As imagens da plataforma comum emitem `WASMHOME_UART_HEARTBEAT,v1` a cada
   500 ms (configurável entre 500 e 750 ms). É um registro serial fixo, sem
   identificadores nem dados de rede; mantém evidência UART atual para o lease
   remoto sem alterar a cadência ou o protocolo UDP. O desafio continua exigindo
   idade do byte mais RTT inferior a 2 s.
5. Observar 60 s em v1 a partir do primeiro `boot_id` recebido e exigir ao
   menos 30 controles corretos e 30 telemetrias válidas nessa janela, todas sob
   um único `boot_id`. Se a qualificação falhar, registrar falha de preparo e
   não enviar `UPDATE_TRIGGER`. Enviar exatamente um disparo por tentativa com
   UUID novo e sequência de mensagem nova. Não reenviar após silêncio, resposta
   ambígua ou aceite tardio. Continuar a observação durante download, troca ou
   reboot, sem preencher perdas com retransmissões.
6. Um aceite correlacionado em até 300 ms inicia uma tentativa aceita. Registrar
   envio e aceite no relógio monotônico do observador, e fases de dispositivo
   somente dentro do mesmo `boot_id`. Uma recusa ou falta de aceite é resultado
   de despacho e não conta entre as cinco atualizações aceitas exigidas por
   cenário.
7. Encerrar o prazo principal aos 60 s do disparo. Após sucesso, manter a
   janela por 30 s após a primeira resposta v2 correta; após expiração sem
   sucesso, observar 30 s adicionais e preservar recuperação tardia
   separadamente. Drenar por até mais 301 ms para classificar respostas de
   pedidos de controle já enviados. Registrar a primeira resposta v2, gaps,
   telemetria, controles tardios/perdidos, boot, versão e GPIO pedido e
   observado.
8. Restaurar e verificar a imagem-base completa antes do próximo slot. Guardar
   hashes e recibos de todas as restaurações, inclusive quando uma tentativa
   falhar.

Temperatura e ambiente são registrados no começo de cada bloco. Os slots A/B/C
de um bloco são executados na mesma sessão física sempre que possível. Uma
pausa ou mudança de rede, alimentação, ambiente, código, toolchain ou
configuração encerra a sessão e recebe novo `session_id`; não reordenar os slots
restantes silenciosamente.

## Piloto e campanha normal

O piloto exige no mínimo cinco atualizações aceitas pontualmente, completas e
rastreáveis por cenário; cada uma precisa de um estado terminal registrado,
sucesso ou falha. A agenda inicial usa seis blocos (18 slots), uma ocorrência de
cada permutação A/B/C, para balancear a ordem; o [schedule candidato](evidence/issue20-pilot-2026-10-16/pilot-schedule.json)
registra a semente e a lista. Executar todas as oportunidades planejadas e
contabilizar recusas/timeouts de aceite separadamente. Depois de cada grupo de
seis blocos, se algum cenário ainda tiver menos de cinco atualizações aceitas
com estado terminal, acrescentar outro grupo completo de seis blocos (18 slots),
com uma ocorrência de cada ordem, sob `campaign_id` e semente novos. Repetir a
regra até todos os cenários cumprirem o mínimo, preservando o vínculo entre as
agendas. Se a bancada não puder continuar com segurança, registrar a interrupção
e deixar o piloto incompleto. Não selecionar reposições pelo resultado
pós-aceite.

Revisar por cenário envio→resposta, aceite→resposta, maior lacuna de controle,
lacuna de telemetria, perdas, respostas tardias e duração/censura. Com pelo
menos cinco tentativas, publicar mediana, mínimo, máximo e intervalo
interquartil como resumos exploratórios; não tratar os tempos repetidos dentro
de uma tentativa como observações independentes. Preservar atualizações aceitas
que falhem e incluí-las nos denominadores. O piloto prova o procedimento e a
variação observada nesta bancada; não prova equivalência nem fornece, sozinho,
um cálculo preciso de poder estatístico.

A campanha definitiva fica congelada em **N=30 blocos e 90 slots planejados**,
um slot A, B e C por bloco. O resultado primário usa todos os 30 slots de cada
cenário; dispatches sem aceite contam como resultado de slot, mas não como
tentativa aceita. Publicar também o denominador de aceites e o de aceites com
instrumentação válida. Não substituir falhas válidas de dispositivo.

N=30 é uma decisão de viabilidade descritiva, não um tamanho amostral derivado
de poder: a #19 tem somente uma atualização aceita por cenário e o piloto terá
apenas cinco ou seis por cenário, insuficientes para estimar a dispersão entre
sessões com precisão. Manter N=30 porque é a referência previamente registrada,
permite cinco repetições de cada uma das seis ordens em 30 blocos e produz 30
oportunidades por cenário para análise de proporções e intervalos descritivos.
As comparações são exploratórias; não alegar poder confirmatório ou
equivalência. Agendar os 90 slots antes da primeira coleta definitiva, balancear
cada permutação cinco vezes, usar semente registrada e preservar o arquivo
congelado. Reposições instrumentais ficam vinculadas ao `slot_id` original e
não aumentam N.

## Invalidação e preservação

Classificar `instrument_status=invalid` somente quando houver evidência
reproduzível de falha de medição, por exemplo: processo do observador/captura
encerrou ou perdeu o relógio monotônico; escrita local truncou/corrompeu os
registros; porta serial foi aberta por outro processo ou a captura serial
falhou; interface/rota do host observador mudou durante o slot e impediu
associar eventos ao dispositivo; ou IDs/boot não permitem correlacionar fontes
por defeito comprovado do coletor. Preservar causa, timestamp, logs e hashes.

Não invalidar por recusa, ausência de aceite, timeout, resposta perdida/tardia,
sequência de amostras com gap, sensor inválido, reboot, falta de rede/servidor
após aceite, payload rejeitado, hash/assinatura/versão incorretos, GPIO errado,
falha ou expiração da atualização. São resultados ou causas de preparo a
classificar, não evidência de instrumento quebrado. Silêncio por si só não
prova falha instrumental. Marcar `unresolved` quando a evidência não permitir
decidir; manter o registro original e não repor até comprovar que a tentativa
não foi executada ou que a medição foi inválida por causa prevista. Falha com
resultado desfavorável nunca autoriza reposição.

Cada piloto usa UUIDs, `campaign_id`, `slot_id`, `block_id` e `session_id`
próprios, caminho `pilot/issue20/<campaign_id>/` no armazenamento privado e
`metadata.campaign_type="pilot"`. A campanha normal usa outra raiz,
`campaign_type="normal"` e ID próprios. JSONL bruto, UART e flash permanecem no
armazenamento privado com manifesto SHA-256; no repositório entram somente
agenda, resumo sanitizado e recibos sem credenciais, endereços privados ou dump
de flash.

## Status de execução

Esta revisão congela o protocolo antes do piloto para impedir escolha de limites
após conhecer resultados. Em 16/10/2026, o preflight identificou uma ESP32-S3
QFN56 rev. 0.2 com flash de 16 MiB e obteve leitura integral, mas o hash vivo
(`d29c…`) divergiu da imagem-base aprovada (`b66…`) em 7.920 bytes da NVS.
A triagem semântica offline isolou uma mudança em um registro de calibração PHY,
sem mudança lógica nos itens Wi-Fi ou no ledger NVS antirretorno da aplicação;
os metadados também mostram rotação de página. Isso não estabelece a causa da
mudança nem observa o estado eFuse relevante. Nenhuma gravação ou tentativa de
piloto ocorreu; a baseline não pode ser reconciliada com segurança ainda e a
porta de gravação permanece fechada. O `boot_id`, firmware ativo, sensor,
GPIO5→GPIO6 e janela viva de observação continuam pendentes. A agenda registrada
é planejamento, não observação. A conclusão da issue #20 depende de resolver
essa divergência, executar pelo menos cinco atualizações completas aceitas por
cenário, revisar os dados reais e preservar seus recibos.

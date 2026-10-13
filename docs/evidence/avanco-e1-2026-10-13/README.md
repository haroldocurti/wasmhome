# Avanço E1 em 13/10/2026

Este pacote acompanha a fonte preparada após `0ba2faab16dc9671f89a663eea8e6d0d1f528997`.
A publicação identifica a fonte pelo commit que contém este arquivo. Relatórios
preservados indicam seus snapshots e hashes específicos; revisões r3/r4 não são
aprovação automática de alterações posteriores.

## Proveniência e configurações

- P0 reconciliou b657/801 e os manifestos R6/R7. O build B histórico precedeu
  801 e depois recebeu uma recompilação incremental. Falta o log de geração do
  manifesto final histórico f311; o nativo também conserva uma lacuna de
  revalidação. Os relatórios e a revisão independente descrevem essas limitações.
- Novas gerações R8/R9 em 801 reproduziram os cinco payloads históricos. O novo
  build B em R8 passou pelo verificador estrito depois de preservar fora do IDF
  um bytecode Python gerado. Recibo: `build_checked_not_device_validated`.
- O bundle de 801 foi recuperado e preservado privadamente em
  `~/.local/share/wasmhome-private/source-801-20261013`. Seu SHA-256 é
  `bbad56f4d599579db41d5d8b9c4258cedc132c0c962dd0b0c532f7fceb0c9e2a`.
  Apesar da mensagem de bundle verify, a ancestralidade não é completa:
  há limite shallow a7c4f72 e pai ausente 8b3721. Não é um backup de toda a história.
- P2 recuperou os sdkconfig e JSON completos, comparou hashes e valores efetivos
  e manteve os originais em diretório privado, modos 700/600. O mapa publicado
  omite valores sensíveis. Seus campos de revisão devem ser lidos com a
  cronologia P0 acima, especialmente para o build B histórico.

## Implementação e testes

PowerShell portátil 7.6.6 e codec C compilado permitiram executar a suíte real
no Linux. A única exclusão final corresponde à junction exclusiva do Windows.
A #11 recebeu serviços concorrentes de telemetria DS18B20 e controle GPIO,
limiares v1/v2, banda total de 1 °C e estado inicial baixo aprovados pelo usuário.
A política de replay/conflito e saturação está no protocolo. O firmware recusa
atualizações; ainda não implementa OTA.

A #9 recebeu instrumentação de memória fora do intervalo cronometrado e um
verificador offline de consistência. Ele não atesta por si só a execução física.
As revisões encontraram e corrigiram limites de heap, ordem do fechamento,
evicção de UUIDs e coleta concorrente de memória. r4 foi reprovado porque a soma
free+allocated não representa capacidade fixa no ESP-IDF. Seus resultados
positivos e o parecer negativo foram preservados; r5 usa capacidade da API específica e livre/maior bloco de uma única coleta.
Sua suíte tem 81 casos: 80 aprovados e uma exclusão exclusiva do Windows.
A revisão independente r5 aprovou o helper e a cobertura de host; ela não
substitui build ESP-IDF ou validação na placa.

## Gates seguintes

A reconstrução independente LLVM/wamrc está em andamento, com fontes fixadas e
um job por vez. Não há atestado concluído do binário antigo. A configuração
privada de rede foi preenchida pelo usuário e validada sem publicar seus valores.
A fiação GPIO4 e GPIO5→GPIO6 foi reconfirmada. Builds ESP-IDF da fonte nova,
backup da aplicação atual e ensaios físicos da #11 e A/B/C ainda são necessários.
As issues #7/#9/#11 continuam abertas.

`SHA256SUMS` cobre os arquivos publicados neste pacote. Configurações privadas,
bundle, manifestos completos grandes, executáveis e firmware com credenciais não
estão incluídos. Logs completos adicionais permanecem nos diretórios de trabalho
indicados nos relatórios; este pacote seleciona recibos, logs de geração, testes
e revisões que sustentam os resultados acima.

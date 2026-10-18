# Issue #22 — resultado da tentativa física encerrada (18/10/2026)

## Resultado

A campanha preliminar `issue22-preliminary-20261018-r1` foi encerrada depois de
uma tentativa de preparação. O preflight remoto validou o staging e a leitura
integral inicial da baseline aceita. O slot 1 (`ENV-SIG`, perfil nativo) foi
reservado e a instrumentação falhou antes de abrir as capturas UDP/serial: o
coletor tentou vincular a porta UDP ao endereço do CT, que não está atribuído à
máquina observadora local. Nenhum `fault_request`, `fault_accepted` ou pedido
HTTPS de fixture foi observado. Nenhuma outra linha foi iniciada ou repetida.

O estado é **inconclusivo para a injeção de falhas**; não conta como rejeição,
trap, recuperação, timeout do dispositivo ou tentativa instrumentada da matriz
da #22. Os critérios de prova física da issue permanecem pendentes.

## Integridade da bancada

- A operação escreveu a tabela de partições temporária e os artefatos de
  firmware necessários à preparação do slot. A flash integral de 16 MiB foi
  restaurada depois da falha e lida de volta com SHA-256
  `00aa22c35720354383c3d686a14612a7cb2fd99c57cb1ce214d07338c1c167a5`, igual
  à baseline aceita.
- O ensaio não fez escrita ou limpeza direcionada de NVS. A restauração integral
  repôs a imagem aceita completa, inclusive a região NVS, e sua leitura de volta
  coincidiu com o hash aprovado.
- A observação do coletor não gerou dados: `observations.jsonl` e
  `summary.json` ficaram vazios. A falha ocorreu antes do disparo da fixture.
- A verificação posterior do gerenciador remoto não encontrou processo/listener
  de campanha, recibo de processo ou recibo de gerenciador ativo.
- Os registros brutos e os dumps de flash permanecem no armazenamento privado
  de bancada. Nenhum deles é copiado para este repositório.

O próximo passo para uma futura retomada é corrigir e verificar a rota de
observação CT→máquina local sem alterar firewall nem usar privilégios de sistema,
depois criar nova campanha com ID e semente novos. Esta campanha encerrada não
deve ser retomada nem ter sua linha repetida.

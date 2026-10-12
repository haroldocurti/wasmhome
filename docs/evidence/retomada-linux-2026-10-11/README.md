# Arquivo de execução para retomada em Linux

Preservação em 11/10/2026. Leia primeiro o
[registro de retomada](../../retomada-linux.md). Os snapshots mantêm os bytes
originais, inclusive estados pendentes que foram resolvidos posteriormente.
Os complementos identificam essas resoluções sem reescrever evidência bruta.

| Diretório/arquivo | Conteúdo |
|---|---|
| `integration/` | Propostas, revisões, testes e recibos das PRs28/29 e fechamento #6; documentos de preflight históricos |
| `runtime-review/` | Revisões do candidato 40aca4c/187ff96 e testes locais; não comprova LLVM ou B/C |
| `ct107/` | Aquisição, dependências, venv/SDKs, build nativo e verificação/coleta real; snapshots de recursos do host |
| `ct107/native-final-ct107/` | Lock, componente externo, config/cache/compile_commands, disassembly e complementos R4; BIN/ELF omitidos conforme `source-map.json` |
| `donor/` | Receita histórica CT110 R3, 28 testes, índices/revisões e mapa de caminhos; proposta substituída, não executar como receita do novo Linux |
| `observer-preliminary/` | Capturas preliminares de 02/10 que ainda estavam locais, incluindo rodada sem boot/sem controles |
| `issues-2026-10-11.json` | Snapshot do rastreador consultado nesta publicação |
| `source-map.json` | Origens e destinos das cópias, bytes/hashes, exclusões e motivos |
| `SHA256SUMS` | Integridade de todos os arquivos deste arquivo de sessão, exceto o próprio índice |

Datas e caminhos absolutos nos recibos são históricos. Nos documentos originais,
links relativos podem apontar a antigos `build-session-*`; use o mapa desta pasta
e o [mapa do doador](donor/path-map.json) para localizar as cópias portáveis.
Os scripts de sessão não são um instalador aprovado para qualquer host novo.

O arquivo não contém a aplicação original da placa, chaves privadas, SDKs,
cache LLVM ou binários de firmware. Backups e saídas interrompidas permanecem
locais; a cópia completa do ELF foi preservada e conferida antes desta publicação.
O build remoto e seus binários continuam no diretório dedicado do CT107.
O parecer intermediário R2 do doador teve seus bytes originais perdidos; a
reconstrução está marcada e não substitui a revisão consolidada preservada.

## Conferência no Linux

```sh
cd docs/evidence/retomada-linux-2026-10-11
sha256sum -c SHA256SUMS
python3 -B donor/verify_preservation.py
python3 -B -m unittest discover -s donor/captures -p 'test_*.py' -v
```

A suíte do doador é local e usa processos descartáveis; não cria CT nem compila
LLVM. A suíte do doador requer UID 1000 e primitivas POSIX, conforme seu README. Para a suíte principal, em um checkout limpo com GCC e Python:

```sh
mkdir -p build-wire
gcc -std=c11 -Wall -Wextra -Werror -shared -fPIC main/observation_wire.c \
  -o build-wire/libobservation_wire.so
WASMHOME_WIRE_LIBRARY="$PWD/build-wire/libobservation_wire.so" \
  python3 -B -m unittest discover -s tests -v
```

Esses comandos descrevem a reprodução. A preparação de um Linux físico novo
ainda não foi realizada. A suíte de evidência separada requer `pyserial`, além
do Python, e fica em `docs/evidence/execucao-2026-10-09/`.

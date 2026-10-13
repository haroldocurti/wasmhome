# Parecer independente P1 — revisão da portabilidade dos testes

Data: 13/10/2026. Checkout revisado: `/home/haroldocurti/Documents/wasmhome`, HEAD `0ba2faab16dc9671f89a663eea8e6d0d1f528997`; diff de código aprovado limitado a `tests/test_e1_runner.py`, SHA-256 `76826491e0f20ffcd7e8cf835738bb9a45ef0b1c23f804864df567f7fed0f0ac`.

Revisei independentemente o diff e repeti a suíte no checkout compartilhado em ambiente isolado sob `/tmp/wasmhome-p1-review-20261013-r2/`. O teste CMake mantém o caminho Windows prioritário e descobre CMake do PATH como fallback. O runner preserva MSVC/Windows quando disponível e usa `cc`/`gcc` no Linux, mantendo a mesma harness, os estágios 0–9 e assertions preexistentes. Não encontrei regressão ou bloqueador no diff.

A validação independente compilou `main/observation_wire.c` com GCC (`-std=c11 -Wall -Wextra -Werror -fPIC -shared`); SHA da biblioteca `667985b4aee880ea755bc4abcd263646c44bcf0b6b4765ba935889b0ea6893b9`, igual ao pacote de evidência do P1. Reexecutei `python -m unittest discover -s tests -v` usando o PowerShell 7.6.6 conferido contra o CT107, CMake 4.4.4 e Ninja 1.13.2 reais. Resultado: 60 testes, zero falhas, um skip esperado para junction Windows. Os testes de CMake e runner compilado passaram; `git diff --check` passou.

A revisão confirma a correção histórica do hash `914495b5323e322cb0da50b0e0f419175d8d5be3cf885100731da55620bdf30f`: ele é de `idf-core-frozen.lock`, não do binário PowerShell. O hash do executável PowerShell 7.6.6 é `3c6e4d629571429cf6cc86db94278e2ce17349edbbfe0a0de007bb6bccdbc208`, igual ao CT107; tar de aquisição `ddbc4a2d113bbd46d283cfedcbcd117a70caefd7673f41f2b4e0000badf103bc`.

Limite: este parecer Linux não valida MSVC ou junctions Windows. Log da execução, comandos, versões e hash da biblioteca estão em `/tmp/wasmhome-p1-review-20261013-r2/`; evidências originais P1 em `/tmp/wasmhome-p1-20261013-r2/`.

# Validação local da composição para publicação

O coordenador executou a suíte principal com o codec C real compilado por MSVC
e a suíte de evidência com o Python 3.11.2/pyserial da toolchain ESP-IDF preservada.
Os logs nesta pasta registram 60 e 21 testes aprovados, sem skips. O código
candidato 187ff96 foi combinado com a documentação pendente e os registros de
retomada; a gravação destes logs/pareceres no arquivo é posterior a essa execução.

Essas suítes cobrem contratos, proveniência, rejeições e preservação de saídas;
não recompilam LLVM/wamrc, firmware B/C nem executam a placa. Os 28 testes do
doador na cópia Linux são registrados separadamente em `../donor/validation/`.
Não há check de CI configurado. A árvore final do commit será validada novamente
antes do push autorizado; seus logs e recibo de comparação HEAD/origin ficam
locais na sessão, sem criar uma cadeia infinita de commits de recibos.

Comandos utilizados na raiz Windows do projeto:

```powershell
./tools/test-platform.ps1
& C:/esp/tools-task8/python_env/idf5.2_py3.11_env/Scripts/python.exe -B `
  -m unittest discover -s docs/evidence/execucao-2026-10-09 -p 'test_*.py' -v
```

O diretório arquivado tem preservação de bytes; `SHA256SUMS` da pasta de sessão
e a conferência de blobs Git verificam que as cópias chegaram iguais ao Git.

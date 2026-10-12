# Revisão da rota userspace de dependências para CT107

Data: 2026-10-10  
Checkout observado: `8b372147e739e46c8952ab8c7907b6c76a76345b`  
Escopo: leitura da simulação apt e da decisão de toolchain; sem instalação, SSH, alterações rastreadas ou prova nova no CT.

## Parecer

A lista apt simulada adiciona 21 pacotes e informa zero upgrades/removals; a versão instalada no snapshot continua `python3.13 3.13.5-2`. Isso reduz o risco de a lista proposta substituir Python, mas a própria saída diz que é apenas uma simulação. Não comprova que a instalação real aconteceu, que o estado atual permaneceu igual, nem que o IDF ou suas dependências funcionem nesse interpretador.

Dada a observação do root de que `venv`, `ssl`, `ctypes` e `lzma` funcionam, enquanto `ensurepip`/pip não estão disponíveis, recomendo preservar o CPython Debian e usar um venv novo sem pip, seguido de bootstrap em espaço do venv com uma única wheel oficial de pip validada por SHA-256. Em 2026-10-10, PyPI lista `pip-26.2.1-py3-none-any.whl`, Python >=3.10, SHA-256 `71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e`; a página também mostra publicação confiável e atestação do projeto. O artefato precisa ser baixado/transferido, re-hasheado contra esse pin mantido localmente e preservado em inventário antes do uso. O modo hash-checking do pip exige pins e hashes para todas as dependências; o bootstrap inicial deve ser offline e restrito àquela wheel.

Não fixaria ainda um comando de extração ou bootstrap como provado: não foi executado no CT, e precisa ser validado em um destino descartável antes do procedimento oficial. O procedimento deve criar um destino exclusivo e inexistente, executar `python3.13 -m venv --without-pip` sem `--system-site-packages`, `--clear` ou `--upgrade`, instalar a wheel somente no site-packages desse venv e então remover qualquer `PYTHONPATH`/ajuste temporário. Confirmar `include-system-site-packages = false`, `sys.prefix != sys.base_prefix`, `sys.executable` e `python -m pip --version` apontando ao destino; nenhum comando deve escrever em `/usr`, `/usr/local`, `~/.local` ou no Python gerido por apt.

A alternativa PyPA virtualenv zipapp também é válida: baixar uma versão e asset imutáveis, verificar digest/proveniência, executar com o Python do sistema para criar destino novo e usar `--no-download` para que a ausência de wheel de seed embutida falhe em vez de buscar uma versão variável. Fixar explicitamente o seeder/versão de pip e redirecionar qualquer cache `app-data` para uma pasta nova e exclusiva do task root. Essa rota traz um bootstrap adicional e cache próprio; por isso é a alternativa de contingência, não a preferida para este CT que já tem `venv`.

## Guards e prova para aceitar Python 3.13.5

1. Registrar antes/depois `python3.13 --version`, caminho resolvido, pacote apt instalado e `sysconfig`; a lista apt deve reportar zero upgrade/removal de Python, e o apt real deve ser revisado antes de executar.
2. Recusar destinos venv/cache existentes ou symlink; criar tudo sob task root exclusivo, como usuário não-root; nunca usar flags que limpem ou atualizem destinos existentes.
3. Preservar o pin/hash e atestação da wheel de bootstrap; usar requisitos/constraints IDF congelados, hashes de todas as distribuições e registro de sdist/wheels compiladas. Não usar versão flutuante, `get-pip.py`, `pip install --user` ou escrita global.
4. Depois da instalação dentro do venv, capturar `<venv>/bin/python -m pip --version`, `pip check`, imports reais de `ssl`, `ctypes`, `lzma` e dependências core, `idf.py --version` = 5.2.6, e execução de configure/reconfigure/build nativo limpo em diretório novo. Preservar logs, ambiente, hashes e resultado.
5. Um resolver report, tag ABI, metadados/classifiers, import isolado de poucos módulos ou simulação apt não fecha o gate. Se resolver/import/build falhar por Python 3.13, aplicar a alternativa userspace 3.11.17 já documentada sem trocar constraints/IDF para ocultar a falha.

## Fontes primárias consultadas

- [pip 26.2.1 no PyPI, arquivos, hash e atestação](https://pypi.org/project/pip/26.2.1/)
- [Documentação Python 3.13 de venv](https://docs.python.org/3.13/library/venv.html)
- [Hash-checking do pip](https://pip.pypa.io/en/stable/topics/secure-installs/)
- [Instalação oficial do virtualenv zipapp](https://virtualenv.pypa.io/en/latest/how-to/install.html)
- [Seeders e cache do virtualenv](https://virtualenv.pypa.io/en/stable/explanation.html)

Este recibo recomenda a preparação do procedimento; não confirma instalação, bootstrap, resolução de dependências, execução do IDF ou aceitação de Python 3.13.5.


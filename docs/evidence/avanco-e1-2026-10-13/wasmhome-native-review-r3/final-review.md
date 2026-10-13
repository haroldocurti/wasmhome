# Revisão final independente do instrumento nativo e do validador E1

Data: 2026-10-13

## Parecer

A revisão r3 não encontrou bloqueador remanescente nos dois achados corrigidos. Aprovo este snapshot somente como base de código validada no host para o próximo gate de compilação ESP-IDF autorizado pelo coordenador.

O parecer fica vinculado ao diff de snapshot SHA-256 `0443a2c36cc6b8f74277369e1fdf1af65f5ede1955761d14e178b5e47eb26f1d` e ao manifesto de hashes de fonte SHA-256 `37dfae04cbb8c430c68a2e8e22b02100c06c416f01b64ca3c0ef4ac2b8f7436d`. Os 13 Git blob IDs presentes no diff r3 correspondem aos arquivos atuais. HEAD permanece `0ba2faab16dc9671f89a663eea8e6d0d1f528997`, tree base `76cd12437c7bf7351880ee7ee03d969248ec5e51`; não editei código nem documentos do repositório.

## Regressões e suíte

Quatro regressões passaram: saturação do cache de UPDATE sem evicção nem falso conflito; recusa quando o status final antecede `after_release`; e limites de `heap_min` acima da capacidade total e do menor heap livre observado.

A suíte completa passou com 81 testes: 80 passaram e 1 foi ignorado por ser um caso negativo específico a junctions do Windows. O log inclui uma mensagem de erro esperada do subprocesso simulado pelo teste de falha do compilador; o teste que verifica preservação do pacote termina aprovado.

Ambiente registrado: Python 3.14.7, GCC 16.2.1, PowerShell 7.6.6, CMake 4.4.4, Ninja 1.13.2; codec `libobservation_wire.so` SHA-256 `667985b4aee880ea755bc4abcd263646c44bcf0b6b4765ba935889b0ea6893b9`. `git diff --check` não reportou problemas.

## Escopo confirmado das correções

O cache de UPDATE conserva todos os 16 UUIDs admitidos durante o boot. UUID já conhecido ainda produz replay da rejeição ou `ATTEMPT_CONFLICT`; UUID novo após a capacidade atingir o limite retorna `CAPACITY_FULL`, não ocupa cache e o handler registra o evento e descarta o pacote sem resposta. A documentação separa nova consulta ao cache — nova mensagem de saída com sequência TX seguinte — da retransmissão byte-idêntica de datagrama já enviado.

O validador agora rejeita `heap_min` acima da capacidade interna ou do menor heap livre observado e exige que o status final venha após `after_release` e após a última linha CSV. Os testes negativos reproduzem os dois defeitos anteriores e falham fechados.

## Preservação e limites

Os registros P0 e r2 anteriores continuam intactos: relatório P0 SHA-256 `d233b570dd9658131c3db15409db6f857297a16635a439c07d5315c8c6030a64`; relatório independente r2 `dacda88dc78511f3d20f50a94bd4979268814101a5ab441fe46621b92761b473`; diff e suíte originais também mantêm seus hashes registrados. Os resultados novos foram salvos separadamente.

Este parecer não atesta compilação ESP-IDF, boot, UDP na placa, comportamento GPIO/DS18B20, flash nem execução física. Esses gates ainda precisam dos logs e artefatos de uma árvore publicada/imóvel, com a configuração efetiva privada preparada pelo coordenador. O skip Windows também permanece sem execução nesta máquina.

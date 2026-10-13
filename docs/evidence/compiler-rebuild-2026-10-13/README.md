# Reconstrução comparativa do compilador — 13/10/2026

O relatório autoral e a revisão independente Luna xhigh confirmam a reprodução
do binário R5 `ff1bf482570dbab67521e0bbb0c682fdbd74f5e784f364eb34bfc9de735861fb`.
O primeiro build usou os flags originais e diferiu no caminho `__FILE__` de um
objeto; o segundo normalizou esse caminho explicitamente com
`-ffile-prefix-map`, em outro diretório e usando apenas o LLVM recém-compilado.
Os 98 archives LLVM e os archives WAMRC coincidem com os de R5. Os dois
compiladores produziram o mesmo AOT para a entrada Wasm de referência conferida.

Esta prova cobre reprodução atual e igualdade de bytes, com pins, comandos e
falhas preservados. O checkout WasmHome foi clonado localmente sem hardlinks e
fixado em `0ba2faab16dc9671f89a663eea8e6d0d1f528997`; LLVM veio do upstream
oficial. O Wasm de referência não foi regenerado nesta etapa. Recibos completos
são JSON locais sem assinatura externa e permanecem na área privada persistente.
O índice publicado é explicitamente derivado e registra seus hashes/tamanhos.
A geração dos payloads e os builds de firmware são gates separados.

As versões com erro de transcrição de árvore foram preservadas em `reports/`;
`report.md`, `independent-review.md` e os índices canônicos usam a árvore real
`76cd12437c7bf7351880ee7ee03d969248ec5e51`. `SHA256SUMS` cobre o pacote publicado;
o manifesto autoral também registra o hash/tamanho do log LLVM integral privado.
Não houve flash nem execução do firmware no MCU nesta prova.

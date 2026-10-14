# Run02 pin correction

This revision supersedes the pin attribution in the preserved R1 metadata packet. The physical run02 execution used:

- Runner R5 SHA-256: `f5628d52d6bf1bd640a498058f2fd11e33031b8cff848ff1c4ed9793ec47f372`.
- Approved root gate R5 SHA-256: `8eabaf4c3636037f116bca9bbce1844dd863ed0f911a17fe50f25f1bbc599be6`.
- Five-input manifest SHA-256: `e24e01c009faf4e8902884d6fcb6a0039db356e45b79fa75597b73ef23116f52`.
- Source commit/tree and build artifact hashes are unchanged from the included summary.

The earlier R1 values (`90806f80…` runner and `72cad455…` root gate) came from R4 and are not the pins for run02. R1 remains preserved at its original path. This correction does not change the captured failure or restoration evidence.

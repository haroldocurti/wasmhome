# Reviewer comparison: five generated payloads

The source artifact tree and the fresh `generation-repro-r1/source/artifacts`
tree were hashed independently over CT107. Each file is byte-identical between
the original build input and the second generation:

| File | Bytes | SHA-256 |
|---|---:|---|
| `benchmark.wasm` | 174 | `d4cc0e280d02a697bb52c391e3b225a0691bc24d14cefb2883e51e085e466847` |
| `benchmark.aot` | 812 | `c4c94777e5f9460b8e60065d5cb89c7e79907cac61720a61f5ebd5467b512d88` |
| `benchmark.wasm.s` | 2,185 | `eab49c09c9c22c1075837f268087e72f536472415abd6812c2292b19a2cf50cf` |
| `benchmark_wasm_embedded.c` | 1,235 | `0c1ad886ae451e8affa974ee79fd0377ab7b77c312039b3c78be331dade5e59b` |
| `benchmark_aot_embedded.c` | 5,272 | `b1e3bc1965b71c4c44a0132cb4cda37f64d521d9a57414949a59d9bde522bb21` |

The second invocation exited 0. Its argument vector is preserved in
`artifact-generation-argv.json`; it ran from a separate source tree at the
same pinned WasmHome commit/tree and used the pinned WASI-SDK package, R5
`wamrc`, its build receipt, and the pinned ESP-IDF checkout.

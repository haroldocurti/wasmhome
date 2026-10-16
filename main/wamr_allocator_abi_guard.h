#ifndef WASMHOME_WAMR_ALLOCATOR_ABI_GUARD_H
#define WASMHOME_WAMR_ALLOCATOR_ABI_GUARD_H

#if !defined(WASM_MEM_ALLOC_WITH_USAGE) || WASM_MEM_ALLOC_WITH_USAGE != 1
#error "WASMHome module scenarios require WAMR's usage-aware allocator ABI"
#endif

#endif

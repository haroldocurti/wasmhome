/*
 * Keep the CMake-generated #22 build provenance file referenced from the
 * application image. The orchestrator checks this exact marker in each app
 * BIN so a detached manifest cannot misstate its endpoint, timeout or CA.
 */
#include <stdint.h>

extern const uint8_t _binary_issue22_build_provenance_txt_start[];

void wasmhome_issue22_build_provenance_retain(void)
{
    /* A volatile load keeps the embedded section reachable under LTO/GC. */
    volatile uint8_t marker = _binary_issue22_build_provenance_txt_start[0];
    (void)marker;
}

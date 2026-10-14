# Run02 failure metadata packet (sanitized, corrected pin attribution)

This published packet contains only metadata, hashes, symbol names/line references and memory measurements. It contains no firmware BIN/ELF files, sdkconfig contents, raw UART logs, flash dumps, MAC/IP/serial identifiers, or network credentials. The private raw run evidence remains in its original restricted store and is identified only by hashes where useful.

This r2 packet corrects the R1 runner/gate attribution after reconciling the execution record: run02 used runner R5 (`f5628d52…`) and the approved R5 root gate (`8eabaf4c…`); the five-input manifest remains `e24e01c0…`. R1 is preserved unchanged and its runner/gate fields should not be used for run02.

The packet preserves the run02 outcome: native completed and its readback matched with zero changes outside ota_0; interp flashed and readback matched but aborted in pthread_self before a workload result; AOT and services versions did not run. Full flash restoration and the post-restore original-app boot matched the pinned d771 baseline. The static ELF memory table is section accounting only, not runtime measurement.

The rebuild note is a plan only. Wait for the exact reviewed fix commit/tree before creating or building new outputs. No hardware I/O was performed while preparing this packet.

The reviewed source correction starts the entire native/interp/AOT benchmark in a detached POSIX pthread with an explicit 8192-byte host stack. The WAMR stack remains a separate 8192-byte runtime setting. Eleven focused host tests passed; the pinned IDF implementation and source change were independently reviewed. The three new-source builds and physical repetition remain pending. The original assertion is preserved rather than bypassed.

[Independent patch review B](thread-patch-review-b.md) · [Independent patch review C](thread-patch-review-c.md).

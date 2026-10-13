# Issue #11 physical harness (prepared, not executed)

`issue11_harness.py` is a UDP observer for a fresh native-service boot. It imports the repository's UDP-v1 codec and has no serial interface. Importing it, running its default plan mode, or running the fake-transport tests does not create a socket or send traffic. Physical I/O requires the explicit `--execute` flag, concrete IPv4 endpoints, firmware identity inputs, and either a boot ID validated against a serial `WASMHOME_EVENT boot_observed` line or `--discover-boot` to capture telemetry before sending the first stimulus.

Use a fresh firmware boot and a distinct output directory for each control version. The root/operator must arrange a recent reset and correlate the serial startup capture with the observed telemetry boot ID before running; the harness only matches the requested ID to a `boot_observed` line and does not establish that the log is recent or that a reset occurred. Start each run with the requested GPIO low. The first control request is at the threshold and must observe low. The script then checks the inclusive upper threshold edge and +250 mC interior while high, silently rejects a wrong-boot request and a conflicting sample ID and verifies that later interior requests retain high, then checks the inclusive lower threshold edge and -250 mC interior while low. It replays the first control datagram byte-for-byte and requires the cached result body/correlation with a new increasing device transmit sequence; the replay tests the cached response and does not request another GPIO application. It sends an `UPDATE_TRIGGER` and requires a correlated BUSY rejection. During the exchanges it records at least five distinct valid DS18B20 sample IDs with age no greater than 500 ms.

The UDP `CONTROL_REQUEST` datagrams intentionally exercise a GPIO control path and can actuate the firmware GPIO. The harness also sends one `UPDATE_TRIGGER` to test its BUSY rejection. It does not request flash erase/write or OTA payload transfer. Response and silence timeouts default to 300 ms; telemetry collection timeout must be finite and no greater than 120 seconds. Every send and receive is retained in `events.jsonl` with raw datagram hex, decoded fields, peer and host monotonic time. The manifest hashes the ELF, BIN, optional sdkconfig, harness and codec; it records boot identity, source revision, endpoint, vector and timeout settings, and explicitly marks that freshness of the external boot evidence is not verified by this utility. Results and SHA256SUMS are saved even if a physical check fails. Output directories are never overwritten, and generated files use private modes.

Run the transport-fake checks with `python3 -B tests/test_issue11_harness.py -v`. An example command shape is below; supply the actual device address and reviewed artifacts at execution time:

```sh
python3 tools/issue11_harness.py \
  --execute --device-ip DEVICE_IPV4 --bind-ip OBSERVER_IPV4 \
  --control-version 1 --boot-id 0xBOOT_ID --boot-evidence /private/startup.log \
  --source-revision COMMIT --elf /private/firmware.elf --bin /private/firmware.bin \
  --sdkconfig /private/sdkconfig --output /private/new-v1-capture
```

No physical run has occurred. Do not use the example placeholders literally. The fake transport tests are not hardware evidence; `observed_gpio` is the firmware's loopback report, not an oscilloscope measurement, and UDP cannot prove a rejected callback was never invoked.

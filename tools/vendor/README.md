`esp_idf_nvs_parser.py` is based on Espressif's Apache-2.0 parser at
`components/nvs_flash/nvs_partition_tool/nvs_parser.py` from the local
ESP-IDF source checkout used for the issue #22 firmware. The upstream source
SHA-256 before the local privacy patch was
`76ecd26b854a20802ec93ea6613a4be5a0b582438d277d3ef4916fa34a09f0d4`.

The only parser change adds a `decode_values=False` option through partition,
page, and entry construction. It preserves structural metadata and raw entry
bytes for CRC checks while leaving every entry key/value undecoded. The
issue #22 wrapper resolves only allowlisted namespace IDs and only assembles
the matching `ledger` and `pending` blobs in memory. It never calls the
upstream JSON/text logger or `dump_raw()` methods.

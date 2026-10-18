(module
  (memory (export "memory") 1 1)

  (func (export "wh_abi_version") (result i32)
    i32.const 1)

  (func (export "wh_control_request") (param i64 i32) (result i32)
    i32.const 0)

  (func (export "probe_zero") (result i32)
    i32.const 0
    i32.const 305419896 ;; 0x12345678
    i32.store
    i32.const 0
    i32.load)

  (func (export "fault_oob")
    i32.const 65533 ;; 4 bytes cross the end of the 65536-byte page
    i32.load
    drop)

  (func (export "fault_div_zero") (param $divisor i32)
    i32.const 1
    local.get $divisor
    i32.div_s
    drop))

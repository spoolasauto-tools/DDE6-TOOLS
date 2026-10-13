#!/usr/bin/env python3
"""
build_boost.py - apply the boost-on-tacho patch to a DDE6 MPC image.

    python3 build_boost.py <in.MPC|fullread.bin> <out>

Patches:
  0x4344AC   sth r28,8(r1)  ->  bl 0x47F600      (the hook)
  0x47F600   the stub from boost_stub.asm
  0x47FFF8   recomputed so sum32(0x400000..0x47FFF7)+word = 0xA03FCA00

Accepts a bare .MPC (0x80000 or 0x80200) or a 2.5 MB full read, and writes
the same shape back out. Verifies the stock bytes before touching anything
and disassembles the result back for inspection.

Requires: pip install capstone
"""
import sys, struct, hashlib

HOOK      = 0x4344AC
HOOK_OLD  = 'b3810008'          # sth r28, 8(r1)
HOOK_NEW  = '4804b155'          # bl  0x47F600
STUB_AT   = 0x47F600
TRAILER   = 0x47FFF8
MAGIC     = 0xA03FCA00

STUB = (
    '9421ffe0' '91210008' '9141000c' '91810010'
    '898db025' '718c0024' '2c0c0000' '41820010'
    '39400000' 'b14dff92' '48000028'
    'a14dff92' '394a0001' '554a043e' 'b14dff92' '2c0a01f4' '40820010'
    '892dff90' '69290001' '992dff90'
    '898dff90' '2c0c0000' '41820038'
    'a98d8b1c' '2c0c0000' '40800008' '39800000'
    '2c0c2800' '40810008' '39802800'
    '39402710' '7d8c51d6' '3940061b' '7d8c5396' 'b1810028' '48000008'
    'b3810028'
    '81210008' '8141000c' '81810010' '38210020' '4e800020'
)

def main():
    if len(sys.argv) < 3:
        print(__doc__); return 1
    src, dst = sys.argv[1], sys.argv[2]
    d = bytearray(open(src, 'rb').read())

    if len(d) == 0x280000:
        base = 0x200000 - 0x400000      # fullread: CPU -> file
        print('input: 2.5 MB full read')
    elif len(d) in (0x80000, 0x80200):
        base = -0x400000                # bare MPC
        print(f'input: bare MPC, 0x{len(d):X}')
    else:
        print(f'unexpected size 0x{len(d):X}'); return 1

    def off(cpu): return cpu + base

    cur = d[off(HOOK):off(HOOK)+4].hex()
    if cur == HOOK_NEW:
        print('hook already patched')
    elif cur != HOOK_OLD:
        print(f'ABORT: 0x{HOOK:06X} is {cur}, expected {HOOK_OLD}')
        return 1
    else:
        d[off(HOOK):off(HOOK)+4] = bytes.fromhex(HOOK_NEW)
        print(f'0x{HOOK:06X}: {HOOK_OLD} -> {HOOK_NEW}')

    stub = bytes.fromhex(STUB)
    region = d[off(STUB_AT):off(STUB_AT)+len(stub)]
    if region != stub and region != b'\xff' * len(stub):
        print(f'WARNING: 0x{STUB_AT:06X} is neither blank nor our stub; overwriting')
    d[off(STUB_AT):off(STUB_AT)+len(stub)] = stub
    print(f'0x{STUB_AT:06X}: stub written, {len(stub)} bytes / {len(stub)//4} instructions')

    mpc0 = off(0x400000)
    s = 0
    for k in range(mpc0, off(TRAILER), 4):
        s = (s + struct.unpack('>I', d[k:k+4])[0]) & 0xFFFFFFFF
    word = (MAGIC - s) & 0xFFFFFFFF
    d[off(TRAILER):off(TRAILER)+4] = struct.pack('>I', word)
    chk = (s + word) & 0xFFFFFFFF
    print(f'0x{TRAILER:06X}: word = 0x{word:08X}, total = 0x{chk:08X} '
          f'{"OK" if chk == MAGIC else "FAIL"}')
    assert chk == MAGIC

    open(dst, 'wb').write(bytes(d))
    print(f'wrote {dst}  md5 {hashlib.md5(d).hexdigest()}  size 0x{len(d):X}')

    try:
        from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN
    except ImportError:
        print('(capstone not installed, skipping readback)'); return 0
    md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    R13 = 0x7FFFF0
    names = {0x7FB015: 'raw button', 0x7F8B0C: 'charge air',
             0x7FFF80: 'mode', 0x7FFF82: 'counter'}
    print('\nreadback:')
    import re
    for ins in md.disasm(bytes(d[off(STUB_AT):off(STUB_AT)+len(stub)]), STUB_AT):
        ann = ''
        m = re.search(r'(-?0x[0-9a-f]+)\(r13\)', ins.op_str)
        if m:
            v = int(m.group(1), 16)
            if v >= 0x8000: v -= 0x10000
            ea = (R13 + v) & 0xFFFFFF
            ann = f'   ; [0x{ea:06X}] {names.get(ea,"")}'
        print(f'  {ins.address:06X}  {ins.bytes.hex()}  {ins.mnemonic:8s} {ins.op_str}{ann}')
    return 0

if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""
dde6re.py - PowerPC reverse engineering harness for BMW DDE6 (Bosch EDC16C35, MPC563).

Handles the two-segment address space, resolves the global register bases so that
r13/r2/r14/r15-relative operands print as real addresses, finds function
boundaries, builds xrefs, dumps the OSEK runnable tables and scans Bosch map
headers.

Usage:
    python3 dde6re.py <file(s)> info
    python3 dde6re.py <file(s)> dis    <start> [end]
    python3 dde6re.py <file(s)> func   <addr>
    python3 dde6re.py <file(s)> xref   <addr>
    python3 dde6re.py <file(s)> rw     <addr>
    python3 dde6re.py <file(s)> runnables [tcb]
    python3 dde6re.py <file(s)> maps   [lo] [hi]
    python3 dde6re.py <file(s)> csum

<file(s)> is either one 2.5 MB full read, or an .FLS plus an .MPC.
Addresses are CPU addresses, hex, 0x optional.

    python3 dde6re.py fullread.bin dis 0x4340B8 0x434200
    python3 dde6re.py e39.FLS e39.MPC runnables

Requires: pip install capstone
"""
import sys, os, struct

# ---------------------------------------------------------------- globals
# Proven out of the startup code of SW 1037390198. If you work on a different
# SW, re-derive them: find the four "mr rX,r3 ; blr" setter stubs in the FLS,
# then the calls to them, and read r3 at each call site.
R2, R13, R14, R15 = 0x000480F0, 0x7FFFF0, 0x0004662C, 0x000501C8
GLOBAL_BASE = {2: R2, 13: R13, 14: R14, 15: R15}

SIGNATURE = bytes.fromhex('FADECAFECAFEAFFE')   # checksum descriptor marker
CSUM_MAGIC = 0xD01FE500                          # every real range sums to this
MPC_TRAILER_MAGIC = 0xA03FCA00                   # sum + word@0x47FFF8

# ---------------------------------------------------------------- segments
class Image:
    """FLS at 0x000000 (2 MB), MPC at 0x400000 (512 KB)."""
    def __init__(self, paths):
        self.segs = []
        blobs = [(p, open(p, 'rb').read()) for p in paths]
        if len(blobs) == 1 and len(blobs[0][1]) == 0x280000:
            d = blobs[0][1]
            self.segs = [('FLS', 0x000000, d[:0x200000]),
                         ('MPC', 0x400000, d[0x200000:0x280000])]
        else:
            for p, d in blobs:
                if len(d) >= 0x180000:
                    self.segs.append(('FLS', 0x000000, d))
                else:
                    self.segs.append(('MPC', 0x400000, d))
        self.segs.sort(key=lambda s: s[1])

    def seg(self, a):
        for nm, b, d in self.segs:
            if b <= a < b + len(d):
                return nm, b, d
        return None, None, None

    def rd(self, a, n):
        nm, b, d = self.seg(a)
        if d is None:
            return None
        x = d[a - b:a - b + n]
        return x if len(x) == n else None

    def u8(self, a):
        x = self.rd(a, 1);  return x[0] if x else None
    def u16(self, a):
        x = self.rd(a, 2);  return struct.unpack('>H', x)[0] if x else None
    def s16(self, a):
        x = self.rd(a, 2);  return struct.unpack('>h', x)[0] if x else None
    def u32(self, a):
        x = self.rd(a, 4);  return struct.unpack('>I', x)[0] if x else None

    def find(self, pat):
        for nm, lo, hi in self.ranges():
            i = self.data(nm).find(pat)
            if i >= 0:
                return lo + i
        return -1

    def ranges(self):
        return [(nm, b, b + len(d)) for nm, b, d in self.segs]

def sx16(v): return v - 0x10000 if v & 0x8000 else v

# ---------------------------------------------------------------- branches
def br_target(a, w):
    op = w >> 26
    if op == 18:
        li = w & 0x03FFFFFC
        if li & 0x02000000: li -= 0x04000000
        return (li & 0xFFFFFFFF) if (w & 2) else (a + li) & 0xFFFFFFFF
    if op == 16:
        bd = w & 0xFFFC
        if bd & 0x8000: bd -= 0x10000
        return (bd & 0xFFFFFFFF) if (w & 2) else (a + bd) & 0xFFFFFFFF
    return None

def is_call(w):   return (w >> 26) == 18 and (w & 1)
def is_blr(w):    return w == 0x4E800020
def is_uncond(w): return (w >> 26) == 18 and not (w & 1)

# ---------------------------------------------------------------- disasm
LOAD_OPS  = {32:'lwz',33:'lwzu',34:'lbz',35:'lbzu',40:'lhz',41:'lhzu',42:'lha',43:'lhau'}
STORE_OPS = {36:'stw',37:'stwu',38:'stb',39:'stbu',44:'sth',45:'sthu'}

def _md():
    from capstone import Cs, CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN
    return Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)

def region(img, a):
    nm, b, d = img.seg(a)
    if nm: return nm
    if 0x7F8000 <= a < 0x802400: return 'RAM'
    if 0x8C0000 <= a < 0x900000: return 'RAM_MIRROR'
    if 0x700000 <= a < 0x710000: return 'PERIPH'
    return '?'

def annotate(img, a, w):
    """Resolve r13/r2/r14/r15-relative loads and stores to absolute addresses."""
    op = w >> 26
    rA = (w >> 16) & 31
    d  = w & 0xFFFF
    if (op in LOAD_OPS or op in STORE_OPS) and rA in GLOBAL_BASE:
        ea = (GLOBAL_BASE[rA] + sx16(d)) & 0xFFFFFFFF
        s = f'   ; EA=0x{ea:06X} {region(img, ea)}'
        v = img.u32(ea)
        if v is not None and region(img, ea) in ('FLS', 'MPC'):
            s += f'  -> 0x{v:08X}'
        return s
    if op == 15 and ((w >> 16) & 31) == 0:        # lis
        return f'   ; = 0x{(sx16(d) << 16) & 0xFFFFFFFF:08X}'
    if is_call(w) or (op == 18):
        t = br_target(a, w)
        if t is not None:
            return f'   ; -> 0x{t:06X} {region(img, t)}'
    return ''

def disasm(img, start, end):
    md = _md()
    data = img.rd(start, end - start)
    if data is None:
        print(f'  (unmapped 0x{start:06X})'); return
    for ins in md.disasm(data, start):
        w = img.u32(ins.address)
        print(f'  {ins.address:06X}  {ins.bytes.hex()}  {ins.mnemonic:9s} {ins.op_str}{annotate(img, ins.address, w)}')

# ---------------------------------------------------------------- functions
def func_start(img, a, limit=0x2000):
    """Walk back to a prologue. A real entry is stwu r1,-N(r1) then mflr r0."""
    p = a & ~3
    for _ in range(limit // 4):
        w = img.u32(p)
        if w is None: break
        if (w >> 16) == 0x9421 and img.u32(p + 4) == 0x7C0802A6:
            return p
        if (w >> 16) == 0x9421:
            return p
        p -= 4
    return a & ~3

def func_end(img, a, limit=0x4000):
    p = a & ~3
    for _ in range(limit // 4):
        w = img.u32(p)
        if w is None: break
        if is_blr(w):
            return p + 4
        p += 4
    return (a & ~3) + limit

def xrefs(img, target):
    out = []
    for nm, lo, hi in img.ranges():
        a = lo
        while a < hi:
            w = img.u32(a)
            if w is not None and (w >> 26) == 18:
                if br_target(a, w) == target:
                    out.append((a, 'bl' if (w & 1) else 'b'))
            a += 4
    return out

def rw_sites(img, ea):
    """Every instruction that reads or writes this absolute address via a global base."""
    rd, wr = [], []
    for base_reg, base in GLOBAL_BASE.items():
        off = ea - base
        if not (-0x8000 <= off <= 0x7FFF):
            continue
        d = off & 0xFFFF
        for nm, lo, hi in img.ranges():
            a = lo
            while a < hi:
                w = img.u32(a)
                if w is not None and (w & 0xFFFF) == d and ((w >> 16) & 31) == base_reg:
                    op = w >> 26
                    if op in STORE_OPS: wr.append((a, STORE_OPS[op]))
                    elif op in LOAD_OPS: rd.append((a, LOAD_OPS[op]))
                a += 4
    return rd, wr

# ---------------------------------------------------------------- OSEK tables
def runnables(img, tcb=0x043E04, count=48):
    """Dump a TCB runnable pointer table. Header 0x00..0x17, pointers from +0x18."""
    print(f'TCB 0x{tcb:06X}  (header 0x00..0x17, runnables from +0x18)')
    for i in range(count):
        a = tcb + 0x18 + i * 4
        v = img.u32(a)
        if v is None: break
        tag = ''
        if 0x400000 <= v < 0x480000:
            tag = ' <- code'
        elif v in (0, 0xFFFFFFFF):
            tag = ' (empty)'
        print(f'  #{i:<3d} @0x{a:06X}  0x{v:08X}{tag}')

def scan_runnable_tables(img, lo=0x043000, hi=0x045000):
    """Find words in the FLS that point at MPC code - these are dispatch tables."""
    print(f'code pointers in 0x{lo:06X}..0x{hi:06X}:')
    run = []
    for a in range(lo, hi, 4):
        v = img.u32(a)
        if v is not None and 0x400000 <= v < 0x480000 and (v & 3) == 0:
            run.append((a, v))
    for a, v in run:
        print(f'  0x{a:06X} -> 0x{v:06X}')
    print(f'  ({len(run)} pointers)')

# ---------------------------------------------------------------- Bosch maps
def find_map_header(img, data_addr, maxdim=40):
    """Bosch layout: [dy][dx][y-axis][x-axis][DATA]. Solve it without a DAMOS."""
    best = None
    for tot in range(2, 2 * maxdim + 1):
        hs = data_addr - 4 - 2 * tot
        if hs < 0: break
        Y, X = img.u16(hs), img.u16(hs + 2)
        if Y is None or X is None: continue
        if 1 <= Y <= maxdim and 1 <= X <= maxdim and Y + X == tot:
            ya = [img.u16(hs + 4 + 2 * i) for i in range(Y)]
            xa = [img.u16(hs + 4 + 2 * Y + 2 * i) for i in range(X)]
            mono = all(ya[i] <= ya[i+1] for i in range(Y-1)) and \
                   all(xa[i] <= xa[i+1] for i in range(X-1))
            score = (2 if mono else 0) + (1 if Y > 1 and X > 1 else 0)
            if best is None or score > best[0]:
                best = (score, Y, X, hs, ya, xa)
    return None if best is None else best[1:]

def scan_maps(img, lo=0x0C0000, hi=0x0FE000, maxdim=32, mincells=8):
    out = []
    for hs in range(lo, hi, 2):
        Y, X = img.u16(hs), img.u16(hs + 2)
        if Y is None or X is None: continue
        if not (1 <= Y <= maxdim and 1 <= X <= maxdim): continue
        if Y * X < mincells: continue
        da = hs + 4 + 2 * (Y + X)
        if da + 2 * Y * X > hi: continue
        ya = [img.u16(hs + 4 + 2 * i) for i in range(Y)]
        xa = [img.u16(hs + 4 + 2 * Y + 2 * i) for i in range(X)]
        if not all(ya[i] < ya[i+1] for i in range(Y-1)): continue
        if not all(xa[i] < xa[i+1] for i in range(X-1)): continue
        if ya[-1] > 0xF000 or xa[-1] > 0xF000: continue
        out.append((da, Y, X, ya, xa))
    return out

# ---------------------------------------------------------------- checksums
def checksums(img):
    """Find FA DE CA FE descriptors and test each range against 0xD01FE500."""
    print('checksum descriptors (range sits immediately BEFORE the signature):')
    for nm, lo, hi in img.ranges():
        _, base, blob = img.seg(lo)
        idx = 0
        while True:
            i = blob.find(SIGNATURE, idx)
            if i < 0: break
            idx = i + 1
            if i < 8: continue
            rlo = struct.unpack('>I', blob[i-8:i-4])[0]
            rhi = struct.unpack('>I', blob[i-4:i])[0]
            if not (rlo < rhi < 0x480000): continue
            s = 0; ok = True
            for k in range(rlo, rhi + 1, 4):
                v = img.u32(k)
                if v is None: ok = False; break
                s = (s + v) & 0xFFFFFFFF
            if not ok:
                print(f'  desc@0x{base+i:06X}  0x{rlo:06X}..0x{rhi:06X}  (not fully mapped)')
            else:
                print(f'  desc@0x{base+i:06X}  0x{rlo:06X}..0x{rhi:06X}  sum=0x{s:08X}  '
                      f'{"OK" if s == CSUM_MAGIC else "** MISMATCH **"}')
    s = 0
    for k in range(0x400000, 0x47FFF8, 4):
        v = img.u32(k)
        if v is None: s = None; break
        s = (s + v) & 0xFFFFFFFF
    if s is not None:
        w = img.u32(0x47FFF8)
        print(f'  MPC trailer: sum+word@0x47FFF8 = 0x{(s+w)&0xFFFFFFFF:08X} '
              f'(want 0x{MPC_TRAILER_MAGIC:08X})  word=0x{w:08X}')

def fix_mpc_trailer(path):
    """Recompute word@0x47FFF8 so the MPC half checksums. Edits in place."""
    d = bytearray(open(path, 'rb').read())
    off = 0x200000 if len(d) == 0x280000 else 0
    s = 0
    for k in range(off, off + 0x7FFF8, 4):
        s = (s + struct.unpack('>I', d[k:k+4])[0]) & 0xFFFFFFFF
    word = (MPC_TRAILER_MAGIC - s) & 0xFFFFFFFF
    d[off+0x7FFF8:off+0x7FFFC] = struct.pack('>I', word)
    open(path, 'wb').write(d)
    print(f'{path}: trailer word set to 0x{word:08X}')

# ---------------------------------------------------------------- cli

def find_bases(img):
    """Locate the four global base registers by their mr rX,r3 ; blr stubs,
    then read the lis/addi pairs in the init routine that calls them."""
    import struct as _s
    fls = img.seg_bytes('FLS') if hasattr(img, 'seg_bytes') else None
    PAT = {'r1': '7C611B784E800020', 'r2': '7C621B784E800020',
           'r13': '7C6D1B784E800020', 'r14': '7C6E1B784E800020'}
    out = {}
    for nm, h in PAT.items():
        a = img.find(bytes.fromhex(h))
        out[nm] = a
    return out


def findhook(img):
    """Locate the boost hook site for this software version.

    The rpm path in the CAN11h formatter is the same shape in every DDE6
    image seen so far:

        cmpwi  rN, 0x2800        <- the 10240 hPa clamp, the anchor
        ble    +0xC
        li     rM, 0x2800
        b      +8
        clrlwi rM, rN, 0x10
        lhz    r12, D(r27)       <- MUL
        lhz    r4,  D-2(r27)     <- DIV
        mullw  r3, rM, r12
        bl     <divide helper>
        addi   rN, r3, 0
        sth    rN, 8(r1)         <- the hook, anchor + 0x28

    Reports the hook address, which register carries rpm, the calibration
    offsets, and somewhere to put the stub.
    """
    import struct as _s
    lo, hi = None, None
    for nm, a, b in img.ranges():
        if nm == 'MPC':
            lo, hi = a, b
    if lo is None:
        print('no MPC segment in this image'); return

    anchors = []
    for a in range(lo, hi - 4, 4):
        w = img.u32(a)
        if (w >> 26) in (10, 11) and (w & 0xFFFF) == 0x2800:
            anchors.append(a)

    if not anchors:
        print('no 0x2800 clamp found - this image does not match the known shape')
        return

    print(f'{len(anchors)} clamp candidate(s)\n')
    for a in anchors:
        h = a + 0x28
        w = img.u32(h)
        op, rS, rA, d = w >> 26, (w >> 21) & 0x1F, (w >> 16) & 0x1F, w & 0xFFFF
        is_sth = (op == 44 and rA == 1 and d == 8)
        is_bl = (w >> 26) == 18
        wmul, wdiv = img.u32(a + 0x14), img.u32(a + 0x18)
        moff = wmul & 0xFFFF if (wmul >> 26) == 40 else None
        doff = wdiv & 0xFFFF if (wdiv >> 26) == 40 else None
        print(f'  clamp  0x{a:06X}')
        if is_sth:
            print(f'  HOOK   0x{h:06X}   sth r{rS}, 8(r1)        <- patch this')
            print(f'         rpm is carried in r{rS}; the stub must store r{rS}')
        elif is_bl:
            print(f'  HOOK   0x{h:06X}   already patched (bl)')
        else:
            print(f'  0x{h:06X} is not an sth into 8(r1) - not the rpm path, skipping')
            print()
            continue
        if moff is not None and doff is not None:
            print(f'         scaling: MUL at r27+0x{moff:02X}, DIV at r27+0x{doff:02X}')
        print()

    free = []
    run_s = None
    for a in range(lo, hi):
        if img.u8(a) == 0xFF:
            if run_s is None: run_s = a
        else:
            if run_s is not None and a - run_s >= 0xB0:
                free.append((run_s, a - run_s))
            run_s = None
    if run_s is not None and hi - run_s >= 0xB0:
        free.append((run_s, hi - run_s))
    print('  free FF runs big enough for the stub (>= 0xB0 bytes):')
    for o, n in sorted(free, key=lambda r: -r[1])[:6]:
        print(f'    0x{o:06X}  {n} bytes')


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    paths = []
    while args and os.path.exists(args[0]):
        paths.append(args.pop(0))
    if not paths:
        print('no input file'); return
    img = Image(paths)
    cmd = args.pop(0) if args else 'info'
    A = lambda i, dflt=None: int(args[i], 16) if len(args) > i else dflt

    if cmd == 'info':
        print('segments:')
        for nm, lo, hi in img.ranges():
            print(f'  {nm}  0x{lo:06X}..0x{hi-1:06X}  ({hi-lo} bytes)')
        print(f'bases: r2=0x{R2:06X} r13=0x{R13:06X} r14=0x{R14:06X} r15=0x{R15:06X}')
    elif cmd == 'dis':
        s = A(0); e = A(1, s + 0x80)
        disasm(img, s, e)
    elif cmd == 'func':
        a = A(0)
        s = func_start(img, a); e = func_end(img, s)
        print(f'function 0x{s:06X}..0x{e:06X}  ({(e-s)//4} instructions)')
        x = xrefs(img, s)
        print(f'callers: {[hex(p) for p, k in x] or "none (likely an OSEK runnable)"}')
        disasm(img, s, e)
    elif cmd == 'xref':
        for p, k in xrefs(img, A(0)):
            print(f'  0x{p:06X}  {k}')
    elif cmd == 'rw':
        ea = A(0)
        rd, wr = rw_sites(img, ea)
        print(f'0x{ea:06X}:')
        print('  WRITERS: ' + (' '.join(f'{p:06X}:{m}' for p, m in wr) or '(none)'))
        print('  readers: ' + (' '.join(f'{p:06X}:{m}' for p, m in rd) or '(none)'))
    elif cmd == 'runnables':
        if args: runnables(img, A(0))
        else:    scan_runnable_tables(img)
    elif cmd == 'maps':
        lo = A(0, 0x0C0000); hi = A(1, 0x0FE000)
        ms = scan_maps(img, lo, hi)
        for da, Y, X, ya, xa in ms:
            print(f'  0x{da:06X}  {X}x{Y}  y={ya[:5]}..{ya[-1]}  x={xa[:5]}..{xa[-1]}')
        print(f'  ({len(ms)} maps)')
    elif cmd == 'csum':
        checksums(img)
    elif cmd == 'findhook':
        findhook(img)
    elif cmd == 'bases':
        for nm, a in find_bases(img).items():
            print(f'  {nm:4s} stub at ' + (f'0x{a:06X}' if a and a > 0 else 'NOT FOUND'))
    elif cmd == 'fixtrailer':
        fix_mpc_trailer(paths[0])
    else:
        print(__doc__)

if __name__ == '__main__':
    main()

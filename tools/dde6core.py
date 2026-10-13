#!/usr/bin/env python3
"""
dde6core.py - analysis engine for Bosch EDC16C35 / BMW DDE6 images.

Everything here is discovered by scanning the loaded image. No address is
tied to a software version. Import this from a CLI or a GUI.

    rom = Rom(); rom.load_full('fullread.bin')
    a = analyse(rom)
    print(a['sw'], a['hook'], a['csum'])
"""
import re
import struct
import hashlib

FLS_SIZE   = 0x200000
MPC_SIZE   = 0x80000
FULL_SIZE  = FLS_SIZE + MPC_SIZE
MPC_CPU    = 0x400000
R13        = 0x7FFFF0

MAGIC      = 0xD01FE500                       # every protected range sums to this
MAGIC_MPC  = 0xA03FCA00                       # sum(0x400000..0x47FFF7) + word@0x47FFF8
MPC_TRAIL  = 0x47FFF8
SIG        = bytes.fromhex('FADECAFECAFEAFFE')

STATE_MODE = 0x7FFF80                         # boost display mode, byte
STATE_CTR  = 0x7FFF82                         # button hold counter, halfword

sx  = lambda v: v - 0x10000 if v & 0x8000 else v
dec = lambda w: (w >> 26, (w >> 21) & 0x1F, (w >> 16) & 0x1F, w & 0xFFFF)
u32 = lambda b, o: struct.unpack_from('>I', b, o)[0]
u16 = lambda b, o: struct.unpack_from('>H', b, o)[0]
LDST = set(range(32, 48))


def sum32(b, lo, hi_incl):
    t = 0
    for o in range(lo, hi_incl - 2, 4):
        t = (t + u32(b, o)) & 0xFFFFFFFF
    return t


# ======================================================================
class Rom:
    """FLS and MPC held separately. A full read is split on load."""

    def __init__(self):
        self.fls = self.mpc = None
        self.fls_orig = self.mpc_orig = None
        self.fls_path = self.mpc_path = self.full_path = None
        self.mpc_pad = b''                     # trailing FF of a 0x80200 .MPC

    # ---- loading ----
    def load_full(self, path):
        d = open(path, 'rb').read()
        if len(d) != FULL_SIZE:
            raise ValueError(f'a full read is 0x{FULL_SIZE:X} bytes; this is 0x{len(d):X}')
        self.fls_orig = bytes(d[:FLS_SIZE]); self.fls = bytearray(self.fls_orig)
        self.mpc_orig = bytes(d[FLS_SIZE:]);  self.mpc = bytearray(self.mpc_orig)
        self.mpc_pad = b''
        self.full_path = path
        self.fls_path = self.mpc_path = None

    def load_fls(self, path):
        d = open(path, 'rb').read()
        if len(d) != FLS_SIZE:
            raise ValueError(f'an FLS is 0x{FLS_SIZE:X} bytes; this is 0x{len(d):X}')
        self.fls_orig = bytes(d); self.fls = bytearray(d)
        self.fls_path = path; self.full_path = None

    def load_mpc(self, path):
        d = open(path, 'rb').read()
        if len(d) == MPC_SIZE:
            body, pad = d, b''
        elif len(d) == MPC_SIZE + 0x200:
            body, pad = d[:MPC_SIZE], d[MPC_SIZE:]       # padding is at the END
            if any(x != 0xFF for x in pad):
                raise ValueError('0x80200 file whose tail is not FF padding')
        else:
            raise ValueError(f'an MPC is 0x{MPC_SIZE:X} or 0x{MPC_SIZE+0x200:X} bytes; '
                             f'this is 0x{len(d):X}')
        self.mpc_orig = bytes(body); self.mpc = bytearray(body)
        self.mpc_pad = pad
        self.mpc_path = path; self.full_path = None

    # ---- saving ----
    def save(self, path, which):
        if which == 'full':
            if self.fls is None or self.mpc is None:
                raise ValueError('a full read needs both chips loaded')
            open(path, 'wb').write(bytes(self.fls) + bytes(self.mpc))
        elif which == 'fls':
            open(path, 'wb').write(bytes(self.fls))
        elif which == 'mpc':
            open(path, 'wb').write(bytes(self.mpc) + self.mpc_pad)
        else:
            raise ValueError(which)

    # ---- helpers ----
    @property
    def have_fls(self): return self.fls is not None
    @property
    def have_mpc(self): return self.mpc is not None

    def mo(self, cpu):  return cpu - MPC_CPU            # MPC cpu -> index
    def mw(self, cpu):  return u32(self.mpc, self.mo(cpu))

    def segs(self):
        """(name, buffer, cpu_base) for whatever is loaded."""
        out = []
        if self.have_fls: out.append(('FLS', self.fls, 0))
        if self.have_mpc: out.append(('MPC', self.mpc, MPC_CPU))
        return out

    def ident(self):
        if not self.have_fls: return (None, None)
        t = bytes(self.fls)
        sw = re.findall(rb'1037\d{6}', t)
        hw = re.findall(rb'0281\d{6}', t)
        return (sw[0].decode() if sw else None, hw[0].decode() if hw else None)

    def md5s(self):
        d = {}
        if self.have_fls: d['FLS'] = hashlib.md5(bytes(self.fls)).hexdigest()
        if self.have_mpc: d['MPC'] = hashlib.md5(bytes(self.mpc)).hexdigest()
        return d


# ======================================================================
#  checksums - discovered, never hardcoded
# ======================================================================
def find_csum(rom):
    """Every protected range, from its FA DE CA FE descriptor.

    Descriptor is [lo:4][hi:4][FA DE CA FE CA FE AF FE] with the range BEFORE
    the signature. A range is accepted only if it actually sums to the magic.
    The correction word is the last word of the range, which is how Bosch
    built them: recomputing it reproduces the stored value exactly in every
    range of every image tested.
    """
    out = []
    for nm, buf, base in rom.segs():
        b = bytes(buf); i = 0
        while True:
            i = b.find(SIG, i)
            if i < 0: break
            if i >= 8:
                lo, hi = u32(b, i - 8), u32(b, i - 4)
                if (base <= lo < hi < base + len(b) and (lo & 3) == 0
                        and ((hi + 1) & 3) == 0 and hi - lo >= 0x40):
                    ok = sum32(b, lo - base, hi - base) == MAGIC
                    out.append({'chip': nm, 'lo': lo, 'hi': hi, 'word': hi - 3,
                                'base': base, 'verified': ok})
            i += 1
    # de-duplicate, innermost first so a nested range is fixed before its parent
    seen, uniq = set(), []
    for r in sorted(out, key=lambda r: (r['chip'], r['hi'] - r['lo'])):
        k = (r['chip'], r['lo'], r['hi'])
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq


def mpc_trailer_ok(rom):
    if not rom.have_mpc: return None
    v = (sum32(rom.mpc, 0, rom.mo(MPC_TRAIL)) + rom.mw(MPC_TRAIL)) & 0xFFFFFFFF
    return v == MAGIC_MPC


def repair_csum(rom, ranges):
    """Recompute every range that does not currently sum, regardless of what
    changed. For images that arrived with broken checksums."""
    log = []
    for r in ranges:
        buf = rom.fls if r['chip'] == 'FLS' else rom.mpc
        base = r['base']; lo_i, hi_i = r['lo'] - base, r['hi'] - base
        if sum32(buf, lo_i, hi_i) == MAGIC: continue
        w = r['word'] - base
        struct.pack_into('>I', buf, w, 0)
        delta = (MAGIC - sum32(buf, lo_i, hi_i)) & 0xFFFFFFFF
        struct.pack_into('>I', buf, w, delta)
        log.append(f"{r['chip']} 0x{r['lo']:06X}-0x{r['hi']:06X}: "
                   f"word@0x{r['word']:06X} = {delta:08X}")
    return log


def fix_csum(rom, changed, ranges):
    """Recompute the correction word of every range containing a changed byte.

    `changed` is {'FLS': set(indices), 'MPC': set(indices)}.
    `ranges` MUST have been discovered before the image was modified: a range
    whose contents just changed no longer sums to the magic, so rediscovering
    it here would mark it unverified and skip the very range that needs fixing.
    Innermost first, so a nested range's word is written before its parent is
    summed.
    """
    log = []
    for r in ranges:
        if not r['verified']:
            continue
        buf = rom.fls if r['chip'] == 'FLS' else rom.mpc
        base = r['base']
        lo_i, hi_i = r['lo'] - base, r['hi'] - base
        touched = any(lo_i <= x <= hi_i for x in changed.get(r['chip'], ()))
        if not touched:
            continue
        w = r['word'] - base
        struct.pack_into('>I', buf, w, 0)
        delta = (MAGIC - sum32(buf, lo_i, hi_i)) & 0xFFFFFFFF
        struct.pack_into('>I', buf, w, delta)
        changed.setdefault(r['chip'], set()).update(range(w, w + 4))
        log.append(f"{r['chip']} 0x{r['lo']:06X}-0x{r['hi']:06X}: "
                   f"word@0x{r['word']:06X} = {delta:08X}")
    return log


def verify_csum(rom):
    """Re-sum every discovered range. Returns (ok_list, bad_list)."""
    ok, bad = [], []
    for r in find_csum(rom):
        buf = rom.fls if r['chip'] == 'FLS' else rom.mpc
        v = sum32(buf, r['lo'] - r['base'], r['hi'] - r['base'])
        (ok if v == MAGIC else bad).append(
            (f"{r['chip']} 0x{r['lo']:06X}-0x{r['hi']:06X}", v))
    return ok, bad


# ======================================================================
#  locators
# ======================================================================
def find_bases(rom):
    """The four mr rX,r3 ; blr stubs, then lis/addi pairs in their caller."""
    if not (rom.have_fls and rom.have_mpc): return None
    f = bytes(rom.fls)
    PAT = {'r1': '7C611B784E800020', 'r2': '7C621B784E800020',
           'r13': '7C6D1B784E800020', 'r14': '7C6E1B784E800020'}
    stubs = {k: f.find(bytes.fromhex(v)) for k, v in PAT.items()}
    if stubs['r1'] < 0: return None
    m = bytes(rom.mpc); caller = None
    for o in range(0, len(m) - 4, 4):
        w = u32(m, o)
        if (w >> 26) != 18 or not (w & 1) or (w & 2): continue
        d = w & 0x03FFFFFC
        if d & 0x02000000: d -= 0x04000000
        if o + MPC_CPU + d == stubs['r1']:
            caller = o + MPC_CPU; break
    vals = []
    if caller is not None:
        o = caller - MPC_CPU - 0x20
        while len(vals) < 4 and 0 <= o < len(m) - 20:
            w = u32(m, o)
            if (w >> 26) == 15 and ((w >> 21) & 0x1F) == 3 and ((w >> 16) & 0x1F) == 0:
                for dd in (4, 8, 12, 16):
                    x = u32(m, o + dd)
                    if (x >> 26) == 14 and ((x >> 21) & 0x1F) == 3 and ((x >> 16) & 0x1F) == 3:
                        vals.append((((w & 0xFFFF) << 16) + sx(x & 0xFFFF)) & 0xFFFFFFFF)
                        break
            o += 4
    names = ['r1', 'r2', 'r13', 'r14']
    return {'stubs': stubs, 'init': caller,
            'vals': dict(zip(names, vals)) if len(vals) == 4 else {}}


def find_variant(rom):
    """lbz rD,0(rA) ; cmpwi rD,3 ; stb rD,d(r13) - the CAN11h selector."""
    if not rom.have_mpc: return []
    m = bytes(rom.mpc); out = []
    for o in range(0, len(m) - 12, 4):
        w0, w1, w2 = u32(m, o), u32(m, o + 4), u32(m, o + 8)
        o0, d0, a0, i0 = dec(w0); o1, _, _, i1 = dec(w1); o2, d2, a2, i2 = dec(w2)
        if o0 != 34 or i0 != 0: continue
        if o1 != 11 or ((w1 >> 23) & 3) != 0 or i1 != 3: continue
        if ((w1 >> 16) & 0x1F) != d0: continue
        if o2 != 38 or a2 != 13 or d2 != d0: continue
        out.append({'lbz': o + MPC_CPU, 'reg': d0,
                    'mirror': (R13 + sx(i2)) & 0xFFFFFFFF,
                    'forced': w0 == (0x38000003 | (d0 << 21))})
    return out


def find_hook(rom):
    """cmpwi rN,0x2800 (the 10240 hPa clamp). The rpm store is anchor+0x28."""
    if not rom.have_mpc: return []
    m = bytes(rom.mpc); out = []
    for o in range(0, len(m) - 0x2C, 4):
        w = u32(m, o)
        if (w >> 26) not in (10, 11) or (w & 0xFFFF) != 0x2800: continue
        h = o + 0x28
        oh, rS, rA, d = dec(u32(m, h))
        if oh == 44 and rA == 1:
            out.append({'clamp': o + MPC_CPU, 'hook': h + MPC_CPU,
                        'reg': rS, 'slot': d, 'patched': False})
        elif oh == 18:
            out.append({'clamp': o + MPC_CPU, 'hook': h + MPC_CPU,
                        'reg': None, 'slot': None, 'patched': True})
    return out


def find_bpscd(rom):
    """Boost sensor calibration: range pair (102,921), substitute 900."""
    if not rom.have_fls: return []
    f = bytes(rom.fls); pat = struct.pack('>HH', 102, 921); out = []; i = 0
    while True:
        i = f.find(pat, i)
        if i < 0: break
        cal = i - 8
        if cal >= 0 and cal + 0xA6 < len(f):
            sv, th = u16(f, cal + 0xA4), u16(f, cal + 0x84)
            db, md = u16(f, cal + 0x88), u16(f, cal + 0xA2)
            if 600 <= sv <= 1200 and 400 <= th <= 1200 and 50 <= db <= 2000:
                out.append({'cal': cal, 'subst': sv, 'thresh': th,
                            'deb': db, 'mode': md})
        i += 2
    return out


def find_r15(rom):
    """Derive r15, the calibration pointer-table base.

    r15 is loaded from RAM at runtime, so it is not a constant anywhere in the
    image. Instead score every candidate by how many `lwz rX,D(r15)` sites
    resolve to a word that is a plausible pointer into the calibration area.
    The real value wins by a wide margin. Verified against SW 1037390198,
    where it returns the documented 0x000501C8.
    """
    if not (rom.have_fls and rom.have_mpc): return None
    from collections import Counter
    fls, m = bytes(rom.fls), bytes(rom.mpc)
    cnt = Counter()
    for o in range(0, len(m) - 4, 4):
        op, d, ra, i = dec(u32(m, o))
        if op == 32 and ra == 15: cnt[sx(i)] += 1
    best = (0, None)
    for r15 in range(0x4F000, 0x54000, 4):
        sc = sum(n for dsp, n in cnt.items()
                 if 0 <= r15 + dsp < len(fls) - 4
                 and 0x0A0000 <= u32(fls, r15 + dsp) < 0x100000)
        if sc > best[0]: best = (sc, r15)
    return best[1]


def cell_profile(rom):
    """One pass over the image: for every r13-relative cell, its halfword
    writers and signed-halfword readers."""
    from collections import defaultdict
    wr, rd = defaultdict(list), defaultdict(int)
    for nm, buf, base in rom.segs():
        b = bytes(buf)
        for o in range(0, len(b) - 4, 4):
            x = u32(b, o); op = x >> 26
            if ((x >> 16) & 0x1F) != 13: continue
            ad = (R13 + sx(x & 0xFFFF)) & 0xFFFFFF
            if op == 44: wr[ad].append(o + base)
            elif op == 42: rd[ad] += 1
    return wr, rd


# the RAM band every confirmed pressure cell has fallen in
PRESSURE_BAND = (0x7F8B00, 0x7F8CFF)


def find_boost_cell(rom):
    """The filtered boost pressure cell, ranked best first.

    The sensor is published through a PT1 filter whose call site is
    `addi r5,r13,<state> ; li r6,10000 ; bl <filter>`. The published value is
    the halfword store to r13 nearest that call which also looks like a
    published signal: exactly one writer and several signed-halfword readers.
    Candidates inside the pressure RAM band rank first, then by reader count.

    This agrees with the independent BPSCD-calibration anchor on every image
    where that anchor resolves, and additionally resolves the families where
    it does not.
    """
    if not rom.have_mpc: return []
    m = bytes(rom.mpc)
    wr, rd = cell_profile(rom)
    pt1 = []
    for o in range(0, len(m) - 12, 4):
        o0, d0, a0, i0 = dec(u32(m, o))
        o1, d1, a1, i1 = dec(u32(m, o + 4))
        o2 = u32(m, o + 8) >> 26
        if o0 == 14 and a0 == 13 and o1 == 14 and a1 == 0 and i1 == 0x2710 and o2 == 18:
            pt1.append(o + MPC_CPU)
    out = []
    for site in pt1:
        best = None
        for k in range(-0x18, 0x1C, 4):
            p = site + k
            if not (MPC_CPU <= p < MPC_CPU + len(m)): continue
            op, rs, ra, i = dec(u32(m, p - MPC_CPU))
            if op != 44 or ra != 13: continue
            ad = (R13 + sx(i)) & 0xFFFFFF
            if len(wr[ad]) != 1 or rd[ad] < 4: continue
            band = PRESSURE_BAND[0] <= ad <= PRESSURE_BAND[1]
            sc = (0 if band else 1, abs(k))
            if best is None or sc < best[0]: best = (sc, ad, p)
        if best:
            ad = best[1]
            out.append({'cell': ad, 'site': best[2], 'pt1': site,
                        'readers': rd[ad],
                        'band': PRESSURE_BAND[0] <= ad <= PRESSURE_BAND[1],
                        'how': 'PT1 publish site'})
    seen, u = set(), []
    for x in sorted(out, key=lambda x: (not x['band'], -x['readers'])):
        if x['cell'] in seen: continue
        seen.add(x['cell']); u.append(x)
    return u


def can11h_support(rom):
    """Does this image actually carry the CAN11h producer?

    Two independent checks: the 0x316/0x329/0x545 descriptors must be present,
    and the CAN11h code paths must be there. Those paths are gated by
    `lbz rX,d(r13) ; cmpwi rX,0x12 ; beq`, and an image that supports CAN11h
    has many such sites. One family carries the descriptors but only a couple
    of gates, and forcing variant 3 on it would select a configuration whose
    producer does not exist.
    """
    ids, gates = set(), 0
    if rom.have_fls:
        f = bytes(rom.fls); i = 0
        while i < len(f) - 0x10:
            run = []; j = i
            while j < len(f) - 0x10:
                r = f[j:j + 0x10]; cid = (r[2] << 8) | r[3]
                if r[0] == 0 and r[1] == 0 and 1 <= cid <= 0x7FF and r[5] <= 2 and 1 <= r[8] <= 8:
                    run.append(cid); j += 0x10
                else: break
            if len(run) >= 6: ids |= set(run); i = j
            else: i += 4
    if rom.have_mpc:
        m = bytes(rom.mpc)
        for o in range(0, len(m) - 0x18, 4):
            o0, d0, a0, i0 = dec(u32(m, o))
            if o0 != 34 or a0 != 13: continue
            for k in (4, 8, 12, 16):
                w = u32(m, o + k); ok, dk, ak, ik = dec(w)
                if ok == 11 and ((w >> 23) & 3) == 0 and ik == 0x12 \
                        and ((w >> 16) & 0x1F) == d0:
                    gates += 1; break
    has_ids = {0x316, 0x329, 0x545} <= ids if rom.have_fls else None
    ok = (gates >= 4) and (has_ids is not False)
    return {'ids': has_ids, 'gates': gates, 'supported': ok,
            'ndesc': len(ids)}


def find_button_cell(rom):
    """The raw steering-wheel button byte.

    The MFL decoder validates the serial word with
        addi  rX, rY, -0x961
        cmplwi rX, 0x17F9
    and on success stores the raw code to RAM. The store after that check is
    the cell. Verified unique on every image tested.
    """
    if not rom.have_fls: return []
    f = bytes(rom.fls); out = []
    for o in range(0, len(f) - 8, 4):
        op, rd, ra, i = dec(u32(f, o))
        if op != 14 or sx(i) != -0x961: continue
        for k in range(4, 0x20, 4):
            o2, _, _, i2 = dec(u32(f, o + k))
            if o2 == 10 and i2 == 0x17F9:
                for j in range(k + 4, k + 0x30, 4):
                    o3, d3, a3, i3 = dec(u32(f, o + j))
                    if o3 == 38 and a3 == 13:
                        out.append({'cell': (R13 + sx(i3)) & 0xFFFFFFFF,
                                    'check': o, 'store': o + j})
                        break
                break
    return _dedupe(out)


def find_frmmng(rom):
    """FrmMng calibration base, via the DIV=1563 / MUL=10000 scaling pair."""
    if not rom.have_fls: return []
    f = bytes(rom.fls); pat = struct.pack('>HH', 1563, 10000); out = []; i = 0
    while True:
        i = f.find(pat, i)
        if i < 0: break
        out.append(i); i += 2
    return out


def find_free(rom, need=0xB0):
    """Runs of 0xFF in the MPC big enough for the stub."""
    if not rom.have_mpc: return []
    m = bytes(rom.mpc); runs = []; s = None
    for i in range(len(m)):
        if m[i] == 0xFF:
            if s is None: s = i
        else:
            if s is not None and i - s >= need: runs.append((s + MPC_CPU, i - s))
            s = None
    if s is not None and len(m) - s >= need: runs.append((s + MPC_CPU, len(m) - s))
    return sorted(runs, key=lambda r: -r[1])


def find_state_cells(rom, bases=None):
    """Pick two unreferenced RAM cells for the boost toggle state.

    0x7FFF80/0x7FFF82 are free in most images but not all, so they are a
    preference rather than an assumption. A window qualifies only if no
    r13-relative access anywhere in the image touches it, it sits above the
    application stack top, and it is inside the block the startup routine
    clears, so the gauge comes up in rpm mode after a battery disconnect.
    """
    # The application sets r13 = 0x7FFFF0, but the FLS boot code runs with
    # r13 = 0x7FFFE0. Resolve against both so a boot-time access cannot be
    # missed, and treat anything either base can reach as occupied.
    used = set()
    for nm, buf, base in rom.segs():
        b = bytes(buf)
        r13s = (R13, R13 - 0x10) if nm == 'FLS' else (R13,)
        for o in range(0, len(b) - 4, 4):
            w = u32(b, o)
            if (w >> 26) not in LDST: continue
            if ((w >> 16) & 0x1F) != 13: continue
            for r13 in r13s:
                a = (r13 + sx(w & 0xFFFF)) & 0xFFFFFFFF
                used.update((a, a + 1, a + 2, a + 3))
    stack_top = 0
    if bases and bases.get('vals'):
        stack_top = bases['vals'].get('r1', 0)
    out = []
    for a in range(0x7FFFFC, 0x7FF000, -4):
        if a <= stack_top: break
        if any(x in used for x in range(a, a + 4)): continue
        if a <= R13 < a + 4: continue
        out.append(a)
    # keep the historical pair first when it is still free
    pref = STATE_MODE & ~3
    out.sort(key=lambda a: (a != pref, -a))
    return out


def cell_refs(rom, lo, hi):
    """Every r13-relative load/store landing in [lo,hi]."""
    hits = []
    for nm, buf, base in rom.segs():
        b = bytes(buf)
        for o in range(0, len(b) - 4, 4):
            w = u32(b, o)
            if (w >> 26) not in LDST: continue
            if ((w >> 16) & 0x1F) != 13: continue
            a = (R13 + sx(w & 0xFFFF)) & 0xFFFFFFFF
            if lo <= a <= hi: hits.append((nm, o + base))
    return hits


def _dedupe(lst):
    seen, out = set(), []
    for x in lst:
        if x['cell'] in seen: continue
        seen.add(x['cell']); out.append(x)
    return out


# ======================================================================
#  stub generator - built per image, never copied
# ======================================================================
def gen_stub(rpm_reg, boost_cell, btn_cell, slot,
             mode_cell=STATE_MODE, ctr_cell=STATE_CTR, frame=0x20):
    d = lambda a: (a - R13) & 0xFFFF
    inner = slot + frame
    I = [
        0x94210000 | ((-frame) & 0xFFFF),      # stwu  r1,-frame(r1)
        0x91210008,                            # stw   r9,8(r1)
        0x9141000C,                            # stw   r10,0xc(r1)
        0x91810010,                            # stw   r12,0x10(r1)
        0x898D0000 | d(btn_cell),              # lbz   r12, btn(r13)
        0x718C0024,                            # andi. r12,r12,0x24
        0x2C0C0000,                            # cmpwi r12,0
        0x41820010,                            # beq   +0x10   (button held)
        0x39400000,                            # li    r10,0
        0xB14D0000 | d(ctr_cell),              # sth   r10, ctr(r13)
        0x48000028,                            # b     +0x28
        0xA14D0000 | d(ctr_cell),              # lhz   r10, ctr(r13)
        0x394A0001,                            # addi  r10,r10,1
        0x554A043E,                            # clrlwi r10,r10,16
        0xB14D0000 | d(ctr_cell),              # sth   r10, ctr(r13)
        0x2C0A01F4,                            # cmpwi r10,500
        0x40820010,                            # bne   +0x10
        0x892D0000 | d(mode_cell),             # lbz   r9, mode(r13)
        0x69290001,                            # xori  r9,r9,1
        0x992D0000 | d(mode_cell),             # stb   r9, mode(r13)
        0x898D0000 | d(mode_cell),             # lbz   r12, mode(r13)
        0x2C0C0000,                            # cmpwi r12,0
        0x41820038,                            # beq   +0x38   (passthrough)
        0xA98D0000 | d(boost_cell),            # lha   r12, boost(r13)
        0x2C0C0000,                            # cmpwi r12,0
        0x40800008,                            # bge   +8
        0x39800000,                            # li    r12,0
        0x2C0C2800,                            # cmpwi r12,0x2800
        0x40810008,                            # ble   +8
        0x39802800,                            # li    r12,0x2800
        0x39402710,                            # li    r10,10000
        0x7D8C51D6,                            # mullw r12,r12,r10
        0x3940061B,                            # li    r10,1563
        0x7D8C5396,                            # divwu r12,r12,r10
        0xB1810000 | inner,                    # sth   r12, inner(r1)
        0x48000008,                            # b     +8
        0xB0010000 | (rpm_reg << 21) | inner,  # sth   rN,  inner(r1)
        0x81210008,                            # lwz   r9,8(r1)
        0x8141000C,                            # lwz   r10,0xc(r1)
        0x81810010,                            # lwz   r12,0x10(r1)
        0x38210000 | frame,                    # addi  r1,r1,frame
        0x4E800020,                            # blr
    ]
    return b''.join(struct.pack('>I', w) for w in I)


# ======================================================================
#  analysis + actions
# ======================================================================
def analyse(rom):
    sw, hw = rom.ident()
    bases = find_bases(rom)
    a = {'sw': sw, 'hw': hw, 'md5': rom.md5s(),
         'bases': bases, 'variant': find_variant(rom),
         'hook': find_hook(rom), 'bpscd': find_bpscd(rom),
         'boost_cell': find_boost_cell(rom), 'button_cell': find_button_cell(rom),
         'frmmng': find_frmmng(rom), 'free': find_free(rom),
         'r15': find_r15(rom), 'can_support': can11h_support(rom),
         'csum': find_csum(rom), 'trailer': mpc_trailer_ok(rom),
         'state_refs': cell_refs(rom, STATE_MODE, STATE_CTR + 1),
         'state_free': find_state_cells(rom, bases)}
    return a


def can11h_ready(a):
    s = a.get('can_support') or {}
    if s and not s.get('supported'):
        return None, (f"this software has no CAN11h producer "
                      f"({s.get('gates', 0)} gate sites, {s.get('ndesc', 0)} descriptors); "
                      f"forcing variant 3 would select a configuration that cannot run")
    v = [x for x in a['variant'] if not x['forced']]
    if len(a['variant']) != 1:
        return None, f"variant dispatch: expected 1 site, found {len(a['variant'])}"
    if not v:
        return None, 'already forced to variant 3'
    return v[0], None


def boost_ready(a, cell=None, stub=None):
    hs = [x for x in a['hook'] if not x['patched']]
    if len(hs) != 1:
        return None, (f"boost hook: expected 1 unpatched site, found {len(hs)}"
                      + (' (already patched)' if a['hook'] else ''))
    h = hs[0]
    if cell is None:
        inband = [x for x in a['boost_cell'] if x['band']]
        if not inband:
            return None, ('boost pressure cell not identified; '
                          'choose one manually')
        cell = inband[0]['cell']
    btn = [x['cell'] for x in a['button_cell']]
    if len(set(btn)) != 1:
        return None, (f'button cell not unique ({len(set(btn))} candidates); '
                      'cannot build the stub')
    free = a.get('state_free') or []
    if not free:
        return None, 'no free RAM found for the toggle state cells'
    base_cell = free[0]
    mode_cell, ctr_cell = base_cell, base_cell + 2
    if stub is None:
        fr = [x for x in a['free'] if x[0] >= 0x404000]
        if not fr:
            return None, 'no free FF run >= 0xB0 bytes in the MPC'
        stub = fr[0][0]
    return {'hook': h, 'cell': cell, 'btn': btn[0], 'stub': stub,
            'mode': mode_cell, 'ctr': ctr_cell}, None


def apply_can11h(rom, v, changed):
    want = 0x38000003 | (v['reg'] << 21)
    struct.pack_into('>I', rom.mpc, rom.mo(v['lbz']), want)
    changed.setdefault('MPC', set()).update(range(rom.mo(v['lbz']), rom.mo(v['lbz']) + 4))
    return (f"CAN11h: MPC 0x{v['lbz']:06X}  lbz r{v['reg']},0(rA) -> li r{v['reg']},3  "
            f"(force variant 3)")


def apply_boost(rom, p, changed):
    h = p['hook']
    stub = gen_stub(h['reg'], p['cell'], p['btn'], h['slot'],
                    mode_cell=p.get('mode', STATE_MODE),
                    ctr_cell=p.get('ctr', STATE_CTR))
    o = rom.mo(p['stub'])
    if any(x != 0xFF for x in rom.mpc[o:o + len(stub)]):
        raise ValueError(f"0x{p['stub']:06X} is not free for {len(stub)} bytes")
    bl = 0x48000001 | ((p['stub'] - h['hook']) & 0x03FFFFFC)
    struct.pack_into('>I', rom.mpc, rom.mo(h['hook']), bl)
    rom.mpc[o:o + len(stub)] = stub
    ch = changed.setdefault('MPC', set())
    ch.update(range(rom.mo(h['hook']), rom.mo(h['hook']) + 4))
    ch.update(range(o, o + len(stub)))
    return (f"boost: hook MPC 0x{h['hook']:06X} (sth r{h['reg']},{h['slot']}(r1)) "
            f"-> bl 0x{p['stub']:06X}; {len(stub)} byte stub; "
            f"pressure 0x{p['cell']:06X}, button 0x{p['btn']:06X}, "
            f"state 0x{p.get('mode', STATE_MODE):06X}, rpm r{h['reg']}")


def set_byte(rom, chip, addr, val, changed):
    buf = rom.fls if chip == 'FLS' else rom.mpc
    base = 0 if chip == 'FLS' else MPC_CPU
    old = buf[addr - base]
    buf[addr - base] = val
    changed.setdefault(chip, set()).add(addr - base)
    return f'{chip} 0x{addr:06X}: {old:02X} -> {val:02X}'

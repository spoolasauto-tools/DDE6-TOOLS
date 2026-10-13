#!/usr/bin/env python3
"""
dde6patch.py - command line front end for the DDE6 analysis engine.

    dde6patch.py info      <image> [image ...]
    dde6patch.py patch     <image> [image ...] -o OUT [--can11h] [--boost]
                           [--boost-cell 0xNNNNNN] [--stub 0xNNNNNN]
                           [--apply FILE.patch] [--set 0xADDR=VV] [--dry-run]
    dde6patch.py verify    <image> [image ...]

An image is a 2.5 MB full read, a 2 MB FLS or a 512 KB MPC; pass an FLS and an
MPC together to work on both. Every address is found by scanning. Checksum
ranges are discovered from their descriptors and only the ranges containing a
changed byte are recomputed.

For the window version run dde6gui.py.
"""
import os
import sys
import argparse
import re
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dde6core as C


ENTRY = re.compile(r'^\s*0x([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s*->\s*([0-9A-Fa-f]+)')


def apply_patchfile(rom, path, changed):
    """Apply a patches/*.patch file. Verifies the stock bytes of every entry
    before writing anything, so a wrong software version is refused rather
    than half-applied."""
    chip, rows = None, []
    for line in open(path):
        if line.startswith('#'):
            if line.startswith('# chip:'):
                chip = line.split(':', 1)[1].strip().upper()
            continue
        m = ENTRY.match(line)
        if not m: continue
        rows.append((int(m.group(1), 16), bytes.fromhex(m.group(2)),
                     bytes.fromhex(m.group(3))))
    if chip not in ('FLS', 'MPC'):
        sys.exit(f"{path}: header needs '# chip: FLS' or '# chip: MPC'")
    buf = rom.fls if chip == 'FLS' else rom.mpc
    if buf is None:
        return f'{os.path.basename(path)}: needs the {chip}, not loaded - skipped'
    base = 0 if chip == 'FLS' else C.MPC_CPU
    bad, todo, already = [], [], 0
    for addr, old, new in rows:
        o = addr - base
        if o < 0 or o + len(old) > len(buf):
            bad.append((addr, 'outside this chip')); continue
        cur = bytes(buf[o:o + len(old)])
        if cur == new: already += 1
        elif cur != old: bad.append((addr, f'has {cur.hex().upper()}, expected {old.hex().upper()}'))
        else: todo.append((o, new))
    if bad:
        for a, why in bad: print(f'    0x{a:06X}: {why}', file=sys.stderr)
        sys.exit(f'{os.path.basename(path)}: refused, {len(bad)} entries did not match')
    for o, new in todo:
        buf[o:o + len(new)] = new
        changed.setdefault(chip, set()).update(range(o, o + len(new)))
    return (f'{os.path.basename(path)}: {len(todo)} applied'
            + (f', {already} already present' if already else ''))


def load(paths):
    rom = C.Rom()
    for p in paths:
        n = os.path.getsize(p)
        if n == C.FULL_SIZE: rom.load_full(p)
        elif n == C.FLS_SIZE: rom.load_fls(p)
        elif n in (C.MPC_SIZE, C.MPC_SIZE + 0x200): rom.load_mpc(p)
        else: sys.exit(f'{p}: unrecognised size 0x{n:X}')
    if not (rom.have_fls or rom.have_mpc): sys.exit('nothing loaded')
    return rom


def show(a, ranges):
    print(f"SW {a['sw'] or '?'}   HW {a['hw'] or '?'}")
    for k, v in a['md5'].items(): print(f'  {k} md5 {v}')

    b = a['bases']
    print('\nbase registers')
    if b and b['vals']:
        for k in ('r1', 'r2', 'r13', 'r14'):
            print(f"  {k:4s} 0x{b['vals'][k]:06X}" + ('   (app stack top)' if k == 'r1' else ''))
    else:
        print('  not found (needs both chips)')

    print('\nCAN11h variant dispatch')
    for v in a['variant']:
        print(f"  MPC 0x{v['lbz']:06X}  r{v['reg']}  mirror RAM 0x{v['mirror']:06X}  "
              f"[{'already forced' if v['forced'] else 'stock'}]")
    if not a['variant']: print('  not found')

    print('\nboost hook')
    for h in a['hook']:
        print(f"  clamp 0x{h['clamp']:06X}  hook 0x{h['hook']:06X}  " +
              ('already patched' if h['patched'] else f"sth r{h['reg']},{h['slot']}(r1)"))
    if not a['hook']: print('  not found')

    print('\nRAM cells')
    for x in a['boost_cell']:
        print(f"  pressure 0x{x['cell']:06X}  via MPC 0x{x['site']:06X}  [{x['how']}]")
    if not a['boost_cell']: print('  pressure: not identified')
    for x in a['button_cell']:
        print(f"  button   0x{x['cell']:06X}  stored FLS 0x{x['store']:06X}")
    if not a['button_cell']: print('  button: not identified')
    n = len(a['state_refs'])
    print(f"  state    0x{C.STATE_MODE:06X}/0x{C.STATE_CTR:06X}  "
          + ('free' if not n else f'{n} reference(s)'))

    if a['bpscd']:
        print('\nboost sensor calibration')
        for x in a['bpscd']:
            print(f"  FLS 0x{x['cal']:06X}  substitute {x['subst']} hPa, fault "
                  f"{x['thresh']}, debounce {x['deb']}, mode {x['mode']}")

    print('\nfree space in MPC')
    for o, m in a['free'][:5]: print(f'  0x{o:06X}  {m} bytes')

    print('\nchecksum ranges')
    for r in ranges:
        print(f"  {r['chip']} 0x{r['lo']:06X}-0x{r['hi']:06X}  word 0x{r['word']:06X}  "
              f"{'verified' if r['verified'] else 'DOES NOT SUM'}")
    if a['trailer'] is not None:
        print(f"  MPC trailer relation: {'OK' if a['trailer'] else 'BAD'}")


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('cmd', choices=['info', 'patch', 'verify'])
    ap.add_argument('images', nargs='+')
    ap.add_argument('-o', '--out')
    ap.add_argument('--can11h', action='store_true')
    ap.add_argument('--boost', action='store_true')
    ap.add_argument('--boost-cell')
    ap.add_argument('--stub')
    ap.add_argument('--set', action='append', default=[])
    ap.add_argument('--apply', action='append', default=[],
                    help='apply a patches/*.patch file')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('-h', '--help', action='store_true')
    o = ap.parse_args()
    if o.help:
        print(__doc__); return 0

    rom = load(o.images)
    a = C.analyse(rom)
    ranges = a['csum']

    if o.cmd == 'info':
        show(a, ranges); return 0

    if o.cmd == 'verify':
        ok, bad = C.verify_csum(rom)
        for s, _ in ok: print(f'  {s}  OK')
        for s, v in bad: print(f'  {s}  sums to {v:08X}, expected {C.MAGIC:08X}')
        t = C.mpc_trailer_ok(rom)
        if t is not None: print(f"  MPC trailer: {'OK' if t else 'BAD'}")
        return 1 if bad or t is False else 0

    if not (o.can11h or o.boost or o.set or o.apply):
        sys.exit('nothing to do: pass --can11h, --boost, --apply or --set')
    if not o.out and not o.dry_run:
        sys.exit('-o OUT is required unless --dry-run')

    print(f"SW {a['sw'] or '?'}  HW {a['hw'] or '?'}")
    changed = {}

    if o.can11h:
        v, err = C.can11h_ready(a)
        if err: sys.exit(f'CAN11h: {err}')
        print('  ' + C.apply_can11h(rom, v, changed))

    if o.boost:
        cell = int(o.boost_cell, 16) if o.boost_cell else None
        stub = int(o.stub, 16) if o.stub else None
        p, err = C.boost_ready(a, cell=cell, stub=stub)
        if err: sys.exit(f'boost: {err}')
        print('  ' + C.apply_boost(rom, p, changed))

    for pf in o.apply:
        print('  ' + apply_patchfile(rom, pf, changed))

    for s in o.set:
        ad, _, vl = s.partition('=')
        addr = int(ad, 16)
        chip = 'MPC' if addr >= C.MPC_CPU else 'FLS'
        print('  ' + C.set_byte(rom, chip, addr, int(vl, 16), changed))

    for line in C.fix_csum(rom, changed, ranges):
        print('  checksum ' + line)

    ok, bad = C.verify_csum(rom)
    t = C.mpc_trailer_ok(rom)
    print(f"  {len(ok)} ranges verified" + (f', {len(bad)} BAD' if bad else '')
          + (f", trailer {'OK' if t else 'BAD'}" if t is not None else ''))
    if bad or t is False:
        sys.exit('refusing to write: checksums inconsistent')

    if o.dry_run:
        print('\ndry run, nothing written'); return 0

    which = ('full' if (rom.have_fls and rom.have_mpc)
             else 'fls' if rom.have_fls else 'mpc')
    rom.save(o.out, which)
    print(f"\nwrote {o.out} ({which})  md5 {rom.md5s()}")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BrokenPipeError:
        try: sys.stdout.close()
        except Exception: pass
        os._exit(0)
    except KeyboardInterrupt:
        sys.exit(130)

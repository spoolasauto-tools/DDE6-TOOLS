#!/usr/bin/env python3
"""
DDE6 Patcher - graphical front end.

    python3 dde6gui.py

Load a full read (2.5 MB), or an FLS (2 MB) and/or MPC (512 KB) separately.
Press Analyse to scan the image; every address is found by signature, nothing
is tied to a software version. Tick what you want, Apply, then Save.

Needs dde6core.py beside it. Standard library only.
"""
import os
import sys
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dde6core as C

APP = 'DDE6 Patcher'


class Gui:
    def __init__(self, root):
        self.root = root
        self.rom = C.Rom()
        self.a = None
        self.ranges = None
        self.changed = {}
        self.dirty = False
        root.title(APP)
        root.geometry('1020x680')
        self._build()
        self._refresh()

    # ---------------------------------------------------------------- ui
    def _build(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill='both', expand=True)

        left = ttk.Frame(outer)
        left.pack(side='left', fill='y', padx=(0, 8))

        # --- load ---
        lf = ttk.LabelFrame(left, text='Image', padding=8)
        lf.pack(fill='x')
        ttk.Button(lf, text='Load full read (2.5 MB)',
                   command=self.load_full).pack(fill='x')
        ttk.Button(lf, text='Load FLS (2 MB)',
                   command=self.load_fls).pack(fill='x', pady=(4, 0))
        ttk.Button(lf, text='Load MPC (512 KB)',
                   command=self.load_mpc).pack(fill='x', pady=(4, 0))
        ttk.Button(lf, text='Clear', command=self.clear).pack(fill='x', pady=(8, 0))
        self.lbl_files = ttk.Label(lf, text='', justify='left', width=38)
        self.lbl_files.pack(fill='x', pady=(8, 0))

        # --- identity ---
        idf = ttk.LabelFrame(left, text='Identity', padding=8)
        idf.pack(fill='x', pady=(8, 0))
        self.lbl_id = ttk.Label(idf, text='nothing loaded', justify='left', width=38)
        self.lbl_id.pack(fill='x')

        # --- actions ---
        af = ttk.LabelFrame(left, text='Modifications', padding=8)
        af.pack(fill='x', pady=(8, 0))
        self.v_can = tk.BooleanVar()
        self.v_boost = tk.BooleanVar()
        self.cb_can = ttk.Checkbutton(
            af, text='CAN11h  (force variant 3)', variable=self.v_can)
        self.cb_can.pack(anchor='w')
        self.cb_boost = ttk.Checkbutton(
            af, text='Boost on tacho', variable=self.v_boost)
        self.cb_boost.pack(anchor='w', pady=(4, 0))
        self.lbl_why = ttk.Label(af, text='', justify='left', width=38,
                                 foreground='#a06000')
        self.lbl_why.pack(fill='x', pady=(4, 0))

        ttk.Label(af, text='pressure cell').pack(anchor='w', pady=(8, 0))
        self.cmb_cell = ttk.Combobox(af, width=34, state='disabled')
        self.cmb_cell.pack(fill='x')
        ttk.Label(af, text='stub address').pack(anchor='w', pady=(6, 0))
        self.cmb_stub = ttk.Combobox(af, width=34, state='disabled')
        self.cmb_stub.pack(fill='x')

        self.btn_apply = ttk.Button(af, text='Apply', command=self.apply)
        self.btn_apply.pack(fill='x', pady=(10, 0))
        self.btn_repair = ttk.Button(af, text='Repair broken checksums',
                                     command=self.repair)
        self.btn_repair.pack(fill='x', pady=(4, 0))

        # --- save ---
        sf = ttk.LabelFrame(left, text='Save', padding=8)
        sf.pack(fill='x', pady=(8, 0))
        self.btn_save_full = ttk.Button(sf, text='Save full read',
                                        command=lambda: self.save('full'))
        self.btn_save_full.pack(fill='x')
        self.btn_save_fls = ttk.Button(sf, text='Save FLS',
                                       command=lambda: self.save('fls'))
        self.btn_save_fls.pack(fill='x', pady=(4, 0))
        self.btn_save_mpc = ttk.Button(sf, text='Save MPC',
                                       command=lambda: self.save('mpc'))
        self.btn_save_mpc.pack(fill='x', pady=(4, 0))

        # --- report ---
        right = ttk.Frame(outer)
        right.pack(side='left', fill='both', expand=True)
        bar = ttk.Frame(right)
        bar.pack(fill='x')
        ttk.Button(bar, text='Analyse', command=self.analyse).pack(side='left')
        ttk.Button(bar, text='Verify checksums',
                   command=self.verify).pack(side='left', padx=(6, 0))
        ttk.Button(bar, text='Clear log',
                   command=lambda: self.txt.delete('1.0', 'end')).pack(side='right')
        self.txt = tk.Text(right, wrap='none', font=('TkFixedFont', 9),
                           bg='#101418', fg='#d8dee4', insertbackground='#d8dee4')
        ys = ttk.Scrollbar(right, orient='vertical', command=self.txt.yview)
        self.txt.configure(yscrollcommand=ys.set)
        ys.pack(side='right', fill='y', pady=(6, 0))
        self.txt.pack(fill='both', expand=True, pady=(6, 0))
        for tag, col in (('h', '#7fd1ff'), ('ok', '#9ae6a0'),
                         ('warn', '#ffd479'), ('bad', '#ff8f8f'),
                         ('dim', '#8a949e')):
            self.txt.tag_configure(tag, foreground=col)

        self.status = ttk.Label(self.root, text='', relief='sunken', anchor='w')
        self.status.pack(fill='x', side='bottom')

    # ------------------------------------------------------------- output
    def log(self, s='', tag=None):
        self.txt.insert('end', s + '\n', tag)
        self.txt.see('end')

    def hdr(self, s):
        self.log('')
        self.log(s, 'h')
        self.log('-' * len(s), 'dim')

    def _refresh(self):
        parts = []
        if self.rom.full_path:
            parts.append(f'full read: {os.path.basename(self.rom.full_path)}')
        if self.rom.fls_path:
            parts.append(f'FLS: {os.path.basename(self.rom.fls_path)}')
        if self.rom.mpc_path:
            parts.append(f'MPC: {os.path.basename(self.rom.mpc_path)}')
        if not parts:
            parts.append('nothing loaded')
        if self.dirty:
            parts.append('MODIFIED, not saved')
        self.lbl_files.configure(text='\n'.join(parts))

        sw, hw = self.rom.ident()
        lines = []
        if self.rom.have_fls or self.rom.have_mpc:
            lines.append(f'SW {sw or "?"}   HW {hw or "?"}')
            for k, v in self.rom.md5s().items():
                lines.append(f'{k} {v}')
        self.lbl_id.configure(text='\n'.join(lines) or 'nothing loaded')

        have = self.rom.have_fls or self.rom.have_mpc
        st = 'normal' if have else 'disabled'
        self.btn_save_full.configure(
            state='normal' if (self.rom.have_fls and self.rom.have_mpc) else 'disabled')
        self.btn_save_fls.configure(state='normal' if self.rom.have_fls else 'disabled')
        self.btn_save_mpc.configure(state='normal' if self.rom.have_mpc else 'disabled')
        self.btn_apply.configure(state='normal' if self.a else 'disabled')
        self.status.configure(
            text='  ' + (' | '.join(parts)))

    # -------------------------------------------------------------- load
    def _guard(self):
        if self.dirty and not messagebox.askyesno(
                APP, 'The loaded image has unsaved changes. Discard them?'):
            return False
        return True

    def _after_load(self):
        self.a = None; self.ranges = None; self.changed = {}; self.dirty = False
        self.v_can.set(False); self.v_boost.set(False)
        self.cmb_cell.configure(state='disabled', values=()); self.cmb_cell.set('')
        self.cmb_stub.configure(state='disabled', values=()); self.cmb_stub.set('')
        self._refresh()
        self.analyse()

    def load_full(self):
        if not self._guard(): return
        p = filedialog.askopenfilename(title='Load full read',
                                       filetypes=[('ECU images', '*.bin *.BIN'), ('All', '*')])
        if not p: return
        try:
            self.rom.load_full(p)
        except Exception as e:
            messagebox.showerror(APP, str(e)); return
        self.txt.delete('1.0', 'end')
        self.log(f'loaded full read {os.path.basename(p)}', 'ok')
        self._after_load()

    def load_fls(self):
        if not self._guard(): return
        p = filedialog.askopenfilename(title='Load FLS',
                                       filetypes=[('FLS', '*.FLS *.fls *.bin'), ('All', '*')])
        if not p: return
        try:
            self.rom.load_fls(p)
        except Exception as e:
            messagebox.showerror(APP, str(e)); return
        self.log(f'loaded FLS {os.path.basename(p)}', 'ok')
        self._after_load()

    def load_mpc(self):
        if not self._guard(): return
        p = filedialog.askopenfilename(title='Load MPC',
                                       filetypes=[('MPC', '*.MPC *.mpc *.bin'), ('All', '*')])
        if not p: return
        try:
            self.rom.load_mpc(p)
        except Exception as e:
            messagebox.showerror(APP, str(e)); return
        self.log(f'loaded MPC {os.path.basename(p)}', 'ok')
        self._after_load()

    def clear(self):
        if not self._guard(): return
        self.rom = C.Rom()
        self.txt.delete('1.0', 'end')
        self._after_load()

    # ----------------------------------------------------------- analyse
    def analyse(self):
        if not (self.rom.have_fls or self.rom.have_mpc):
            self._refresh(); return
        try:
            self.a = C.analyse(self.rom)
        except Exception:
            self.log(traceback.format_exc(), 'bad'); return
        if self.ranges is None:
            self.ranges = self.a['csum']          # captured before any edit
        a = self.a
        self.hdr('identity')
        self.log(f"  SW {a['sw'] or '?'}    HW {a['hw'] or '?'}")
        for k, v in a['md5'].items():
            self.log(f'  {k} md5 {v}', 'dim')

        b = a['bases']
        self.hdr('global base registers')
        if b and b['vals']:
            for k in ('r1', 'r2', 'r13', 'r14'):
                note = '   (application stack top)' if k == 'r1' else ''
                self.log(f"  {k:4s} 0x{b['vals'][k]:06X}{note}")
            self.log(f"  init routine MPC 0x{b['init']:06X}", 'dim')
        else:
            self.log('  not found (needs both chips loaded)', 'warn')

        cs = a.get('can_support') or {}
        self.hdr('CAN11h support')
        self.log(f"  message descriptors {cs.get('ndesc', 0)}, "
                 f"producer gate sites {cs.get('gates', 0)}, "
                 f"0x316/0x329/0x545 present: {cs.get('ids')}")
        self.log('  verdict: ' + ('this software can run CAN11h'
                                  if cs.get('supported') else
                                  'this software has NO CAN11h producer'),
                 'ok' if cs.get('supported') else 'bad')

        self.hdr('CAN11h variant dispatch')
        if a['variant']:
            for v in a['variant']:
                s = 'already forced to 3' if v['forced'] else 'stock'
                tag = 'warn' if v['forced'] else 'ok'
                self.log(f"  MPC 0x{v['lbz']:06X}  r{v['reg']}   "
                         f"variant mirror RAM 0x{v['mirror']:06X}   [{s}]", tag)
        else:
            self.log('  not found', 'warn')

        self.hdr('boost hook')
        for h in a['hook']:
            if h['patched']:
                self.log(f"  clamp 0x{h['clamp']:06X}  hook 0x{h['hook']:06X}  "
                         f"already patched", 'warn')
            else:
                self.log(f"  clamp 0x{h['clamp']:06X}  hook 0x{h['hook']:06X}  "
                         f"sth r{h['reg']},{h['slot']}(r1)", 'ok')
        if not a['hook']:
            self.log('  not found', 'warn')

        self.hdr('RAM cells')
        for x in a['boost_cell']:
            self.log(f"  pressure 0x{x['cell']:06X}   published MPC 0x{x['site']:06X}"
                     f"   [{x['how']}]", 'ok')
        if not a['boost_cell']:
            self.log('  pressure: not identified, choose one manually', 'warn')
        for x in a['button_cell']:
            self.log(f"  button   0x{x['cell']:06X}   stored FLS 0x{x['store']:06X}", 'ok')
        if not a['button_cell']:
            self.log('  button: not identified', 'warn')
        n = len(a['state_refs'])
        self.log(f"  state    0x{C.STATE_MODE:06X} / 0x{C.STATE_CTR:06X}   "
                 + ('free' if not n else f'{n} reference(s) - IN USE, will pick another'),
                 'ok' if not n else 'warn')
        fs = a.get('state_free') or []
        if fs:
            self.log('  free state cells: '
                     + ', '.join(f'0x{x:06X}' for x in fs[:5]), 'dim')
        if a.get('r15'):
            self.log(f"  r15 (cal pointer table) 0x{a['r15']:06X}", 'dim')

        if a['bpscd']:
            self.hdr('boost sensor calibration')
            for x in a['bpscd']:
                self.log(f"  FLS 0x{x['cal']:06X}   substitute {x['subst']} hPa, "
                         f"fault {x['thresh']}, debounce {x['deb']}, mode {x['mode']}")

        self.hdr('free space in MPC')
        for o, n in a['free'][:5]:
            self.log(f'  0x{o:06X}   {n} bytes')

        self.hdr('checksums')
        for r in self.ranges:
            tag = 'ok' if r['verified'] else 'bad'
            self.log(f"  {r['chip']} 0x{r['lo']:06X}-0x{r['hi']:06X}   "
                     f"word 0x{r['word']:06X}   "
                     f"{'verified' if r['verified'] else 'does NOT sum'}", tag)
        if a['trailer'] is not None:
            self.log(f"  MPC trailer relation: "
                     f"{'OK' if a['trailer'] else 'BAD'}",
                     'ok' if a['trailer'] else 'bad')

        self._fill_choices()
        self._gate_options()
        self._refresh()

    def _gate_options(self):
        """Grey out whatever this image cannot take, and say why."""
        a = self.a
        why = []
        v, ev = C.can11h_ready(a)
        if ev:
            self.v_can.set(False)
            self.cb_can.configure(state='disabled')
            why.append('CAN11h: ' + ev.split(';')[0])
        else:
            self.cb_can.configure(state='normal')
        p, ep = C.boost_ready(a)
        if ep:
            self.v_boost.set(False)
            self.cb_boost.configure(state='disabled')
            why.append('Boost: ' + ep.split(';')[0])
        else:
            self.cb_boost.configure(state='normal')
        self.lbl_why.configure(text='\n'.join(why))
        ok, bad = C.verify_csum(self.rom)
        self.btn_repair.configure(state='normal' if bad else 'disabled')

    def repair(self):
        ok, bad = C.verify_csum(self.rom)
        if not bad:
            messagebox.showinfo(APP, 'All checksum ranges already sum correctly.')
            return
        if not messagebox.askyesno(
                APP, f'{len(bad)} checksum range(s) do not sum.\n\n'
                     'This image arrived already modified by something that did '
                     'not fix them. Recompute all of them?'):
            return
        self.hdr('repairing checksums')
        for line in C.repair_csum(self.rom, self.ranges):
            self.log('  ' + line, 'ok')
        self.dirty = True
        ok, bad = C.verify_csum(self.rom)
        self.log(f'  {len(ok)} verified, {len(bad)} still bad',
                 'ok' if not bad else 'bad')
        self._gate_options()
        self._refresh()

    def _fill_choices(self):
        a = self.a
        cells = [f"0x{x['cell']:06X}   ({x['readers']} readers"
                 + (', in band)' if x['band'] else ', outside band)')
                 for x in a['boost_cell']]
        self.cmb_cell.configure(values=cells, state='readonly' if cells else 'normal')
        if cells:
            self.cmb_cell.current(0)
        else:
            self.cmb_cell.set('')
        stubs = [f'0x{o:06X}   ({n} bytes free)' for o, n in a['free'][:6]
                 if o >= 0x404000]
        self.cmb_stub.configure(values=stubs, state='readonly' if stubs else 'normal')
        if stubs:
            self.cmb_stub.current(0)

    def verify(self):
        if not (self.rom.have_fls or self.rom.have_mpc): return
        ok, bad = C.verify_csum(self.rom)
        self.hdr('checksum verification')
        for s, _ in ok:
            self.log(f'  {s}   sums to the magic', 'ok')
        for s, v in bad:
            self.log(f'  {s}   sums to {v:08X}, expected {C.MAGIC:08X}', 'bad')
        t = C.mpc_trailer_ok(self.rom)
        if t is not None:
            self.log(f"  MPC trailer relation: {'OK' if t else 'BAD'}",
                     'ok' if t else 'bad')
        if not bad and (t is None or t):
            self.log('  all good', 'ok')

    # ------------------------------------------------------------- apply
    @staticmethod
    def _hexfield(s):
        s = (s or '').strip().split()[0] if (s or '').strip() else ''
        return int(s, 16) if s else None

    def apply(self):
        if not self.a:
            return
        if not (self.v_can.get() or self.v_boost.get()):
            messagebox.showinfo(APP, 'Tick a modification first.'); return
        a = self.a
        plan = []

        if self.v_can.get():
            v, err = C.can11h_ready(a)
            if err:
                messagebox.showerror(APP, f'CAN11h: {err}'); return
            plan.append(('can', v))

        if self.v_boost.get():
            try:
                cell = self._hexfield(self.cmb_cell.get())
                stub = self._hexfield(self.cmb_stub.get())
            except ValueError:
                messagebox.showerror(APP, 'pressure cell / stub address must be hex'); return
            p, err = C.boost_ready(a, cell=cell, stub=stub)
            if err:
                messagebox.showerror(APP, f'boost: {err}'); return
            plan.append(('boost', p))

        if not messagebox.askyesno(
                APP, 'Apply to the loaded image in memory?\n\n'
                     'Nothing is written to disk until you press Save.'):
            return

        self.hdr('applying')
        try:
            for kind, obj in plan:
                if kind == 'can':
                    self.log('  ' + C.apply_can11h(self.rom, obj, self.changed), 'ok')
                else:
                    self.log('  ' + C.apply_boost(self.rom, obj, self.changed), 'ok')
            for line in C.fix_csum(self.rom, self.changed, self.ranges):
                self.log('  checksum ' + line, 'ok')
        except Exception as e:
            self.log(f'  FAILED: {e}', 'bad')
            messagebox.showerror(APP, str(e))
            return

        self.dirty = True
        ok, bad = C.verify_csum(self.rom)
        t = C.mpc_trailer_ok(self.rom)
        if bad or (t is False):
            self.log('  checksums are NOT consistent after patching', 'bad')
        else:
            self.log(f'  {len(ok)} checksum ranges verified'
                     + (', trailer OK' if t else ''), 'ok')
        self.a = None            # force re-analysis before another apply
        self.cb_can.configure(state='disabled')
        self.cb_boost.configure(state='disabled')
        self.lbl_why.configure(text='applied - save, then reload to do more')
        self._refresh()
        self.log('')
        self.log('  done. Save the image, then re-load it to patch anything else.', 'dim')

    # -------------------------------------------------------------- save
    def save(self, which):
        names = {'full': ('full read', '.bin'), 'fls': ('FLS', '.FLS'),
                 'mpc': ('MPC', '.MPC')}
        label, ext = names[which]
        p = filedialog.asksaveasfilename(
            title=f'Save {label}', defaultextension=ext,
            filetypes=[(label, '*' + ext), ('All', '*')])
        if not p: return
        try:
            self.rom.save(p, which)
        except Exception as e:
            messagebox.showerror(APP, str(e)); return
        self.log(f'saved {label} to {p}', 'ok')
        if which == 'full' or (which == 'fls' and not self.rom.have_mpc) \
                or (which == 'mpc' and not self.rom.have_fls):
            self.dirty = False
        self._refresh()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use('clam')
    except tk.TclError:
        pass
    Gui(root)
    root.mainloop()


if __name__ == '__main__':
    main()

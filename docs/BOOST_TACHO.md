# Boost on the tacho

An E39 has no boost gauge. The rev counter is driven by a field in CAN 0x316, so that field can carry manifold pressure instead, with a steering-wheel button switching between the two.

## The scaling is free

The cluster divides the number in that field by 6.4 to get rpm. The stock firmware scales engine speed by 10000/1563, which is 6.3979.

Push boost in hPa through that same scaling and the cluster's divide cancels it, so the needle reads absolute pressure 1:1. 1000 hPa puts the needle on 1000 rpm, matching INPA. No new tables and no lookup; the arithmetic was already there.

```
clamp to [0, 0x2800], then * 10000, then / 1563     integers, in that order
0x2800 = 10240 hPa, an overflow guard only
```

## Edit 1: the hook

One instruction in the formatter is replaced with a call.

|Address|Stock|New|Meaning|
|---|---|---|---|
|MPC 0x4344AC|b3810008|4804b155|`sth r28,8(r1)` becomes `bl 0x47F600`|

The stub lives at MPC 0x47F600 in the FF padding at the end of the MPC and performs the store itself, so nothing is lost.

Two things to watch:

- The stub does its own `stwu r1,-0x20(r1)`, so the caller's `8(r1)` is `0x28(r1)` seen from inside the stub. Storing to the wrong one quietly corrupts the formatter's locals.
- r28 still holds the stock rpm on entry, so the passthrough path is just `sth r28,0x28(r1)`.

Full stub with every instruction and its encoding: [../src/boost_stub.asm](../src/boost_stub.asm). Builder: [../src/build_boost.py](../src/build_boost.py).

## Edit 2: the checksum

MPC 0x47F600 and the trailer word at MPC 0x47FFF8 are both inside range 0x404000-0x47FFFB, so one recalculation covers the hook, the stub and the trailer. See [REFERENCE.md](REFERENCE.md).

## Which pressure value to read

Three exist and only one is correct.

|Address|Value|Use|
|---|---|---|
|RAM 0x7F8B14|raw, written only when the sensor range check passes|no|
|RAM 0x7F8B10|intermediate|no|
|RAM 0x7F8B0C|filtered, one writer at MPC 0x41A36C|yes|

0x7F8B14 only updates while the reading is valid, so on a dead sensor it holds the last good number forever. A gauge that lies is worse than one reading zero.

## The toggle

Hold the wheel on/off button for about 5 seconds to swap between rpm and boost, and again to go back.

|Item|Value|
|---|---|
|Trigger|exactly 500 passes of the 10ms task|
|Counter reset|on button release|
|Mode byte|RAM 0x7FFF80|
|Hold counter|RAM 0x7FFF82|
|Button byte read|RAM 0x7FB015 (raw)|

Firing on an exact count rather than a threshold means a long hold cannot toggle twice.

Known defect, not fixed: holding the button for roughly 11 minutes continuously wraps the 16-bit counter and fires again.

The two state addresses are not arbitrary. The application stack top is 0x7FE99C, not the 0x800000 found in the boot code, and the 2 KB below it is stack that looks completely free to any scanner because it is addressed through r1 rather than r13. State placed there survives for about a second and then goes to zero. The full reasoning for 0x7FFF80 and 0x7FFF82 is in [REFERENCE.md](REFERENCE.md).

The button byte must be 0x7FB015, the raw one, and not 0x7F8849. See [MFL.md](MFL.md) for why.

## Why the needle parks at 900 with a dead sensor

- Module: BPSCD
- Input: QADC-B channel 9 at 0x704C00
- Handler table: FLS 0x040364
- Calibration block: FLS 0x0C9B28

|Offset|Value|Meaning|
|---|---|---|
|+0x08|102|range check low, ~0.5V|
|+0x0A|921|range check high, ~4.5V|
|+0x84|800|low pressure fault threshold|
|+0x88|300|debounce|
|+0xA2|5|substitute mode|
|+0xA4|900|substitute value|

Mode 5 has bit 2 set, which selects the ramp branch at MPC 0x41A274. That ramps RAM 0x7F8B12 towards 900, MPC 0x41A298 feeds it into r27, and MPC 0x41A36C writes it to 0x7F8B0C. The ECU substitutes 900 hPa and the gauge sits there looking plausible.

Check +0xA2 in your own file before assuming; a different software version may select a different branch.

## Worked example

Needle reading 1850 on the rev counter, in boost mode:

```
field value sent = 1850 * 10000 / 1563 = 11836
cluster displays  = 11836 / 6.4 = 1849 "rpm"
actual pressure   = 1850 hPa absolute = ~850 mbar of boost over an atmosphere
```

## Porting to another software version

The addresses here are for SW 1037390198. They do not transfer; every software
version moves the code. The patch files refuse rather than apply to the wrong
image, and `dde6patch.py` finds the addresses for any version instead.

What does transfer is the shape of the rpm path, which is identical in every
DDE6 image examined so far:

```
cmpwi  rN, 0            |
bge    +8               |
li     rM, 0            |  lower clamp
b      +0x18            |
cmpwi  rN, 0x2800       <- the 10240 hPa clamp, the anchor
ble    +0xC             |
li     rM, 0x2800       |  upper clamp
b      +8               |
clrlwi rM, rN, 0x10     |
lhz    r12, D(r27)      <- MUL
lhz    r4,  D-2(r27)    <- DIV
mullw  r3, rM, r12
bl     <divide helper>
addi   rN, r3, 0
sth    rN, 8(r1)        <- THE HOOK, always anchor + 0x28
```

`tools/dde6re.py <file> findhook` searches for the clamp, confirms the store at
+0x28, reports which register carries rpm, and lists free space for the stub.

Measured across five images:

|File|SW|Gen|Clamp|Hook|Reg|MUL/DIV off r27|0x47F600|
|---|---|---|---|---|---|---|---|
|CU78124 (mine)|1037390198|DDE606|0x434484|0x4344AC|r28|+0x5C / +0x5A|free|
|CU02765|1037387341|DDE606|0x42AA50|0x42AA78|r29|+0x6C / +0x6A|occupied|
|KV70422|1037381343|DDE626|0x42E8C4|0x42E8EC|r29|+0x68 / +0x66|free|
|LX80448|1037381791|DDE606|0x4320C8|0x4320F0|r29|+0x68 / +0x66|free|
|VF14265|1037500774|DDE606|0x42A920|0x42A948|r29|+0x6C / +0x6A|occupied|

Two things change the stub, not just the hook address:

- **The register.** Mine carries rpm in r28; all four others use r29. The stub's
  passthrough path is `sth rN, 0x28(r1)` and must name the right register, or
  the needle reads whatever happened to be in r28.
- **The stub address.** 0x47F600 is occupied by real code in two of the five.
  0x402E64 holds 4504 free bytes in all five and is the better universal home,
  though it sits in checksum range 0x400000-0x403FFF rather than the one that
  carries the trailer word.

The MUL/DIV offsets are reported for completeness. The stub does not use them;
it carries 10000 and 1563 as literals.

## Verified global values

Confirmed by reading the init routine in each image, found via the base-register
stubs described in [REFERENCE.md](REFERENCE.md):

|File|r1 (app stack top)|r2|r13|r14|
|---|---|---|---|---|
|CU78124 (mine)|0x7FE99C|0x000480F0|0x7FFFF0|0x04662C|
|CU02765|0x7FE934|0x000480F0|0x7FFFF0|0x04652C|
|KV70422 (DDE626)|0x7FE93C|0x000480F0|0x7FFFF0|0x04653C|
|LX80448|0x7FE98C|0x000480F0|0x7FFFF0|0x046614|
|VF14265|0x7FE92C|0x000480F0|0x7FFFF0|0x04652C|

r2 and r13 are identical in all five. r14 and the application stack top move.
Every stack top lands between 0x7FE92C and 0x7FE99C, so the state cells at
0x7FFF80 and 0x7FFF82 sit above all of them and were confirmed unreferenced in
all four unmodified images.

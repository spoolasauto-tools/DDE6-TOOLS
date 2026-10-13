# Reference

Memory map, registers, RAM, checksums and DAMOS method. Shared by all three modifications.

## Table of Contents

- [Global base registers](#global-base-registers)
- [RAM](#ram)
- [Checksums](#checksums)
- [DAMOS](#damos)
- [Interrupt vectors](#interrupt-vectors)
- [Turbo actuators](#turbo-actuators)
- [Multimap](#multimap)
- [Peripherals](#peripherals)

## Global base registers

This is the single thing that decides whether the firmware disassembles into code or into noise. The compiler keeps four registers pointed at fixed addresses and addresses everything as an offset from them, so without these values every data access is meaningless.

|Register|Value|
|---|---|
|r2|0x000480F0|
|r13|0x7FFFF0|
|r14|0x0004662C|
|r15|0x000501C8|

To find them on a different software version, look for four two-instruction stubs in the FLS of the form `mr rX,r3 ; blr`:

|Bytes|Returns|Address in this file|
|---|---|---|
|`7C 61 1B 78` + `4E 80 00 20`|r1|FLS 0x01DCF4|
|`7C 62 1B 78` + `4E 80 00 20`|r2|FLS 0x01DCFC|
|`7C 6D 1B 78` + `4E 80 00 20`|r13|FLS 0x01DD04|
|`7C 6E 1B 78` + `4E 80 00 20`|r14|FLS 0x01DD0C|

fn_4509BC calls all four back to back during startup.

r15 is never set from a constant. It is loaded from RAM by fn_06EB30:

```
if ([0x7FDBB1] == 1 && [0x7FDBB0] == 4)   r15 = [0x7FDBB8]    multimap path
else                                      r15 = [0x7FDBB4]    normal
[0x7FDBBC] = r15 - 0x000501C8
```

1065 instructions load something via r15.

A second base set exists, selected by RAM 0x7F804D at FLS 0x01CBB0, giving r2=0x0F80F0 and r14=0x0F461C. 0x0F461C holds real data. Status: TBD, when it switches was never established.

## RAM

Startup routine fn_4509BC zeroes:

```
0x7F8000-0x7FE9A8
0x7FF000-0x800000
0x6F8000-0x6F8800
0x800000-0x802400
```

and copies:

```
0x7FA368 <- FLS 0x050840   (0x758 bytes)
0x7F8774 <- FLS 0x027350   (0x30 bytes)
```

0x7FE9A8-0x7FF000 appears in neither list. Those 1624 bytes survive a reset and hold the keep-alive data.

### Heap

Bump allocator fn_470DF0. Descriptors at FLS 0x04F820 as `{start, end, init_fn}`, stride 0xC, count at FLS 0x04F844 = 3, chunk size at FLS 0x04F845 = 64.

|Region|Range|Size|Note|
|---|---|---|---|
|0|0x7FDD38-0x7FE190|1112|-|
|1|0x8C0000-0x8FE000|253952|ETK window, not fitted on a car|
|2|0x801F70-0x802400|1168|-|

Current pointers at RAM 0x7FF998, 0x7FF99C and 0x7FF9A0.

Anything placed in region 1 on a car without an ETK never keeps its value and never reports an error.

### The two stacks

|Stack|r1|Set by|
|---|---|---|
|Boot|0x800000|FLS startup code|
|Application|0x7FE99C|fn_4509BC, `r3_in - 0x1664`|

The application moves the stack after boot. Reading the startup code, finding 0x800000 and stopping there gives the wrong answer.

Heap region 0 ends at 0x7FE190, so:

```
0x7FE1A8 - 0x7FE99C IS STACK
```

This is invisible to scanning. The stack is addressed relative to r1, never r13, so a tool that resolves r13 and lists every address anything touches reports that entire 2 KB as untouched free RAM. It looks like the ideal place for variables and is the worst place in the chip.

### Why 0x7FFF80 and 0x7FFF82 are safe

The two cells the boost modification uses, each check performed against this image:

|Check|Result|
|---|---|
|Reads or writes anywhere in either chip|none|
|Address constants landing in range|none|
|Highest global below 0x800000|0x7FFF1D|
|Highest reach of any string or block store|0x7FFF03 (every `stswi` has NB=2, so 2 bytes, not 32)|
|Inside any heap region|no|
|Relative to app stack top|above it, and the stack grows away|
|FLS boot code|uses r13=0x7FFFE0, all 453 accesses land 0x7FD7F4-0x806022, none in range|
|Startup clear|zeroed by `memclr(0x7FF000,0x800000)`, so the gauge comes up in rpm mode after a battery disconnect|

Two values in range look like hits and are not: 0x7FFFF0 is r13 itself, and 0x7FFFFF at MPC 0x468780 is `lis r5,0x7f ; ori r5,r5,0xffff` feeding a compare, so it is a limit, not an address.

### Named RAM

|Address|Contents|
|---|---|
|0x7F9506|engine speed|
|0x7F8B0C|boost pressure, filtered. Use this|
|0x7F8B14|boost pressure, raw. Do not|
|0x7FB015|raw button code, 0xFE idle|
|0x7F8849|latched button byte, one shot|
|0x7FB00E|cruise latch|
|0x7F881A|system state, ~190 readers|
|0x7F87A8|free running counter|
|0x7FAD18|pointer to CAN slot descriptor base|
|0x7F85AE|CAN mailbox ownership table|
|0x7F92DC|CAN transmit gate bits|
|0x7FBD04|formatter pass counter|
|0x7FDBB0/B4/B8/BC|ETK state, base pointers, cached delta|

## Checksums

Nothing boots without these. Every protected range, summed as 32-bit big-endian words, comes to **0xD01FE500**.

Descriptors look like `[id][flags][lo][hi][FA DE CA FE CA FE AF FE]`, with the range before the signature. Rather than trusting a parse of that format, sum the range and check for the magic.

|Range|Contains|
|---|---|
|0x000000-0x007FBF|-|
|0x010000-0x01024F|-|
|0x0103D0-0x03FEFF|-|
|0x040000-0x0BFFFF|pointer tables, the runnable edits|
|0x0C3A10-0x0C3CA3|-|
|0x0C03D0-0x0FDFFB|frame switches, CrCCD_swtDevVal_C|
|0x400000-0x403FFF|-|
|0x404000-0x47FFFB|boost hook, stub and trailer|
|0x410100-0x413F4F|-|

MPC trailer:

```
sum32(0x400000..0x47FFF7) + word@0x47FFF8 = 0xA03FCA00     then 5A 5A 5A 5A
```

FLS correction words sit at 0x0BFFFC and 0x0FDFF8. Nothing covers 0x0FE000 upward, so that is free space with no checksum work.

Two descriptors never sum: 0x410100..0x413F50 and 0x0C3A10..0x0C3CA4. They are off-by-one duplicates of real ranges and fail identically in a completely stock file.

0x0BFF78 and 0x47FF70, immediately after the signature at 0x40403C, are a pointer pair (0xF30B36E4 stock), not a range. They happen to cover the hook and the stub but not the trailer.

`tools/dde6re.py <file> csum` checks all of them; `fixtrailer` recalculates the MPC word.

## DAMOS

Nobody has a DAMOS with a software number matching their file. The method is to load a close one and shift it by an offset until the blocks land on real data.

Two exist for EDC16C35, both E60 535d: 369821 and 372066. Pick by how much of the calibration area 0x0C0000-0x0FE000 is byte-identical to the target:

|DAMOS|Identical|Verdict|
|---|---|---|
|372066|96.1%|use this|
|369821|24.5%|no|
|500776 (DDE626)|14.1%|different family|
|390902 (DDE626)|14.1%|different family|

### The offset

372066 on SW 1037390198 shifts by **+0x8000**. DAMOS address + 0x8000 = address in the file.

To confirm a shift is right rather than merely close, take an object the DAMOS declares with an 8-point axis, go to the shifted address, and check there really are 8 rising 16-bit values. Real axes always count up. Across a few hundred objects:

|Offset|Axes that verify|
|---|---|
|0|15.2%|
|+0x8000|80.2%|
|-0x8000|10.0%|
|±0x4000, ±0x2000, ±0x10000|10-19%|

Wrong offsets sit at noise level, so there is no ambiguity. At +0x8000 the 372066 DAMOS names 3501 objects in this file, 2539 of them holding real data rather than padding.

### Confirming a DAMOS belongs to a file

A WinOLS `.ols` project embeds the original binary, so it can be extracted and compared directly instead of guessing from the software number.

Locate it by the spacing between EDC16 checksum signatures `FA DE CA FE CA FE AF FE`. In this file the gaps run `0x28 0xbd38 0x78 0x2fe48 0x28 0x28 0x7ffb0 0x28`, and the identical run appears inside the 372066 project at 0x7ABBE0.

|Project|Binary starts at .ols offset|
|---|---|
|372066|0x79BA64|
|369821|0x6BE7CF|

Confirmed by the software number string landing at 0x10150, 0x1049A, 0x40010 and 0xC0010 in both, at the same offsets.

### Export defects

- The unit column comes out shifted down by one row, so every object shows the unit belonging to the next one in the list.
- Packed `_CUR` curves get the wrong element width, producing values like 380114600. Read them as 16-bit.
- When parsing the `.ols` directly, real dimensions are tagged by the byte sequence `01 00 00` immediately before two little-endian 32-bit values. Taking the first plausible dimension pair after the name instead gives 256x256 for half the file, because that reads the FF/00 padding.

### The RAM half does not match

At any offset. The DAMOS calls RAM 0x7F8B0C `CoEng_trqInrLim`; it is boost pressure. Maps shift by a constant between software versions, RAM gets relocated. Trust it for maps and work RAM out separately.

### With no DAMOS at all

Bosch places a header immediately in front of the data:

```
[dy][dx][y axis][x axis][DATA]

start = data - 4 - 2*(Y+X), with both axes rising
```

Self-validating, and finds 406 maps in this FLS using nothing but the file. Also useful as a cross-check on a shifted DAMOS.

Worked example, FLS 0x0C3E68, pedal to torque:

```
14 08 | 500 750 1000 1250 1500 1750 2000 2250 2500 3000 3500 4000 4500 5000
      | 0 819 1065 1638 2458 4096 5734 8192

0x14 = 20 rpm breakpoints, 0x08 = 8 pedal breakpoints
```

## Interrupt vectors

Table at 0x000100, 8 bytes per entry, a `ba` followed by FFFFFFFF padding, 13 entries.

|Vector|Target|
|---|---|
|reset|0x000180|
|machine check|0x400130|
|DSI|0x402E20|
|ISI|0x002360|
|external|0x00023C|
|alignment|0x4004F4|
|program|0x4007C4|
|fp unavailable|0x4008B8|
|decrementer|0x4008E4|
|syscall|0x004D38|
|trace|0x000534|

When decoding `ba` by hand, AA is bit 30 and LK is bit 31 in the numbering where bit 0 is most significant. Swapped, they produce garbage targets that look almost plausible.

## Turbo actuators

Easy to confuse on a biturbo. Enumerate by `numPwmOut_C`, `numDioOut_C` and `numExtPin_C`.

|Actuator|Pin|Module|Output|Freq|Duty|Config|
|---|---|---|---|---|---|---|
|Wastegate (main N75)|17|LPACD|PWM ch11|33 Hz|5-95%|FLS 0x0EA688|
|Compressor bypass|20|BPACD|PWM ext pin3|33 Hz|5-95%|FLS 0x0C99EC|
|Turbine control valve|93|CBACD|digital out 9|-|-|FLS 0x0C9CB8|

Three with turbo-sounding names that are not turbo control:

|Module|Actually|
|---|---|
|TVACD|intake throttle valve, PWM ch9, feeds AirCtl_rTVABas_MAP and ThrVlv_rTVA|
|VSACD|swirl flaps, ch4|
|EGRCD|EGR, ch1|

BPACD is the only one with a linearisation curve: `BPACD_rLin_CUR` at FLS 0x0C9AD6, 25 points.

Boost control lives under PCR, not AirCtl; every `AirCtl_rTVACtl` map in this file is zeros. There is a high-pressure stage and a low-pressure stage, and the low-pressure stage drives LPACD.

|Map|Address|
|---|---|
|PCR_pBDesLP_MAP|FLS 0x0EE298|
|PCR_rBPBasLP_MAP|FLS 0x0EEA68|
|PCR_rBPRglBasLP_MAP|FLS 0x0EF134|
|PCR_pBDesMaxAPLP_MAP|FLS 0x0EE4C4 (flat 3190 hPa)|

Changeover curves, all on rpm axis 1000 1500 3200 3350 3550 3750 4000 4500:

|Curve|Address|Values|
|---|---|---|
|PCR_qLPGvnrOn_CUR|FLS 0x0EF476|4500 x4 then 1000 x4|
|PCR_qLPGvnrOff_CUR|FLS 0x0EF432|4100 x4 then 1000 x4|
|PCR_qLPGvnrOnRgn_CUR|FLS 0x0EF454|6000 x4 then 3500 3000 2500 1200|
|PCR_qLPGvnrOffRgn_CUR|FLS 0x0EF410|5800 x4 then 3300 2800 2300 1000|

PID gains: facP at FLS 0x0ED54E, facI at FLS 0x0ED4DE, facD at FLS 0x0ED46E.

## Multimap

Status: TBD, not finished. The machinery is all present.

Normally the map table lives in flash and r15 points at it. Multimap copies the table into RAM, points r15 at the copy, and maps can then be swapped live.

|Function|Role|
|---|---|
|fn_06EB30|picks which pointer r15 receives|
|fn_470718|allocates 0x674 bytes, pointer into RAM 0x7FDBB8, sets state 2 (3 if allocation fails)|
|fn_470784|memcpy's the normal table in, 64 bytes per call, sets RAM 0x7FDBB0 = 4 when finished|
|fn_4708E0|pulls individual calibration blocks in on demand|
|0x470A00|the state machine; its only caller is behind a diagnostic service|

State 4 means r15 points at a writable table in RAM. The flash table at FLS 0x04FB54 is every normal entry plus 0x01000000, the ETK form.

The hazard: fn_470718 writes RAM 0x7FDBB8 and fn_470784 copies into whatever it points at. Pointing 0x7FDBB8 at flash and then letting that diagnostic service fire makes the ECU attempt a memcpy into flash. The service must be provably unable to run before going near this.

## Peripherals

|Range|Contents|
|---|---|
|0x6F8000-0x6F8800|TPU RAM|
|0x704C00|QADC-B|
|0x707080 / 0x707480 / 0x707880|TouCAN controllers A / B / C|
|0x7F8000-0x802400|RAM|
|0x8C0000-0x8FE000|ETK window, not fitted on a car, writes go nowhere|
|0x8FE000-0x900000|measurement mirror|

## Debugging without a debugger

There is no logging and no debugger on a running car. The boost hook already hijacks the rpm field of 0x316, so it can be pointed at any RAM address, scaled to fit the dial, and read off the needle.

The 0x316 tacho field is engine speed times 6.4. Forgetting that divide makes every probed value read 6.4x out.

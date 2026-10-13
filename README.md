# dde6-e39

> Run a BMW E60 535d DDE6 engine ECU in an E39: CAN11h cluster and DSC, boost on the tacho, working cruise buttons.

The E60's Bosch EDC16C35 (DDE6.0.6) is a good ECU to swap into an older chassis, but dropped into an E39 it drives nothing. It transmits the E60 message set, so the E39 cluster shows no rpm and no temperature and the DSC sees no engine. The steering-wheel cruise buttons arrive on a pin the E60 firmware is not listening to.

This repo documents three firmware modifications that fix that, with every address and byte value, plus the memory map and tooling needed to find them again in a different software version. All of it was found by disassembling the firmware and all of it is confirmed running in the car.

A fourth change is included because an E39 has no boost gauge: the rev counter needle can be switched to show manifold pressure.

## Table of Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
- [Patches](#patches)
- [Modifications](#modifications)
- [Reference](#reference)
- [Abbreviations](#abbreviations)
- [Contributing](#contributing)
- [License](#license)

## Background

|Item|Value|
|---|---|
|ECU|Bosch EDC16C35, BMW DDE6.0.6|
|CPU|Freescale MPC563, PowerPC, big-endian|
|SW|1037390198|
|HW|0281013852|
|Donor|E60 535d, M57N, 200kW (272hp)|
|Target|E39|
|CAN|3x Motorola TouCAN, 500 kbps, one PT-CAN transceiver|

A full read is 2.5 MB because there are two flash chips:

|Range in file|Size|Chip|CPU address|
|---|---|---|---|
|0x000000-0x1FFFFF|2 MB|FLS, calibration and some code|0x000000|
|0x200000-0x27FFFF|512 KB|MPC, the program|0x400000|

The MPC does not run at the address it sits at in the file. A jump to 0x4340B8 is byte 0x2340B8 of the full read. Everything is big-endian.

Tune-related calibration (boost, fuel, timing) is out of scope and may differ from stock in any given file.

## Install

You need:

- a full read of both chips, and a way to write them back
- a bench setup that can recover the ECU if it stops talking
- Python 3 and `capstone` for the tooling: `pip install capstone`

Split a full read into the two chips:

```sh
dd if=fullread.bin of=e39.FLS bs=1M count=2
dd if=fullread.bin of=e39.MPC bs=1M skip=2
```

A `.MPC` saved by a tuning tool is 0x80200 bytes, with 0x200 of FF padding on the front. A full read has none. Add it back if your flasher expects it.

> **Warning**
> You will brick something. Keep that full read somewhere safe before you touch anything. If you brick it, that is on you. No EWS/immobiliser work is in this repo.

## Usage

### Any software version

```sh
python3 tools/dde6gui.py                       # window version
python3 tools/dde6patch.py info     fullread.bin
python3 tools/dde6patch.py patch    fullread.bin -o out.bin --can11h --boost
python3 tools/dde6patch.py verify   out.bin
```

Load a full read, or an FLS and an MPC together, or either on its own. Nothing
is tied to a software version: every address is found by scanning the image,
including the checksum ranges.

|Found|How|
|---|---|
|Base registers|the four `mr rX,r3 ; blr` stubs, then the `lis`/`addi` pairs in their caller|
|CAN11h dispatch|`lbz rD,0(rA) ; cmpwi rD,3 ; stb rD,d(r13)`|
|Boost hook, rpm register|`cmpwi rN,0x2800` clamp; the store is always anchor + 0x28|
|Sensor calibration|range pair (102, 921) with substitute 900, fault 800, debounce 300|
|Pressure cell|the `lha rX,0xA4(rCal)` substitute read, then the following `sth` pair|
|Button cell|`addi rX,rY,-0x961 ; cmplwi rX,0x17F9`, then the store after it|
|FrmMng calibration|the DIV=1563 / MUL=10000 scaling pair|
|Stub space|longest run of 0xFF in the MPC|
|Checksum ranges|the `FA DE CA FE CA FE AF FE` descriptors, each re-summed to confirm|

#### Checksums

Ranges are read from their descriptors rather than listed in the tool, because
they move: the big calibration range ends at 0x0FDFFB on some versions and
0x0FCFFB or 0x0FD7FB on others, and some images carry a range at 0x100000 that
others do not. The correction word is the last word of each range. Recomputing
it reproduces the factory value exactly in all 34 ranges across the five images
tested, so the formula is the one Bosch used.

Only ranges containing a changed byte are recomputed, innermost first so a
nested range is fixed before its parent is summed. Everything is re-verified
after patching and the tool refuses to write if anything fails.

#### The stub is generated, not copied

The rpm register, the pressure cell, the button cell and the stub address all
differ between versions, so a fixed blob would read the wrong RAM and store the
wrong register. Run against stock SW 1037390198 the generator reproduces the
file running in my car byte for byte, which is the regression test.

Measured on five images:

|SW|Gen|`--can11h`|`--boost`|
|---|---|---|---|
|1037390198|DDE606|yes|already patched (this is my file)|
|1037381343|DDE626|yes|yes, pressure 0x7F8B54, button 0x7FAEA1, r29|
|1037381791|DDE606 (E53)|yes|yes, pressure 0x7F8B32, button 0x7FB385, r29|
|1037387341|DDE606|yes|needs the pressure cell given|
|1037500774|DDE606|yes|needs the pressure cell given|

Checksums verify clean on all five after patching. CAN11h works on all five,
DDE626 included. The two that need help are a newer sub-generation (83 message
descriptors and 69/69/13 configs rather than 65 and 52/52/12) whose sensor
publish has a different shape; the hook, button cell, stub and checksums are
still found automatically, only the pressure cell is not.

### This software version: patch files

The bench-verified byte edits for SW 1037390198 are kept as patch files. `--apply` checks the stock bytes of every entry before writing anything, so a wrong software version is refused rather than half-applied:

```sh
python3 tools/dde6patch.py patch fullread.bin -o modified.bin --can11h --boost
```

```
$ python3 tools/dde6patch.py patch fullread.bin -o modified.bin --apply patches/can11h.patch --apply patches/mfl.patch
input: full read, 0x280000 bytes
  CAN11h conversion  (7 entries, chip FLS)
  MFL steering wheel buttons  (5 entries, chip FLS)

applied 12
  FLS checksum 0x040000-0x0BFFFF: word@0x0BFFFC = 5FE005EC
  FLS checksum 0x0C03D0-0x0FDFFB: word@0x0FDFF8 = 23A1C30C

wrote modified.bin  md5 ...
```

Every entry states the stock bytes it expects. If your file does not match, nothing is written and the mismatch is reported rather than guessed at. Re-running is safe; entries already present are counted and skipped.

`src/build_boost.py` is the alternative route for the boost patch alone. It assembles the stub from source rather than applying it as bytes, and produces an identical file:

```sh
python3 src/build_boost.py e39.MPC e39_boost.MPC
```

Inspect a file with the included tool, which takes either a full read or an FLS and MPC pair:

```sh
python3 tools/dde6re.py fullread.bin info
python3 tools/dde6re.py fullread.bin csum
python3 tools/dde6re.py fullread.bin dis 0x4340B8 0x434200
python3 tools/dde6re.py fullread.bin rw 0x7FB015
```

```
$ python3 tools/dde6re.py fullread.bin rw 0x7FB015
0x7FB015:
  WRITERS: 09A210:stb 09A6E8:stb 09A8F4:stb
  readers: 09A748:lbz 09A7B8:lbz 47F610:lbz
```

|Command|Does|
|---|---|
|`info`|identify the image and report what was loaded|
|`dis <start> <end>`|disassemble a range|
|`func <addr>`|disassemble one function|
|`xref <addr>`|find callers|
|`rw <addr>`|find every read and write of a RAM address|
|`runnables [tcb]`|dump the OSEK task tables|
|`findhook`|locate the boost hook site for this software version|
|`bases`|locate the global base register stubs|
|`maps`|find every calibration map in the FLS|
|`csum`|verify all checksums|
|`fixtrailer`|recalculate the MPC checksum word|

For IDA or Ghidra: PowerPC 32-bit big-endian, MPC at 0x400000 and FLS at 0x000000. Load the MPC at zero and nothing cross-references.

## Patches

No ECU binaries are distributed here. The modifications ship as byte-level patch files that apply to a dump you already own.

|Patch|Chip|Entries|Bytes|
|---|---|---|---|
|`patches/can11h.patch`|FLS|7|18|
|`patches/can11h_variant3.patch`|MPC|1|4|
|`patches/can11h_switches_only.patch`|FLS|1|11|
|`patches/mfl.patch`|FLS|5|5|
|`patches/boost_tacho.patch`|MPC|12|165|
|`patches/unknown.patch`|FLS|2|2|

The format is plain text, one edit per line, so it reads and diffs like source:

```
0x0417E0  21 -> 11  # 0x316 descriptor +0x0C
0x043DE0  0043AF84 -> 004340B8  # runnable -> CAN11h formatter 0x4340B8
```

Checksum correction words are deliberately not in the patch files. They depend on everything else in the range, so they are recomputed from the finished image.

`patches/unknown.patch` is a pair of bytes present in my own flashed file that I could not identify. It is checksum-neutral (+768 at 0x0C3C9C, -768 at 0x0C3CA0) and no modification here needs it. Skip it unless you know what it is.

`can11h_variant3.patch` + `can11h_switches_only.patch` is an alternative to
`can11h.patch` that reproduces a factory E53's CAN configuration instead of
working around it. Use one or the other, not both. See
[docs/CAN11h.md](docs/CAN11h.md).

Applying all four to a stock dump of SW 1037390198 / HW 0281013852 reproduces the image currently running in my car byte for byte.

## Modifications

|Modification|Result|Layer|Bytes|Status|
|---|---|---|---|---|
|[CAN11h](docs/CAN11h.md)|cluster and DSC see the engine|FLS|20|working in the car|
|[Boost on tacho](docs/BOOST_TACHO.md)|rev counter shows manifold pressure|MPC + FLS|180|working in the car|
|[MFL buttons](docs/MFL.md)|E39 wheel cruise buttons work|FLS|5|working in the car|
|Multimap|switchable calibrations|FLS + MPC|-|TBD, not finished|

All three touch checksummed ranges. Nothing boots without the checksums corrected afterwards.

## Reference

[docs/REFERENCE.md](docs/REFERENCE.md) covers the parts shared by all three modifications:

- the four global base registers, without which no data access disassembles correctly
- RAM layout, the heap, and the two stacks
- the checksum ranges and how to recalculate them
- using a DAMOS from a different software version, and the offset it needs
- finding calibration maps with no DAMOS at all
- interrupt vectors, turbo actuator mapping, multimap internals

## Abbreviations

|Term|Meaning|
|---|---|
|DDE|Digital Diesel Electronics, BMW's diesel ECU|
|FLS|the external calibration flash chip|
|MPC|the MPC563 internal program flash|
|FrmMng|Framework Manager, the CAN message layer in the firmware|
|MFL|multifunction steering wheel|
|TouCAN|the MPC563 CAN controller peripheral|
|OSEK|the automotive RTOS this firmware runs on|
|DAMOS|the file naming an ECU's maps and their addresses|
|CAN11h|the older BMW message set used by E39/E46|

## Contributing

Issues and pull requests welcome, particularly for:

- the `+0x0A` and `+0x0C` message descriptor fields. A factory CAN11h car (E53)
  does not have them set, so they are probably not required; see
  [docs/CAN11h.md](docs/CAN11h.md)
- the multimap state machine, which is mapped but not finished
- confirmation on other software versions. `findhook` locates the boost hook
  on five different SW numbers including a DDE626; porting the CAN11h and MFL
  edits the same way is not done yet

If you run this on a different SW number, the addresses will move. The method for finding them again is in [docs/REFERENCE.md](docs/REFERENCE.md).

## License

MIT © Adomas

Not affiliated with or endorsed by BMW or Robert Bosch GmbH. BMW, Bosch, DDE and EDC are trademarks of their respective owners.

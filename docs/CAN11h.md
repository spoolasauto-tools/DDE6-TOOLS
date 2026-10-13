# CAN11h conversion

The E60 DDE6 transmits the E60 message set. The E39 cluster and DSC want the older CAN11h set (0x316 / 0x329 / 0x545 and the ASC/DSC frames). Both message sets are already in the firmware as variants; the CAN11h one ships switched off and attached to a task that runs at the wrong rate.

Four edits: enable the variant's frames, fix two fields in each TX message descriptor, and move the formatter into the 10ms task.

All addresses are FLS unless marked MPC. Everything here sits inside checksum ranges 0x040000-0x0BFFFF and 0x0C03D0-0x0FDFFB, so the FLS needs its checksum fixed after editing (see [REFERENCE.md](REFERENCE.md)).

## Slot table ##

- Location: FLS 0x042064
- Variant: 2
- Slots: 12

|Slot|CAN ID|Dir|Ctrl/MB|
|---|---|---|---|
|0|0x316|TX|2/6|
|1|0x329|TX|2/7|
|2|0x545|TX|2/8|
|3|0x43F|RX|0/0|
|4|0x43B|RX|0/1|
|5|0x613|RX|0/2|
|6|0x615|RX|0/3|
|7|0x153|RX|0/4|
|8|0x1F0|RX|0/5|
|9|0x1F3|RX|0/6|
|10|0x1F8|RX|0/7|
|11|0x170|TX|2/9|

There is no steering-wheel message in this set, which is why the buttons need [MFL.md](MFL.md). If a tool shows a slot 28 on variant 2 it is reading FLS 0x042294, 0x140 past the end of the table.

## Edit 1: frame switches ##

Per-message enable bytes. The E60 software ships the E39 ones off.

|Address|Name|Stock|New|
|---|---|---|---|
|0x0D9221|FrmMng_swtASC1_C|00|01|
|0x0D9222|FrmMng_swtASC2_C|00|01|
|0x0D9223|FrmMng_swtASC3_C|00|01|
|0x0D9224|FrmMng_swtASC4_C|00|01|
|0x0D9225|FrmMng_swtCANA_C|01|00|
|0x0D9226|FrmMng_swtDDE1_C|00|01|
|0x0D9227|FrmMng_swtDDE2_C|00|01|
|0x0D9228|FrmMng_swtDDE4_C|00|01|
|0x0D9229|FrmMng_swtDSCBrkAct_C|00|01|
|0x0D922A|FrmMng_swtEGS1_C|00|01|
|0x0D922B|FrmMng_swtEGS2_C|00|01|

swtCANA_C goes the other way (01 to 00). Leave 0x0D922C swtEKP_C at 00; 0x0D922D/2E INSTR2/3 are already 01.

The bytes at 0x0D9210-0x0D9220 are calibration values (nML, nTSCErr, nTSC_Off, nTSC_Slope, pVal_Off, pVal_Slope, rFlt_Off, rFlt_Slope, stGearErr), not switches. Do not run a block edit backwards into them. The FrmMng calibration block base is 0x0D918C, reached in code as `[r15+0x2E8]`.

## Edit 2: message descriptors ##

- Table base: FLS 0x0417D4
- Record size: 0x10

Descriptor layout, 0x329 at 0x0417E4 as the example:

```
00 00 03 29 | 01 02 07 01 | 08 00 00 00 | 21 01 00 01
 CAN ID        F  C  .  MB   DLC           +0C
              F = flags (bit0 = standard id)   C = controller
```

Two fields change on every TX message:

|Field|CAN ID|Address|Stock|New|
|---|---|---|---|---|
|+0x0C|0x316|0x0417E0|21|11|
|+0x0C|0x329|0x0417F0|21|11|
|+0x0C|0x545|0x041800|21|11|
|+0x0C|0x170|0x041810|21|11|
|+0x0A|0x170|0x04180E|00|02|

Status: the +0x0A / +0x0C fields are required but not decoded. The +0x0C top nibble going 2 to 1 on every TX message lines up with the task move below, so it is probably a scheduler group, but that is unconfirmed.

## Edit 3: move the formatter ##

The CAN11h formatter exists but is attached to a task at the wrong rate.

|Address|Stock|New|Effect|
|---|---|---|---|
|0x043DE0|0043AF84|004340B8|this slot now runs the CAN11h formatter|
|0x043E64|004340B8|0043C2B8|old slot neutralised|

0x043E64 is runnable 18 of the task control block at 0x043E04 (`0x043E04 + 0x18 header + 18*4`). 0x43C2B8 already sits at 0x043E5C, so it is a deliberate duplicate of a harmless function and the slot now does nothing. Do not write 0x00000000 there; the scheduler calls whatever is in the table.

## Scheduling background ##

The firmware runs OSEK. Functions are not called by other functions; they are entries in tables the scheduler walks, which is why nothing has an xref.

- ActivateTask: MPC 0x47EB28, task control block in r3
- A task control block is a 0x18 header followed by function pointers

Rate comes from which list a function is in, not from the function or the message:

|Group byte|Trampoline (MPC)|List (FLS)|Rate|
|---|---|---|---|
|0x11|0x460C04|0x043D48|10ms|
|0x21|0x460C10|0x043E1C|~20ms|
|0x31|0x460C1C|0x0441C8|100ms|

The per-slot period table at RAM 0x7FF3F4 looks like it controls timing but does not; it is gated on RAM 0x7FAA74, which nothing ever writes.

## Transmit chain ##

Needed only if you add or change a frame rather than flipping switches.

```
FrmMng_Formatter_CAN11h   MPC 0x4340B8
   Com_SendMessage        MPC 0x460D50    lmw/stmw, 8 bytes at once
   Signal_Set             MPC 0x410984    one field
      Signal_PackToBuffer MPC 0x460C28
   -> runtime state A = [[0x7FAD18] + slot*0x14 + 0x10]
Com_TransmitWorker        MPC 0x46E00C    runs later, sends anything ready
TouCAN_WriteMailbox       FLS 0x01B090    writes the hardware
```

The formatter does not send; it fills a buffer and sets a flag, and the worker sends. That is why a frame can vanish while the formatter looks correct.

Slot descriptor, 0x14 stride, base `[0x7FAD18]`:

|Offset|Meaning|
|---|---|
|+0x08|pointer; byte [+1] of target is tested against 0xFF (purpose unknown)|
|+0x0C|B = message descriptor|
|+0x10|A = runtime state|

Runtime state A (12 bytes): +0x00..07 data, +0x08 countdown, +0x09 state (1 = send every pass, 3 = countdown done then send once), +0x0A flags (bit0/bit1 = skip), +0x0B status.

Descriptor B: +0x00 CAN ID (or pointer to it if flags bit4), +0x04 flags (bit0 std id, bit4 id is pointer), +0x05 controller, +0x06 mailbox, +0x08 DLC.

## Signal table ##

- Location: FLS 0x040642
- Record size: 6 bytes, indexed by signal ID
- The first two bytes echo the ID back, so a wrong index is obvious

|Offset|Meaning|
|---|---|
|+0,+1|signal ID|
|+2|slot|
|+3|byte order (2 = little-endian)|
|+4|start bit|
|+5|length in bits (0 = unused, packer returns)|

Worked example, the tacho field:

```
signal 0x116 = 01 16 00 02 10 10
  slot 0 (message 0x316), little-endian, start bit 16, length 16
  = bytes 2-3 of 0x316
```

## TouCAN hardware, FLS 0x01B090 ##

Controller bases at `[FLS 0x0003EE90 + ctrl*4]`:

|Ctrl|Base|Mailboxes from|
|---|---|---|
|0|0x00707080|0x00707100|
|1|0x00707480|0x00707500|
|2|0x00707880|0x00707900|

Mailbox N = base + 0x80 + N*16. The stride is 16, not 32; 32 gives a silent bus with no error, because the writes land in a mailbox that was never armed.

|Offset|Meaning|
|---|---|
|+0x00|control/status: bits4-7 CODE, bits0-3 LENGTH|
|+0x02|id, 32-bit; standard id = canid << 21|
|+0x06|data 0-3|
|+0x0A|data 4-7|
|+0x0E|timestamp|

CODE 8 = inactive, 0xC = transmit once. base+0x24 is IFLAG, acknowledged by writing ~(1<<n). Write order:

```
lhz  IFLAG [base+0x24]              ; our bit clear -> CODE must be 8 or bail
sth  ~(1<<mb) -> [base+0x24]        ; ack
MB[0x00] &= ~CODE ; MB[0x00] |= 0x80
stw  (canid<<21) -> MB[0x02]
stw  payload[0..3] -> MB[0x06]
stw  payload[4..7] -> MB[0x0A]
MB[0x00] &= ~LENGTH ; MB[0x00] |= dlc
MB[0x00] |= 0xC0                    ; go
lhz  [base+0x0A]                    ; readback, forces the write out
```

## Three things that silently kill a frame ##

|Cause|Where|Behaviour|
|---|---|---|
|Mailbox theft|ownership table RAM 0x7F85AE|worker takes a mailbox off another slot (ReloadCountdown, then claims it), no report|
|TX gate|Com_ChanGate MPC 0x4360D0|gate bit set, or FrmMng_CAL+0x9A == 0, makes the formatter jump 0x434A84 and skip the whole 0x316 build incl. all signal writes|
|Multiplex|0x545|which variant goes out is `(counter / FrmMng_ctrDiv_545) mod 4`; divisor 0x0D918E = 4, counter RAM 0x7FBD04|

Ownership table 0x7F85AE: 2 bytes per (controller*16 + mailbox), +0 owning slot, +1 busy. TX gate: Com_ChanDisable MPC 0x460048 / Com_ChanEnable MPC 0x460124, gate word RAM 0x7F92DC.

## Rolling your own frame (TBD, untested) ##

You can enter at TouCAN_WriteMailbox and skip the formatter, signal table, variant table and gate. You need your own 12-byte A block, your own 9-byte B descriptor on a mailbox nothing else uses, `bl 0x01B090` (or the hardware writes inline, which also dodges the ownership table), and one entry in task list FLS 0x043D48.

## Portability across software versions

Checked against five DDE6 images (four DDE606, one DDE626). The addresses move
with every software version, but the structure and the byte values do not.

|File|SW|Chassis|Descriptor table|Selector|CAN11h triple|
|---|---|---|---|---|---|
|CU78124|1037390198|E60 (mine)|0x0414A4|0x04109C|0x04113B|
|CU02765|1037387341|-|0x0413D8|0x0411D8|0x041277|
|KV70422|1037381343|DDE626|0x0414DC|0x0410D4|0x041173|
|LX80448|1037381791|E53|0x041388|0x040F80|0x04101F|
|VF14265|1037500774|-|0x0413DC|0x0411DC|0x04127B|

Three relationships hold in all five:

- **Every image carries three CAN configurations, and config 2 is always the
  CAN11h set.** Counts are 50/50/12 (mine), 52/52/12 (DDE626 and E53) and
  69/69/13 (the two newer files). The small third list is CAN11h every time.
- **The variant triple block is byte-identical and always at selector + 0x9F:**
  `01 01 01 01 01 01 | 00 00 00 | 01 00 01 | 01 00 01 | 02 01 02`, where
  `02 01 02` selects config 2.
- **The CAN11h descriptors hold the same values in every file**, stock:

```
0x316  00 00 03 16  01 02 06 01  08 00 00 00  21 01 00 01   ctrl 2 mb 6
0x329  00 00 03 29  01 02 07 01  08 00 00 00  21 01 00 01   ctrl 2 mb 7
0x545  00 00 05 45  01 02 08 01  08 00 00 00  21 01 00 01   ctrl 2 mb 8
0x170  00 00 01 70  01 02 09 01  04 00 00 00  21 01 00 01   ctrl 2 mb 9
0x153  00 00 01 53  01 00 04 00  08 00 00 00  10 01 00 00   ctrl 0 mb 4
0x1F0  00 00 01 f0  01 00 05 00  08 00 00 00  10 01 00 00   ctrl 0 mb 5
```

So the edits in this document are the same edits on any DDE6 image; only the
addresses need relocating. Find the descriptor table by matching the 0x316
record above, and the rest follows from it.

### How a factory CAN11h car actually does it

Every E53 runs CAN11h, so LX80448 is a working reference. Tracing it answers
what this document was previously guessing at.

The CAN11h formatter in every image opens with the same gate:

```
LX80448 (E53)   0x431D04  lbz r11, -0x7741(r13)   = RAM 0x7F88AF
mine   (E60)    0x4340B8  lbz r11, -0x7746(r13)   = RAM 0x7F88AA
                          cmpwi r11, 0x12
                          beq   <exit>            skip the whole frame build
```

So the E53 is not hard-enabled in calibration either. It runs the identical
conditional. One RAM byte decides it, and that byte is written from a table:

```
0x43336C (E53)  stb r3, -0x7741(r13)      preceded by
0x433354        lbz r3, -0x76E4(r13)      = RAM 0x7F890C, the variant mirror
0x433358        li  r4, 0
0x43335C        bl  0x413DF4              the mapping function

0x413DF4        cmpwi r3, 6               variants 0-5 only
0x413DFC        cmpwi r4, 0
0x413E04        addi  r12, r2, -0x2678    = FLS 0x045A78, the table
0x413E08        slwi  r11, r3, 3          index = variant * 8
0x413E0C        lbzx  r3, r12, r11
```

So `gate = TABLE[variant * 8]`. The table, 6 records of 8 bytes:

|Variant|Gate|Record|Meaning|
|---|---|---|---|
|0|0x12|`12 00 00 00 00 00 00 3C`|CAN11h formatter skips|
|1|0x12|`12 00 00 00 00 00 00 41`|CAN11h formatter skips|
|2|0x12|`12 00 00 00 00 00 00 5A`|CAN11h formatter skips|
|**3**|**0x11**|`11 00 00 00 00 00 00 53`|**CAN12H producers skip, CAN11h runs**|
|4|0x12|`12 00 00 00 00 00 00 57`|CAN11h formatter skips|
|5|0x12|`12 00 00 00 00 00 00 46`|CAN11h formatter skips|

This table is **byte-identical in all five images**, at five different
addresses:

|File|Table|
|---|---|
|CU78124 (mine, E60)|0x045C50|
|CU02765|0x0455B4|
|KV70422 (DDE626)|0x045D84|
|LX80448 (E53)|0x045A78|
|VF14265|0x0455FC|

The byte at +7 of each record is `3C 41 5A 53 57 46` in every file. Not
identified; recorded in case it means something to someone.

**Variant 3 is the CAN11h vehicle, and nothing in the flash selects it.** The
variant code arrives at RAM 0x7F890C (0x7F8903 in my file) through a runtime
pointer, `lwz r12,0x34(r15); lbz r4,0(r12)`, not from the calibration. So an
E53's CAN11h behaviour lives in its coding data, not in its FLS or MPC. Every
DDE6 flash image is equally capable of CAN11h; the car decides.

### Variant 3 takes its own code path

The dispatch is identical in both files. Variants 0, 1, 2, 4 and 5 all load a
triple pointer and fall into a common tail. Variant 3 does something else:

```
            lbz   r4, 0(r12)              read the coded variant
            cmpwi r4, 3
            stb   r4, <variant mirror>
            beq   -> variant 3 path
            ... 0,1,2,4,5 -> addi r4, r2, <triple> ; b <common tail>

variant 3:  addi  r12, r2, -0x6FB5        triple C, 02 01 02
            li    r4, 0
            stw   r12, -0x52B8(r13)       store the triple pointer to RAM
            stw   r4,  -0x5818(r13)       zero three words
            stw   r4,  -0x5814(r13)
            stw   r4,  -0x5810(r13)
            b     <a different tail>
```

So variant 3 is not just a different table row, it is a different setup path.
Forcing the dispatch to read 3 therefore reproduces an E53's CAN configuration
in full, and leaves all nine other consumers of the variant byte agreeing with
it instead of half-converted.

```
MPC 0x435324   888C0000 -> 38800003      lbz r4,0(r12)  ->  li r4,3
```

Four bytes. Confirmed on the bench: with the frame switches set, this made
0x316, 0x329, 0x545 and 0x170 transmit, with no descriptor edits and no runnable
swap. CAN12H stops completely, which for a pure CAN11h car is the point.

`patches/can11h_variant3.patch` plus `patches/can11h_switches_only.patch` is
that configuration: 21 bytes against stock, checksums included.

|Route|Edits|CAN12H|Matches a factory car|
|---|---|---|---|
|variant 3 + switches|4 MPC + 11 FLS|stops|yes|
|`can11h.patch` (this doc)|18 FLS|stops|no, and two bytes unexplained|

### What that means for the edits in this document

The patch here takes a third path: leave the variant alone and move the
formatter into a task that runs, with the frame switches on. It is confirmed
working in the car, but it is not how a factory CAN11h car is configured, and
two of its bytes are unexplained.

A factory CAN11h car has `+0x0C = 0x21` and `+0x0A = 0x00`, stock. The E53 file
confirms it. So **the descriptor edits are not what enables CAN11h** and are
probably unnecessary. They remain in the patch because the combination is
bench-verified, not because they were isolated.

Anyone porting this should try, in order: the variant force alone, then the
frame switches alone, before reproducing the descriptor edits.

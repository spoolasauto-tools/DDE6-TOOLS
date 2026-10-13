# MFL buttons

E39 steering-wheel cruise buttons arrive on a serial wire to ECU pin 37, not on CAN. The E60 firmware is listening for them on CAN. Five calibration bytes switch it over.

## The edit

|Address|Name|Stock|New|
|---|---|---|---|
|FLS 0x0CA926|CrCCD_stMSWModeDflt_C|00|01|
|FLS 0x0CA928|CrCCD_swtDevVal_C|02|01|
|FLS 0x0CA92A|CrCCD_swtMSWVal_C|00|01|
|FLS 0x0CA92D|CrCCD_tiDebSetMn_C+1|0C|00|
|FLS 0x0CA92F|CrCCD_tiDebSetPl_C+1|0C|00|

`CrCCD_swtDevVal_C` is the one that matters: 2 selects CAN, 1 selects the serial wire. With it set to 1, fn_09A1A4 at FLS 0x09A25C calls the serial init routines at FLS 0x06C21C and FLS 0x06C3B0. With 2 it never does.

These bytes are inside checksum range FLS 0x0C03D0-0x0FDFFB. See [REFERENCE.md](REFERENCE.md).

## Decode chain

Handler is fn_09A2F4. Four gates run before it decodes anything, and any one failing means it does nothing at all.

|Condition|Meaning|
|---|---|
|`[0x7F884B] == 1`|-|
|`[0x7FB012] == 0`|-|
|`[0x7F881A] != 0x30 and != 8`|engine running, see below|
|`[0x7FB010] != 0`|-|

FLS 0x09A3C4 calls fn_47E10C, which reads the serial word from `[[0x7FDA30]+4]+0` and extracts four fields with `rlwinm`.

The raw byte goes to 0x8FE667 at FLS 0x09A3D0, and to 0x7FB015 at FLS 0x09A6E8, but only if the validity check at FLS 0x09A6D0 passes (`([r1+0xc] - 0x961) <= 0x17F9`). If it fails, 0x7FB015 keeps 0xFE.

|Address|Contents|Notes|
|---|---|---|
|RAM 0x7FB015|raw code, 0xFE when idle|momentary, upstream of all cruise logic, works during a fault. Use this one|
|RAM 0x8FE667|the same byte into the ETK window|useless on a car without an ETK fitted|
|RAM 0x7F8849|latched byte, written at FLS 0x09AD48 every pass|bit0 +, bit1 -, bit2 resume, bit3 on/off. One shot, see below|
|RAM 0x7FB00E|cruise latch|-|
|RAM 0x7FB00F|phase flag|-|
|RAM 0x7FB017|previous raw bit|-|

## Protocol

Active low, two bits per button, each button owning its own pair.

|Button|Test|Bits|
|---|---|---|
|idle|`(code & 0xFE) == 0xFE`|-|
|on/off|`(code & 0x24) == 0`|2 and 5|
|+|`(code & 0x48)`|3 and 6|
|-|`(code & 0x14)`|2 and 4|
|resume|`(code & 0x90)`|4 and 7|

Because each button owns its own pair, `(code & 0x24) == 0` means on/off and nothing else.

## 0x7F8849 is a one shot

Worth reading before building any hold-to-do-something feature.

The byte looks like a button state. It is not. fn_09A2F4 writes r30 there every pass, and r30 holds 8 only on the single pass that recognises the press. RAM 0x7FB00E is then cleared at FLS 0x09AA80.

A counter that increments while bit 3 is set can therefore never exceed 1. There is no way to make it work.

It is also behind the gates above, so during a sensor fault, when cruise will not arm, the bit never sets at all.

Use RAM 0x7FB015 instead.

## 0x7F881A, the engine state gate

This is in the gate list above and explains "works with the engine running, does nothing with the ignition on".

- Seeded to 8 by fn_41DAB8, transitions in fn_41DACC
- Timing from RAM 0x7F87A8 minus a snapshot in RAM 0x7FAF58

|State|Transition|
|---|---|
|8|to 2 if `[0x7F89ED] != 0`, otherwise to 0x30 if `[0x7F89EF] == 0`|
|2|to 4 if `[0x7F89ED] != 0` and `[0x7F89E8] == 0`|
|4|writes RAM 0x7F8B76|
|0x30|handler at MPC 0x41DC7C|

Cruise bails out on 8 and on 0x30 (FLS 0x09A9AC and FLS 0x09A9B4). Both mean the engine is not running.

## The CAN route is a dead end

Every line below is correct and the conclusion it suggests is wrong. Recorded so the same dig is not repeated.

|Finding|Value|
|---|---|
|Code reads|RAM 0x7F88CB|
|Only writer|MPC 0x4386CC, inside a CAN receive decoder|
|Values from|MPC 0x438588-0x438658, signal 0xA7, 8 button codes|
|Signal 0xA7|`00 A7 1C 02 10 07`, so slot 28|
|Slot 28|exists on variants 0 and 1 only; CAN11h has 12 slots|

That reads as "the buttons cannot reach the ECU on this layout". It is the E60 path. On an E39 they never go near CAN.

The cruise lamp in the E39 cluster is driven by the stalk directly. It lights whether or not the ECU knows anything, so it confirms nothing.

Translation table for 0x7F88CB at MPC 0x438588, for reference:

|Code|Condition|Button|
|---|---|---|
|0xFF|-|nothing|
|0x24|`(code & 0x30) == 0x10`|on/off|
|0x01|`(code & 0xFF) == 0`|-|
|0x02|`(code & 0x0F) == 4`|-|
|0x48|`(code & 0x0F) == 1`|-|
|0x14|`(code & 0x0F) == 8`|-|
|0x18|`(code & 0x0F) == 2`|-|
|0x90|bit 5|-|

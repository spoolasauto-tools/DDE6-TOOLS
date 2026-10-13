; ---------------------------------------------------------------------------
; boost_stub.asm - boost on the tacho + hold-ON/OFF toggle
; BMW DDE6 / Bosch EDC16C35 / MPC563, SW 1037390198
;
; Installed at MPC 0x47F600 (free 0xFF space inside checksum range
; 0x404000-0x47FFFB).
;
; The hook replaces ONE instruction in FrmMng_Formatter_CAN11h:
;     0x4344AC   sth r28, 8(r1)      b3 81 00 08      <- stock
;     0x4344AC   bl  0x47F600        48 04 b1 55      <- patched
;
; FRAME NOTE: this stub makes its own stwu r1,-0x20(r1), so the caller's
; 8(r1) is 0x28(r1) in here. Storing to 8(r1) instead would corrupt the
; caller's locals. This is the easiest way to break the patch.
;
; r28 still holds the stock tacho value on entry, so the rpm path is a
; straight sth r28.
;
; r13 = 0x7FFFF0, so:
;     -0x4fdb -> 0x7FB015   raw wheel button code
;     -0x74e4 -> 0x7F8B0C   charge air pressure, published, hPa
;     -0x70   -> 0x7FFF80   display mode byte   (0 = rpm, 1 = boost)
;     -0x6e   -> 0x7FFF82   hold counter, halfword
;
; 0x7FFF80/82 are above the APPLICATION stack top (0x7FE99C) so the stack
; can never reach them, are referenced by nothing else in the image, and are
; zeroed by memclr(0x7FF000,0x800000) at boot so the ECU wakes in rpm mode.
; ---------------------------------------------------------------------------

                                        ; addr      encoding
stwu    r1, -0x20(r1)                   ; 0047F600  9421ffe0
stw     r9,  8(r1)                      ; 0047F604  91210008
stw     r10, 0xc(r1)                    ; 0047F608  9141000c
stw     r12, 0x10(r1)                   ; 0047F60C  91810010

; ---- read the raw wheel button code -------------------------------------
; Protocol is ACTIVE LOW with a redundant bit pair per button. Idle = 0xFE.
; ON/OFF owns bits 2+5 (mask 0x24); no other button clears that pair, so
; (code & 0x24) == 0 identifies ON/OFF on its own.
;
; Do NOT use 0x7F8849 here. That byte is the LATCHED decode and bit 3 is a
; one-shot event - it is 8 only on the single pass that recognises the press,
; so counting consecutive samples of it can never get past 1. It is also
; suppressed while a sensor fault stops the cruise FSM arming.

lbz     r12, -0x4fdb(r13)               ; 0047F610  898db025   [0x7FB015]
andi.   r12, r12, 0x24                  ; 0047F614  718c0024
cmpwi   r12, 0                          ; 0047F618  2c0c0000
beq     pressed                         ; 0047F61C  41820010   == 0 -> held down

released:
li      r10, 0                          ; 0047F620  39400000
sth     r10, -0x6e(r13)                 ; 0047F624  b14dff92   counter = 0
b       show                            ; 0047F628  48000028

pressed:
lhz     r10, -0x6e(r13)                 ; 0047F62C  a14dff92
addi    r10, r10, 1                     ; 0047F630  394a0001
clrlwi  r10, r10, 0x10                  ; 0047F634  554a043e   keep 16 bits
sth     r10, -0x6e(r13)                 ; 0047F638  b14dff92
cmpwi   r10, 0x1f4                      ; 0047F63C  2c0a01f4   500 passes
bne     show                            ; 0047F640  40820010
                                        ; task is 10 ms, so 500 = 5 s
lbz     r9, -0x70(r13)                  ; 0047F644  892dff90   [0x7FFF80]
xori    r9, r9, 1                       ; 0047F648  69290001   toggle
stb     r9, -0x70(r13)                  ; 0047F64C  992dff90

; ---- display -------------------------------------------------------------
show:
lbz     r12, -0x70(r13)                 ; 0047F650  898dff90
cmpwi   r12, 0                          ; 0047F654  2c0c0000
beq     rpm                             ; 0047F658  41820038

; boost: read the PUBLISHED charge air cell. One writer, 0x41A36C.
; On a sensor fault r27 there comes from the substitute ramp, so this parks
; at ~900 hPa instead of dropping to zero. Do NOT read 0x7F8B14 - that is the
; frozen last-good raw value and it lies when the sensor is dead.
lha     r12, -0x74e4(r13)               ; 0047F65C  a98d8b1c   [0x7F8B0C]
cmpwi   r12, 0                          ; 0047F660  2c0c0000
bge     clamp_hi                        ; 0047F664  40800008
li      r12, 0                          ; 0047F668  39800000
clamp_hi:
cmpwi   r12, 0x2800                     ; 0047F66C  2c0c2800   10240 hPa
ble     scale                           ; 0047F670  40810008
li      r12, 0x2800                     ; 0047F674  39802800
scale:
; hPa * 10000 / 1563 = hPa * 6.3979. The cluster divides the rpm field by
; 6.4, so the two cancel and the needle reads absolute hPa 1:1.
; Same factor the stock formatter uses on nEng (0x7F9506).
; Multiply FIRST, divide second, or truncation eats the result.
li      r10, 0x2710                     ; 0047F678  39402710   10000
mullw   r12, r12, r10                   ; 0047F67C  7d8c51d6
li      r10, 0x61b                      ; 0047F680  3940061b   1563
divwu   r12, r12, r10                   ; 0047F684  7d8c5396
sth     r12, 0x28(r1)                   ; 0047F688  b1810028   caller's 8(r1)
b       done                            ; 0047F68C  48000008

rpm:
sth     r28, 0x28(r1)                   ; 0047F690  b3810028   stock value

done:
lwz     r9,  8(r1)                      ; 0047F694  81210008
lwz     r10, 0xc(r1)                    ; 0047F698  8141000c
lwz     r12, 0x10(r1)                   ; 0047F69C  81810010
addi    r1, r1, 0x20                    ; 0047F6A0  38210020
blr                                     ; 0047F6A4  4e800020

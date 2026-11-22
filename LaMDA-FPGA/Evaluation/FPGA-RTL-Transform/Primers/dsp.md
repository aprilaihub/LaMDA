<!--
family: Xilinx 7-Series (Artix-7, Kintex-7, Virtex-7, Zynq-7000)
module: dsp
references: UG479 (7 Series DSP48E1 Slice User Guide), UG901 (Vivado Synthesis, "Inferring Multipliers and DSP blocks", use_dsp), UG953 (7 Series Libraries Guide, DSP48E1), DS181/DS182/DS183 (7 Series data sheets, DSP switching characteristics)
scope: resource reference only (no design-specific or task rules)
-->
## DSP48E1 (7-Series): architecture, inference, attributes and reports

Reference for the DSP48E1 slice. It describes what the resource is and how Vivado synthesis maps behavioral RTL onto it.

### 1. Architecture (UG479)
One DSP48E1 contains, in this order: an optional pre-adder, a signed multiplier, three multiplexers (X, Y, Z) feeding a 48-bit three-input ALU, and a pattern detector on the result.

| Port | Width | Meaning |
|------|-------|---------|
| `A` | 30 | Multiplier input uses `A[24:0]` (25 bit signed); all 30 bits form the upper part of the 48-bit `A:B` concatenation |
| `B` | 18 | Signed multiplier input; lower part of `A:B` |
| `C` | 48 | Direct ALU operand |
| `D` | 25 | Pre-adder operand |
| `P` | 48 | Result / accumulator |
| `PCIN` / `PCOUT` | 48 | Dedicated cascade of `P` to the next slice |
| `ACIN` / `ACOUT` | 30 | Dedicated cascade of the `A` operand |
| `BCIN` / `BCOUT` | 18 | Dedicated cascade of the `B` operand |
| `CARRYIN`, `CARRYOUT[3:0]`, `CARRYCASCIN/OUT`, `MULTSIGNIN/OUT` | 1 / 4 / 1 / 1 | Carry and sign chaining between slices |
| `PATTERNDETECT`, `PATTERNBDETECT`, `OVERFLOW`, `UNDERFLOW` | 1 each | Pattern detector and overflow flags |

- Multiplier: 25 x 18 two's complement, product 43 bit. An unsigned operand therefore has effectively one bit less (24 x 17).
- Pre-adder: 25 bit, `D +/- A` (selected by `INMODE`); the result (`AD`) feeds the multiplier.
- ALU: 48 bit. It can add or subtract, perform logic operations, and its input can be the product, `C`, `P` (accumulate), `PCIN`, or `A:B`.
- A slice sits in a dedicated DSP column. Cascade ports connect only vertically adjacent slices inside the same column through dedicated routing, no fabric resources.
- Every control input (`RST*`, `CE*`) is synchronous. The slice has no asynchronous reset or set.

### 2. Dynamic controls
`OPMODE[6:0]`, `ALUMODE[3:0]`, `INMODE[4:0]`, `CARRYINSEL[2:0]` select the data path each cycle (they can be tied to constants, which is what inference usually does).

OPMODE fields:

| Field | Value | Selected operand |
|-------|-------|------------------|
| X mux `OPMODE[1:0]` | 00 / 01 / 10 / 11 | 0 / multiplier partial product (`M`) / `P` / `A:B` |
| Y mux `OPMODE[3:2]` | 00 / 01 / 10 / 11 | 0 / `M` / all ones / `C` |
| Z mux `OPMODE[6:4]` | 000 / 001 / 010 / 011 | 0 / `PCIN` / `P` / `C` |
| Z mux `OPMODE[6:4]` | 100 / 101 / 110 | `P` (MACC extend) / `PCIN` shifted right 17 / `P` shifted right 17 |

A multiplication uses X = `M` and Y = `M` together. Typical ALU modes (`ALUMODE`): 0000 `Z + X + Y + CIN`, 0011 `Z - (X + Y + CIN)`.

### 3. Optional internal registers (attributes of the primitive)
Registers inside the slice cost no fabric flip-flops and have the shortest paths. Each register that is enabled adds one clock cycle on the paths through it.

| Attribute | Values (default) | Register |
|-----------|------------------|----------|
| `AREG` | 0, 1, 2 (1) | `A` input (two stages `A1`, `A2` when 2) |
| `BREG` | 0, 1, 2 (1) | `B` input |
| `ACASCREG`, `BCASCREG` | 0, 1, 2 (1) | Register stage tapped by the `ACOUT` / `BCOUT` cascade; must be consistent with `AREG` / `BREG` |
| `CREG` | 0, 1 (1) | `C` input |
| `DREG` | 0, 1 (1) | `D` input |
| `ADREG` | 0, 1 (1) | Pre-adder output |
| `MREG` | 0, 1 (1) | Multiplier output |
| `PREG` | 0, 1 (1) | Result `P` (also the accumulator register) |
| `OPMODEREG`, `ALUMODEREG`, `CARRYINREG`, `CARRYINSELREG`, `INMODEREG` | 0, 1 (1) | Control inputs |

Other attributes: `USE_MULT` (`"MULTIPLY"` default, `"DYNAMIC"`, `"NONE"`), `USE_DPORT` (`"FALSE"`/`"TRUE"`, enables the pre-adder), `USE_SIMD` (`"ONE48"` default, `"TWO24"`, `"FOUR12"`: splits the ALU into independent 24 or 12 bit adders, no multiplier), pattern-detector attributes (`USE_PATTERN_DETECT`, `PATTERN`, `MASK`, `SEL_PATTERN`, `SEL_MASK`, `AUTORESET_PATDET`).

Latency from the operands to `P`, counted as clocked stages:
- Multiply with `AREG = BREG = MREG = PREG = 1`: 3 cycles.
- Multiply-accumulate with `AREG = BREG = 1`, `MREG = 0`, `PREG = 1`: 2 cycles; the multiplier and the ALU form one longer combinational path between two registers.
- Every register set to 0 is a pure combinational path; the slice behaves as a combinational multiplier-adder.
- Synthesis does not create stages by itself. Registers present in the RTL are absorbed (section 5); there is no retiming unless it is requested explicitly.

### 4. Timing behavior
- The critical path of a DSP-based design is normally the internal path between two enabled registers, plus the fabric routing to the DSP inputs and from its output.
- Enabling more of `AREG/BREG`, `MREG`, `PREG` shortens the internal path (the multiplier is separated from the ALU by `MREG`; `PREG` removes the output routing from the path). The highest clock frequencies need all of them, which gives the 3-cycle multiply above.
- Adders, multiplexers and other fabric logic placed before an operand or after `P` run in LUT/CARRY4 and are slower than the dedicated paths inside the slice.
- Cascade paths (`PCOUT`->`PCIN`, `ACOUT`->`ACIN`) use dedicated wires; they are faster and use no fabric routing or LUTs compared with a fabric adder.
- The maximum frequency of the slice itself depends on the speed grade and on the registers enabled; the values are in the data sheets (DS181/DS182/DS183).

### 5. Inference from behavioral RTL (UG901)
Vivado synthesis recognises multipliers, multiply-add/subtract, multiply-accumulate and multiplications with a pre-adder, and maps them to DSP48E1. Registers around the operation are absorbed into the internal registers of the slice.

A register is absorbed into a DSP register when:
- It is located directly on an operand (`A/B/C/D`), directly after the pre-adder (`AD`), directly after the multiplier (`M`), or on the result (`P`).
- Its reset is synchronous (single reset signal for the group) and its enable is a plain clock enable. Asynchronous reset or set on such a register prevents the absorption (the slice has none).
- No other logic lies between the register and the DSP, and the register output has no other consumer; extra fanout of an intermediate register may keep a fabric copy or prevent the absorption.
- At most two stages per `A` / `B` operand, and one stage for `C`, `D`, `AD`, `M`, `P`.

Templates (all registers synchronous, non-blocking):
```
// multiply: operand registers -> AREG/BREG, product register -> MREG, output register -> PREG
always @(posedge clk) begin
    a_r <= a;  b_r <= b;
    m_r <= a_r * b_r;
    p_r <= m_r;
end

// multiply-accumulate in one statement: accumulator -> PREG (OPMODE feedback of P)
always @(posedge clk) begin
    if (rst) acc <= 0;                 // synchronous reset -> RSTP
    else     acc <= acc + a_r * b_r;
end

// post-add with C and pre-add with D
always @(posedge clk) p <= a_r * b_r + c_r;
always @(posedge clk) p <= (d_r + a_r) * b_r;
```
Further patterns Vivado handles:
- Multiply-subtract (`- a * b`), accumulate with synchronous clear `acc <= clr ? a*b : acc + a*b;` (dynamic `OPMODE`), enable (`if (en) ...`, maps to `CE*`).
- Chains of multiply-adds where one stage adds the registered `P` of the previous stage can use the `PCOUT` -> `PCIN` cascade between consecutive slices instead of a fabric adder. Operand registers that only shift an operand to the next stage can use the `ACOUT`/`BCOUT` cascade.
- Operations wider than 25 x 18 are split over several slices plus fabric adders; results wider than 48 bit need fabric logic.
- Constant operands, very small operands and a multiplication that is not registered are frequently kept in LUT/CARRY4 by the tool.

### 6. Controlling the choice (UG901)
- `(* use_dsp = "yes" *)`: force the operation onto DSP48E1. `"no"` (or `"logic"`): force LUT/CARRY4. `"simd"`: map adders/subtractors to the SIMD mode of the ALU.
- The attribute can be placed on the module (affects every arithmetic operation in it) or on the signal/register that holds the result of the operation.
- Without the attribute the tool decides with internal heuristics. In the baseline runs recorded for this repository, a signed 8 x 8 multiply-accumulate with registered operands and accumulator was implemented with LUT/CARRY4 and 0 DSP48E1 until DSP use was requested. Do not assume that a multiplication becomes a DSP without checking the report.
- Using DSP48E1 where the design is small can trade LUT/flip-flop area for DSP area and routing to the DSP column; check the effect in the reports.

### 7. How a DSP shows up in the Vivado reports
- Utilization report, section "DSP": row `DSPs` (used / available; the device has 240 on `xc7a100t`). The primitive table lists `DSP48E1`.
- Synthesis log: the line `DSPs: <N> (col length:<L>)` describes the device. When DSP48E1 are inferred, a "DSP Final Report" table is printed with one row per DSP: `DSP Mapping` (for example `A*B`, `(A*B)'`, `PCIN+A*B`, `C'+(A:B)'`; a prime marks a registered result), the operand sizes (`A/B/C/D/P Size`) and the register columns (`AREG`, `BREG`, `CREG`, `DREG`, `ADREG`, `MREG`, `PREG`, `INMODEREG`, `OPMODEREG`, `ALUMODEREG`, ...). The value of each register column is the number of stages absorbed.
- If a multiplication you expected on a DSP is missing from the table, it was implemented in LUT/CARRY4; the LUT and `CARRY4` counts in the primitive table then include it.
- Timing report: a path starting or ending at a pin such as `<instance>/CLK` of a `DSP48E1` shows the internal delay of the slice; if the path has many `CARRY4` stages instead, arithmetic is in fabric.

### 8. Common reasons a DSP is not used or not fully used
- Asynchronous reset/set on registers around the arithmetic, or registers with different reset or enable signals inside one operand group.
- Logic between the register and the operand (for example a multiplexer or an addition that is not a pre-adder).
- An intermediate register with a second consumer.
- The accumulation split over several statements or blocks (product and sum in different always blocks).
- An unsigned operand mixed into a signed expression, which changes the arithmetic and may change the mapping.
- Operand widths larger than 25 x 18 (several slices) or an accumulator wider than 48 bit.

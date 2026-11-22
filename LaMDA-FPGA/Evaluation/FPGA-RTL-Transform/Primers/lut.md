<!--
family: Xilinx 7-Series (Artix-7, Kintex-7, Virtex-7, Zynq-7000)
module: lut
references: UG474 (7 Series FPGAs CLB User Guide), UG901 (Vivado Synthesis, HDL coding techniques, synthesis attributes), UG953 (7 Series Libraries Guide: LUT1-6, CARRY4, MUXF7/8, FDRE/FDSE/FDCE/FDPE, SRL16E, SRLC32E), UG949 (UltraFast Design Methodology, logic levels and control sets)
scope: resource reference only (no design-specific or task rules)
-->
## CLB logic (7-Series): LUT, carry chain, wide multiplexers, flip-flops, shift registers

Reference for the general-purpose fabric: look-up tables, carry chains, multiplexers, flip-flops and shift registers, and how Vivado synthesis maps behavioral RTL onto them.

### 1. The slice (UG474)
- A CLB contains two slices. Each slice has 4 LUT6 (`A`..`D`), 8 storage elements, 3 wide-multiplexer stages (`F7AMUX`, `F7BMUX`, `F8MUX`) and one 4-bit carry chain (`CARRY4`).
- `SLICEL`: logic, carry chain and wide multiplexers. `SLICEM`: all of that plus the use of the LUTs as distributed RAM or shift registers. About one third of the slices are `SLICEM`.
- Resources are reported per device, for example `xc7a100t`: 63400 Slice LUTs, 126800 Slice Registers, 15850 slices, 19000 LUTs usable as memory.

### 2. LUT6 and LUT5 (UG474, UG953)
- A `LUT6` implements any Boolean function of up to 6 inputs. `LUT1`..`LUT5` are the same hardware with fewer inputs.
- A LUT can produce two outputs at once (`O6` and `O5`, `LUT6_2`): two independent functions of up to 5 inputs that share their inputs. The utilization report shows this as "using O5 and O6"; it is the reason why "Slice LUTs" can be lower than the number of logic functions ("LUT value is adjusted to account for LUT combining").
- Logic depth: each LUT level adds one logic delay plus the routing to it. In implemented designs the routing delay is often as large as the logic delay, so fewer logic levels on a path help twice.
- Constant folding, common-subexpression sharing and dead-logic removal happen automatically; signals whose outputs are unused (for example upper product bits) are trimmed, so declaring only the needed width is the main way to avoid unneeded LUTs.
- Mapping rules of thumb: a function of at most 6 variables is one LUT; reductions (`&x`, `|x`, `^x`) of 6 bits are one LUT, wider ones form a tree of LUTs; a `case` or `if` selecting among 4 sources is one LUT6 (4 data + 2 select); equality compare of two N-bit values needs about N/3 LUTs plus a reduction tree.

### 3. Carry chain `CARRY4` (UG474)
- `CARRY4` has inputs `CI`, `CYINIT`, `DI[3:0]`, `S[3:0]` and outputs `O[3:0]` (sums), `CO[3:0]` (carries). One `CARRY4` plus 4 LUTs (the propagate/select signals) implement a 4-bit adder; the carry travels through dedicated vertical wires to the next slice in the column.
- An N-bit `+`, `-`, comparison (`<`, `>`, `<=`, `>=`, and `==` in some forms) or counter maps onto about `ceil(N/4)` `CARRY4` and about N LUTs. The carry delay per slice is small but accumulates: a long chain (for example 12 `CARRY4` in series) is the common critical path of wide arithmetic.
- Adding more than two operands in one expression creates a second carry level or compressor LUTs; the depth in `CARRY4` grows with the number of additions that depend on each other.
- Use the native operators (`sum = a + b + cin;`, `a - b`). A hand-written ripple adder made of `xor`/`and` per bit is usually not recognized as a carry chain and uses plain LUTs with a long path.
- Shifts by a constant are wiring and cost nothing; shifts by a variable amount are multiplexer levels; a multiplication by a constant is shift-and-add built from carry chains; a general multiplier in LUTs is a large array of LUTs and `CARRY4` (a DSP48E1 is the alternative, see the DSP primer).

### 4. Wide multiplexers `MUXF7`, `MUXF8`
- `MUXF7` combines the outputs of two LUT6 of a slice into an 8:1 multiplexer (or a 7-input function); `MUXF8` combines two `MUXF7` into a 16:1 multiplexer using four LUT6 across two slices. They use dedicated paths and avoid an extra LUT level.
- A wide selection written as one `case` statement or one indexed read (`data[sel]`) is recognized as a multiplexer and can use them. Priority-encoded `if / else if` chains are inferred as priority logic with more levels; use `case` (parallel, no priority) when the conditions are mutually exclusive.
- The report lists `F7 Muxes` and `F8 Muxes` under "Slice Logic".

### 5. Flip-flops and control sets
- Flip-flop primitives: `FDRE` (synchronous reset), `FDSE` (synchronous set), `FDCE` (asynchronous clear), `FDPE` (asynchronous preset), all with clock enable; latches `LDCE`/`LDPE`.
- Per slice, the storage elements share a clock, a set/reset signal and clock-enable signals (CE is shared per group of flip-flops). A "control set" is the combination (clock, set/reset, clock enable). Flip-flops with different control sets cannot share a slice, so many small control sets waste slices and routing. The report prints "Unique Control Sets".
- Synchronous reset (`if (rst) q <= 0;` in a clocked block) is the preferred style: it maps to `FDRE`, packs well, and can be absorbed by DSP and block RAM registers. Asynchronous reset (`always @(posedge clk or posedge rst)`) maps to `FDCE`/`FDPE` and prevents absorption into DSP/RAM registers and into shift-register LUTs.
- A flip-flop without reset costs the same as one with reset; a reset only adds a routing net. Reset only the registers whose value matters.
- A flip-flop located directly behind a LUT output uses the LUT's slice flip-flop at no extra routing; "Register driven from within the Slice" in the report counts such cases.
- Register duplication for high fanout: `(* max_fanout = N *)` on a signal tells synthesis to replicate its driver; `(* keep = "true" *)` / `(* dont_touch = "true" *)` stop optimization of a signal.
- Constant-input registers and registers without loads are removed; registers with identical inputs are merged unless prevented.

### 6. Shift registers `SRL16E`, `SRLC32E` (UG474)
- In a `SLICEM` one LUT can hold a 16 or 32 bit shift register (`SRL16E`, `SRLC32E`); four LUTs in a chain give up to 128 bits. The read tap is chosen by a 5-bit address (dynamic) or fixed; the shift happens on the clock edge, the tap read is asynchronous.
- Inferred from: `always @(posedge clk) if (ce) sr <= {sr[N-2:0], din};` where only one tap (or a dynamically indexed tap `sr[addr]`) is read, there is no reset of the shift register, and the elements have no other consumers. A fixed delay of N cycles then costs about `ceil(N/32)` LUTs instead of N flip-flops.
- Typically an extra flip-flop is added after the SRL output (the SRL read is slow), so the used latency is the shift length plus that register in the netlist, but not in the RTL.
- Not inferred when: the elements are reset, intermediate taps are read in other logic, the structure has individual enable differences per bit, or the length is too short to be worthwhile. `(* shreg_extract = "no" *)` forbids it, `"yes"` allows it.
- The report lists "LUT as Shift Register" under "LUT as Memory"; they count against the same LUT total.

### 7. Inference summary

| RTL | Maps to |
|-----|---------|
| Arbitrary Boolean expression, `case`, `?:` on few inputs | LUT1..LUT6 |
| `+`, `-`, comparison, counter, wide width | CARRY4 + LUT |
| `data[sel]`, `case` with 8 to 16 sources | LUT6 + MUXF7 / MUXF8 |
| `q <= d;` in a clocked block with sync reset / enable | FDRE |
| Delay line with a single read tap, no reset | SRL16E / SRLC32E |
| Array with asynchronous read | LUT RAM (see the memory primer) |
| Multiplier, multiply-add, accumulate with registers | DSP48E1 or LUT/CARRY4 (see the DSP primer) |

### 8. How logic shows up in the Vivado reports
- Utilization: "Slice LUTs" (with "LUT as Logic" and "LUT as Memory"), "Slice Registers" ("Register as Flip Flop" / "Register as Latch"), "F7 Muxes", "F8 Muxes". "Summary of Registers by Type" lists the registers by clock enable, synchronous or asynchronous set/reset. The primitives table lists `LUT1`..`LUT6`, `CARRY4`, `FDRE`, `FDSE`, `FDCE`, `FDPE`, `MUXF7`, `MUXF8`, `SRL16E`, `SRLC32E`.
- A large `CARRY4` count means that arithmetic is in fabric. A large `LUT2` count next to many `CARRY4` is typical of adders and comparators.
- Timing report, per path: `Data Path Delay` split in logic and route, and `Logic Levels: N (CARRY4=a LUT3=b LUT6=c ...)`, the cell types on the critical path. Many `CARRY4` in the list: carry chain too long; many `LUT` levels: deep logic; high route share: high fanout or poor placement.
- Slack: the worst negative slack (WNS) against the clock period gives the achievable frequency, `1 / (period - WNS)`.

### 9. Anti-patterns
- Gate-level carry or adder logic instead of the arithmetic operators.
- Deep chains of dependent arithmetic and logic between two registers when the same result is available earlier in the pipeline.
- Declaring signals wider than needed (unused upper bits cost LUTs and carry stages until trimmed, and block the trimming of dependent logic).
- Asynchronous resets on registers that do not need them, a different reset or enable per bit of a vector, multiple clock enables on one datapath: many control sets.
- Priority `if / else if` chains where a `case` would do.
- Relying on initial values of registers.

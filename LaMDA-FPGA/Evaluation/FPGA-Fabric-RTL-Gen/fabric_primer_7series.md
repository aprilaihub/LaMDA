# Xilinx 7-Series Fabric Primer

This primer summarizes the AMD/Xilinx 7-Series (Artix-7, Kintex-7, Virtex-7,
Zynq-7000) logic fabric primitives and the RTL coding idioms that let Vivado
synthesis infer them automatically. It is injected into the LLM system prompt
so the generated Verilog is written with fabric efficiency in mind, without
requiring manual primitive instantiation.

## 1. CLB / Slice Basics

- A Configurable Logic Block (CLB) contains two SLICEs (`SLICEL` or `SLICEM`).
- Each slice has 4 look-up tables (LUTs) and 8 flip-flops, plus dedicated
  carry and wide-mux logic.
- `SLICEM` slices can also be configured as distributed RAM (`RAM32X1S`, ...)
  or as shift-register LUTs (`SRL16E`, `SRLC32E`).

## 2. LUT6 / LUT5 (general logic)

- Every 6-input Boolean function maps to one `LUT6`. A `LUT6_2` can also
  implement two independent 5-input functions (`LUT5`) with shared inputs.
- Idiom: keep combinational functions expressed as plain Boolean/`case`
  expressions with at most 6 relevant inputs feeding a single output bit
  where possible; avoid manually decomposing logic into many tiny gates,
  which only adds artificial hierarchy without helping synthesis.

## 3. CARRY4 (fast carry chain)

- Each slice includes a `CARRY4` primitive: 4 dedicated fast-carry bits used
  for addition, subtraction, comparison, and incrementers.
- Idiom for fabric-efficient arithmetic:
  - Use native operators on full-width vectors: `sum <= a + b + cin;`
    `assign {cout, sum} = a + b + cin;`
  - Do **not** hand-write a bit-by-bit ripple-carry loop with explicit
    `and`/`xor` gates per bit — this often prevents Vivado from mapping the
    result onto `CARRY4` and instead consumes plain LUTs.
  - Comparators (`>`, `<`, `>=`, `<=`, `==`) on vectors also map onto
    `CARRY4`; prefer them over manual bit-serial comparison logic.
- Expect ceil(width / 4) `CARRY4` instances for an N-bit adder/subtractor.

## 4. DSP48E1 (arithmetic/DSP slice)

- Each `DSP48E1` provides a 25x18 signed multiplier, a 48-bit adder/ALU, and
  optional pre-adder and pipeline registers, all in dedicated silicon.
- Idiom for multiply and multiply-accumulate (MAC):
  - Multiplication: `product <= a * b;` on registered operands/outputs so
    Vivado can infer a full `DSP48E1` datapath (including its output
    register) instead of LUT-based partial-product logic.
  - MAC/accumulate: keep the multiply and the accumulation in the **same**
    synchronous always-block/register chain, e.g.
    `acc <= acc + (a * b);` so the DSP's internal adder is used instead of
    inferring a separate adder in fabric.
  - Avoid splitting the multiply and the addition across unrelated always
    blocks or mixing them with unrelated combinational logic; that tends to
    break DSP inference into a LUT multiplier plus a separate fabric adder.
  - For unsigned operands, ensure operand widths stay within the DSP's
    native operand sizes (up to 25x18 bits) to fit in a single `DSP48E1`
    rather than being split into multiple slices/LUT logic.

## 5. MUXF7 / MUXF8 (wide multiplexers)

- `MUXF7` combines two LUT6 outputs into an 8:1 mux; `MUXF8` combines two
  `MUXF7` outputs into a 16:1 mux, all in dedicated per-slice mux logic
  instead of general LUT fabric.
- Idiom: describe wide selects using a single `case` statement or a
  ternary/priority chain over the full select vector, e.g.
  `case (sel) ... endcase` or nested `?:` on `sel`, rather than a manually
  unrolled tree of small 2:1 muxes built from separate always blocks. Clean,
  single-block `case`/`if-else` selection logic gives Vivado the best chance
  to pack the result onto `MUXF7`/`MUXF8`.

## 6. SRLC32E / SRL16E (shift-register LUTs)

- A `SLICEM` LUT can be configured as a 16- or 32-bit shift register
  (`SRL16E`, `SRLC32E`) instead of a chain of flip-flops, saving flip-flop
  and routing resources for pure delay lines / shift registers.
- Idiom: describe fixed-length shift registers as a simple synchronous shift
  on a vector or array, e.g. `shreg <= {shreg[N-2:0], data_in};`, with a
  single clock and no per-bit reset/enable divergence, so Vivado can pack it
  into `SRLC32E`/`SRL16E` instead of N discrete `FDRE` flip-flops.
- If individual taps must be observed every cycle (not just the final
  output), a plain register chain may still be preferable — mention this
  trade-off is acceptable when functionally required.

## 7. RAMB18E1 / RAMB36E1 (Block RAM)

- Block RAM is inferred from a synchronous read/write array pattern:
  registered write on a clock edge, and a registered (synchronous) read
  through the same array indexed by an address signal.
- Idiom: model memories as `reg [WIDTH-1:0] mem [0:DEPTH-1];` with writes
  and reads gated by `always @(posedge clk)`, avoiding combinational
  (asynchronous) reads if block RAM inference is desired.

## 8. General Anti-Patterns That Block Fabric-Efficient Inference

- Manually decomposing arithmetic into discrete gate-level carry logic
  instead of using `+`/`-`/comparison operators on vectors (blocks CARRY4).
- Splitting a multiply-accumulate across multiple unrelated always blocks or
  interleaving it with unrelated control logic (blocks DSP48E1 inference).
- Building wide muxes from deeply nested, separately-clocked 2:1 mux trees
  instead of one `case`/ternary expression (blocks MUXF7/MUXF8 packing).
- Using asynchronous/combinational memory reads when block RAM inference is
  desired (forces distributed RAM or plain registers instead of BRAM).
- Introducing per-bit resets/enables inside what should be a uniform shift
  register (blocks SRL16E/SRLC32E packing into single LUTs).

## 9. Summary Guidance for the LLM

When generating Verilog for this pipeline: write clean, idiomatic RTL using
full-vector arithmetic, single `case`/ternary select statements, and simple
synchronous array-based memories/shift-registers. Let Vivado's synthesis
engine infer `CARRY4`, `DSP48E1`, `MUXF7`/`MUXF8`, `SRLC32E`/`SRL16E`, and
`RAMB18E1`/`RAMB36E1` automatically from that style — do not manually
instantiate primitives unless explicitly instructed to do so.

## 10. Structural Instantiation Templates (explicit primitive instantiation)

The following templates are reference examples for **directly instantiating**
UNISIM primitives by name, used only when explicitly instructed to do so
(the `explicit_primitive` generation style). Port names/widths are as defined
by the Xilinx UNISIM library for 7-Series.

### 10.1 CARRY4 (cascaded fast-carry chain)

A single `CARRY4` handles 4 bits of carry propagation. Chain multiple
`CARRY4` instances (CO[3] of one feeding CI of the next) to cover wider
additions. Example skeleton for an N-bit adder (N a multiple of 4):

```verilog
wire [N-1:0] p = a ^ b;       // propagate
wire [N-1:0] g = a & b;       // generate
wire [N:0]   carry;           // carry[0] = cin, carry[N] = cout
assign carry[0] = cin;

genvar i;
generate
  for (i = 0; i < N; i = i + 4) begin : carry_chain
    CARRY4 carry4_inst (
      .CO(carry[i+4:i+1]),    // carry-out per bit
      .O(sum[i+3:i]),         // sum bits
      .CI(carry[i]),          // carry-in from previous CARRY4 (or cin)
      .CYINIT(1'b0),          // only used for the first slice if not chaining CI
      .DI(g[i+3:i]),          // generate term per bit
      .S(p[i+3:i])            // propagate term per bit
    );
  end
endgenerate
assign cout = carry[N];
```

### 10.2 DSP48E1 (Arithmetic / DSP Slice)

`DSP48E1` has ~40 ports and **eleven independent register-enable parameters**, not just the four commonly-known ones. Every one of them defaults to `1` on its own:
`AREG`, `BREG`, `CREG`, `MREG`, `PREG`, `ADREG`, `DREG`, `INMODEREG`, `ALUMODEREG`, `OPMODEREG`, `CARRYINREG`, `CARRYINSELREG` (plus the cascade registers `ACASCREG`/`BCASCREG`, which track `AREG`/`BREG`). To prevent the outputs from sitting at a constant 0 or `X`, you **must explicitly configure ALL of them** depending on whether the design is combinational or synchronous, and you **must explicitly wire every input port used by the datapath you selected via `OPMODE`/`CARRYINSEL`** — never leave an input port disconnected/floating just because it "shouldn't matter" for your operation. Copy Template A or Template B below verbatim (only changing operand widths/extension and the port names for `a`/`b`/`product`/`clk`/etc.) rather than trimming the parameter or port list down.

**Critical cascade-register rule**: whenever `AREG` or `BREG` is set to a non-default value, the matching cascade-register parameter **must be set to the same value** — `ACASCREG` must equal `AREG`, and `BCASCREG` must equal `BREG`. Leaving them at their mismatched defaults triggers a UNISIM `Attribute Syntax Error` at elaboration that immediately calls `$finish`, so the simulation ends at time 0 with every output stuck at its initial (`X`/`0`) value.

**Critical "no-clock, constant-0-output" rule**: if `CLK` is tied to a constant (e.g. `1'b0`) for a fully combinational instantiation, then **every** register-enable parameter — including `OPMODEREG`, `ALUMODEREG`, `CARRYINREG`, `CARRYINSELREG`, `CREG`, `ADREG`, `DREG` — **must also be explicitly set to `0`**. If even one is left at its default `1` with no clock edges, that control signal never loads the real port value — it stays latched at its GSR power-up value of all-`0` forever, corrupting the ALU's X/Y/Z mux selection so **`P` silently reads as a constant `0`** (no simulation errors, just silently wrong data). This is why `OPMODEREG` in particular is easy to forget: it is not one of the "well-known four" (`AREG`/`BREG`/`MREG`/`PREG`) but is just as critical.

**Critical "unconnected-port-injects-X" rule**: once you bypass a register (set it to `0`), the corresponding input port becomes a **live combinational input** feeding the ALU/datapath on every delta cycle — even if your chosen `OPMODE`/`CARRYINSEL` "shouldn't" use it. In particular, `CARRYINREG(0)` and `CARRYINSELREG(0)` with `CARRYINSEL(3'b000)` route the `CARRYIN` port *directly* into the adder's carry-in bit; if `CARRYIN` is left unconnected (floating), it reads as `X` in simulation and the adder's XOR-based sum (`sum = z ^ x ^ y ^ carry`) turns the **entire 48-bit `P` output into `X`**, even though `A`/`B`/`C` are all valid. Always explicitly tie `.CARRYIN(1'b0)` and `.D(25'b0)` (even when unused, i.e. `USE_DPORT("FALSE")`) — see the fully-wired templates below, which tie off every input port with no exceptions.

#### Template A: Fully Combinational Multiplier (No clock, combinational logic only)
To implement a combinational multiplier (e.g. `mult_16x16_unsigned`), you **must bypass ALL eleven register-enable parameters** and **must wire every single input port** (no port left unconnected), exactly as shown below.
- `OPMODE(7'b0000101)` configures the post-adder to bypass the `C`/`P` feedback paths and output only the multiplier product (`Z_mux = 0`, `Y/X_mux = MULT`).
- `CARRYINSEL(3'b000)` selects `CARRYIN` (tied to `0`) as the adder's carry-in, contributing nothing extra to the multiply result.

```verilog
// 16x16 Unsigned Combinational Multiply (A x B -> P)
// Zero-extend/Sign-extend operands to native sizes (a to 30 bits, b to 18 bits)
wire [29:0] a_ext = {14'b0, a}; // 16 bits zero-extended to 30
wire [17:0] b_ext = {2'b0, b};  // 16 bits zero-extended to 18
wire [47:0] product_wide;

DSP48E1 #(
  .USE_MULT("MULTIPLY"),
  .USE_DPORT("FALSE"),
  .USE_PATTERN_DETECT("NO_PATDET"),
  .A_INPUT("DIRECT"),
  .B_INPUT("DIRECT"),
  // Bypass ALL eleven register-enable parameters (no clock edges will ever occur):
  .AREG(0), .ACASCREG(0),   // MUST match: both 0
  .BREG(0), .BCASCREG(0),   // MUST match: both 0
  .CREG(0),
  .MREG(0),
  .PREG(0),
  .ADREG(0),
  .DREG(0),
  .INMODEREG(0),
  .ALUMODEREG(0),
  .OPMODEREG(0),      // Easy to forget: without this, OPMODE never takes effect and P reads as 0
  .CARRYINREG(0),
  .CARRYINSELREG(0)
) dsp_mult_inst (
  .A(a_ext),          // 30-bit zero-extended operand A
  .B(b_ext),          // 18-bit zero-extended operand B
  .C(48'b0),          // Unused post-adder input C; tie to 0
  .D(25'b0),          // Unused pre-adder input D (USE_DPORT=FALSE); tie to 0
  .CARRYIN(1'b0),     // MUST tie explicitly: with CARRYINREG=0 this feeds the adder directly;
                      // leaving it unconnected injects X into every bit of P
  .P(product_wide),   // 48-bit raw product; slice to get needed width (e.g., product_wide[31:0])
  .CLK(1'b0),         // Tied to 0 since all registers are bypassed
  .CEA1(1'b0), .CEA2(1'b0), .CEB1(1'b0), .CEB2(1'b0), .CEC(1'b0),
  .CEAD(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0),
  .CEALUMODE(1'b0), .CECTRL(1'b0), .CEINMODE(1'b0), .CECARRYIN(1'b0),
  .RSTA(1'b0), .RSTB(1'b0), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0), .RSTP(1'b0),
  .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0), .RSTCTRL(1'b0), .RSTINMODE(1'b0),
  .ALUMODE(4'b0000),  // 4'b0000: Addition/Pass-through
  .OPMODE(7'b0000101), // 7'b0000101: Selects 0 + Multiplier product
  .INMODE(5'b00000),
  .CARRYINSEL(3'b000), // Selects CARRYIN (tied 0) as the adder carry-in
  // Unused outputs left unconnected (safe: only unconnected INPUTS inject X):
  .ACOUT(), .BCOUT(), .CARRYCASCOUT(), .CARRYOUT(), .MULTSIGNOUT(),
  .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .PCOUT(), .UNDERFLOW()
);
```

#### Template B: Synchronous Multiply-Accumulator (Single-cycle latency)
To implement a multiply-accumulate unit (e.g., `mac_8x8_accum`) where the accumulation `acc <= acc + a * b` happens in **exactly one clock cycle**, you **must set and enable the output register** `PREG(1)`, but **bypass every other register-enable parameter** (`AREG(0), BREG(0), CREG(0), MREG(0), ADREG(0), DREG(0), INMODEREG(0), ALUMODEREG(0), OPMODEREG(0), CARRYINREG(0), CARRYINSELREG(0)`) so the control signals still take effect immediately (they are driven by real `CLK` edges here, so bypassing them is a latency choice, not a correctness requirement — but keeping them consistent with Template A avoids ever having to remember which template needs which subset).
- `OPMODE(7'b0100101)` configures the feedback loop: Z multiplexer selects the registered output `P` to feed back, and X/Y multiplexers select the multiplier output (`P + A * B`).
- `CARRYINSEL(3'b000)` + `CARRYIN(1'b0)` tied explicitly, same reasoning as Template A — never leave `CARRYIN` floating once `CARRYINREG` is bypassed.
- Use clock enables (`CEP`) and synchronous resets (`RSTP`) to manage the accumulation state.
- **Critical "double-registration" rule**: `PREG(1)` already makes `P` (and therefore `accum_wide`) a **registered** signal — it updates on the same clock edge that latches the new accumulated value, one cycle after the inputs that produced it. The module's output port (e.g. `acc`) **must expose `accum_wide` combinationally** (e.g. `always @(*) acc = accum_wide[19:0];`, which still satisfies an `output reg` port declaration because the assignment is procedural, not because it is clocked). If you instead re-register `accum_wide` into `acc` with a **second** `always @(posedge clk)` block, you add a **second** cycle of latency on top of the DSP's own `PREG`, and every accumulated value will read back exactly one cycle late (e.g. testbench shows `acc` equal to the *previous* cycle's expected value, forever trailing by one) — a very recognizable symptom of accidental double pipelining.

```verilog
// 8x8 Unsigned Registered Multiply-Accumulator (P <= P + A x B)
// Zero-extend/Sign-extend operands to native sizes (a to 30 bits, b to 18 bits)
wire [29:0] a_ext = {22'b0, a}; // 8 bits zero-extended to 30
wire [17:0] b_ext = {10'b0, b}; // 8 bits zero-extended to 18
wire [47:0] accum_wide;

DSP48E1 #(
  .USE_MULT("MULTIPLY"),
  .USE_DPORT("FALSE"),
  .USE_PATTERN_DETECT("NO_PATDET"),
  .A_INPUT("DIRECT"),
  .B_INPUT("DIRECT"),
  .AREG(0), .ACASCREG(0),   // MUST match: both 0
  .BREG(0), .BCASCREG(0),   // MUST match: both 0
  .CREG(0),
  .MREG(0),
  .PREG(1),           // MUST be 1: enable output register P to hold feedback / accumulated value
  .ADREG(0),
  .DREG(0),
  .INMODEREG(0),
  .ALUMODEREG(0),
  .OPMODEREG(0),      // Easy to forget: without this, OPMODE never takes effect and P reads as 0
  .CARRYINREG(0),
  .CARRYINSELREG(0)
) dsp_mac_inst (
  .A(a_ext),          // 30-bit zero-extended operand A
  .B(b_ext),          // 18-bit zero-extended operand B
  .C(48'b0),          // Unused post-adder input C; tie to 0
  .D(25'b0),          // Unused pre-adder input D (USE_DPORT=FALSE); tie to 0
  .CARRYIN(1'b0),     // MUST tie explicitly (see Template A rationale)
  .P(accum_wide),     // 48-bit running sum output; slice to expose (e.g., accum_wide[19:0])
  .CLK(clk),          // Connect to the system clock
  .CEA1(1'b0), .CEA2(1'b0), .CEB1(1'b0), .CEB2(1'b0), .CEC(1'b0),
  .CEAD(1'b0), .CED(1'b0), .CEM(1'b0),
  .CEP(accum_en | rst), // Robust clock enable: must be active when accumulating OR resetting
  .CEALUMODE(1'b0), .CECTRL(1'b0), .CEINMODE(1'b0), .CECARRYIN(1'b0),
  .RSTA(1'b0), .RSTB(1'b0), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
  .RSTP(rst),         // Synchronously reset output register P to 48'b0 on rising edge of CLK when rst is high
  .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0), .RSTCTRL(1'b0), .RSTINMODE(1'b0),
  .ALUMODE(4'b0000),  // 4'b0000: Addition
  .OPMODE(7'b0100101), // 7'b0100101: Accumulate (Z mux = P output register, Y/X mux = Multiplier product)
  .INMODE(5'b00000),
  .CARRYINSEL(3'b000), // Selects CARRYIN (tied 0) as the adder carry-in
  .ACOUT(), .BCOUT(), .CARRYCASCOUT(), .CARRYOUT(), .MULTSIGNOUT(),
  .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .PCOUT(), .UNDERFLOW()
);

// DO NOT re-register accum_wide here (that would add a SECOND cycle of
// latency on top of the DSP's own PREG, making every result read one
// cycle late). accum_wide is already clocked by the DSP internally, so
// expose it combinationally:
always @(*) begin
  acc = accum_wide[19:0];
end
```

Because of DSP48E1's port count and parameter complexity, prefer these template configurations carefully when explicit instantiation is required; otherwise let synthesis infer the DSP from `a * b` / `acc <= acc + a * b` style RTL (see Section 4).

### 10.3 MUXF7 / MUXF8 (structural wide multiplexer)

```verilog
// 8:1 mux built from two 4:1 halves plus a MUXF7 combining them on sel[2]
MUXF7 muxf7_inst (
  .O(data_out),
  .I0(mux_lower_4to1),  // result for sel[1:0] with sel[2]=0
  .I1(mux_upper_4to1),  // result for sel[1:0] with sel[2]=1
  .S(sel[2])
);
```

For a 16:1 mux, combine two `MUXF7` outputs with one `MUXF8` selecting on the
top select bit (`S(sel[3])`).

### 10.4 SRLC32E (structural 32-bit shift-register LUT)

```verilog
SRLC32E srlc32e_inst (
  .Q(tap_out),        // dynamically-addressed tap output
  .Q31(serial_out),    // fixed 32-cycle-delayed output
  .A(5'd31),           // tap address (5'd31 = full 32-stage delay)
  .CE(shift_en),
  .CLK(clk),
  .D(serial_in)
);
```

## 11. Anti-Patterns Specific to Structural Instantiation

- Leaving `DSP48E1` control ports (e.g. `OPMODE`, `ALUMODE`, `CARRYINSEL`) in
  an inconsistent combination for the desired operation; when unsure, mirror
  the exact template above rather than guessing new port values.
- Setting `AREG`/`BREG` on `DSP48E1` without also setting the matching
  `ACASCREG`/`BCASCREG` to the same value — this passes synthesis but fails
  at simulation elaboration with an `Attribute Syntax Error` that calls
  `$finish` immediately, making every output read as 0/X for the whole run.
- Tying `DSP48E1`'s `CLK` to a constant (no clock) while leaving any of the
  eleven register-enable parameters (including the easy-to-forget
  `OPMODEREG`/`CREG`) at their default `1` — this passes both synthesis AND
  simulation elaboration with no errors, but the control-path registers
  never latch the real port values (no clock edge ever occurs), so `P`
  silently reads as a constant 0 for every input. ALL eleven register-enable
  parameters must be bypassed (`0`) together whenever `CLK` is tied off
  (`PREG` stays `1` only in Template B's registered-output case) — see
  Template A/B for the complete list.
- Bypassing `CARRYINREG`/`CARRYINSELREG` (setting them to `0`) without also
  tying `.CARRYIN(1'b0)` explicitly — once bypassed, `CARRYIN` becomes a
  live combinational input to the adder; leaving it unconnected makes it
  read as `X` in simulation, and the adder's XOR-based sum turns the
  **entire** `P` output into `X` even though `A`/`B`/`C` are all valid
  known values. Always wire every DSP48E1 input port explicitly, even ones
  that "shouldn't" affect the selected operation — see Template A/B, which
  tie off every input with no exceptions.
- Re-registering `DSP48E1`'s `P` output (from Template B, where `PREG(1)`
  already makes `P` a registered signal) into a **second** clocked register
  before exposing it as the module's output — this adds an extra cycle of
  latency on top of the DSP's own output register, so every accumulated
  result reads back exactly one clock cycle late compared to what the
  testbench expects (a "trailing by one cycle" symptom). Expose `P`
  (or a slice of it) **combinationally** at the module boundary (e.g.
  `always @(*) acc = accum_wide[19:0];`) instead of with another
  `always @(posedge clk)` block — see Template B's closing lines.
- Chaining `CARRY4` instances with the wrong `CI`/`CO` bit order (`CO[3]` of
  slice *i* must feed `CI` of slice *i+1`, not the reverse).
- Instantiating `SRLC32E`/`SRL16E` with `A` (the tap address) tied to
  anything other than the fixed required delay when only the final tap is
  needed — use the widest fixed-delay output (`Q31`/`Q15`) instead of the
  dynamically-addressed `Q` output when the address never changes.


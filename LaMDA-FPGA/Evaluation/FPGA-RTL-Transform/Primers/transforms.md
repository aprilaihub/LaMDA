<!--
family: Xilinx 7-Series (Artix-7, Kintex-7, Virtex-7, Zynq-7000)
module: transforms
references: UG901 (Vivado Synthesis: attributes, RAM / SRL / DSP inference), UG479 (DSP48E1), UG474 (CLB, LUT RAM, SRL), UG473 (Memory Resources), UG949 (UltraFast Design Methodology: fanout, control sets, pipelining)
scope: generic RTL patterns only (no design-specific advice); used with --knowledge primers+patterns
-->
## RTL transformation patterns for Vivado on 7-Series

A catalogue of generic edits, one entry per label of the answer format (`// Start Transformations`). Each entry says what the
edit is, how it is written in Verilog, what it normally changes in the reports, and what can go wrong. Whether an edit pays
off depends on the design: check the effect in the measured result of every attempt.

### 0. Rules that apply to every edit
- **Count registers per path.** The latency at every output is the number of clocked register crossings from each relevant input.
  An edit that adds or removes a register on such a path changes the behavior (a testbench compares every cycle), even if the design gets
  smaller or faster. Moving logic across a register is allowed only when the count on every path stays the same.
- **Attributes steer, registers define latency.** A synthesis attribute never adds or removes a pipeline stage; it only changes how the
  existing registers and operators are mapped.
- **Reset and initial values.** Moving or removing a register changes what it holds after reset. Keep the reset behavior visible at the ports.
- **One or two edits per attempt.** The measured result is for the whole design: with several edits at once, the effect of each is unknown.
- **Check the report.** Vivado may already do what the edit does (for example infer an SRL or replicate a register); then the edit shows no change.

### MEM_TO_BRAM
- *Edit:* a memory (array of regs) with one clocked write and a **registered read** (`dout <= mem[addr];` in a clocked block) is mapped to a RAMB18E1 / RAMB36E1. `(* ram_style = "block" *)` on the array forces it.
- *Typical effect:* LUT and FF down (the storage and the read multiplexer leave the fabric), BRAM up. A block RAM has a fixed minimum size (a RAMB18 holds 18 Kb), so it pays only for memories that are not tiny.
- *Watch:* a combinational read (`assign dout = mem[addr];`) cannot be a block RAM. Registering the read adds one cycle at that output, so it is valid only when the reference already registers the read data (then that register is absorbed) or the surrounding schedule is unchanged. A block RAM has no reset of its content.
  Reading and writing the same address in one cycle returns old or new data depending on the coding style; keep the same behavior as the reference.

### MEM_TO_LUTRAM
- *Edit:* a memory with a clocked write and a **combinational read** maps to distributed RAM (RAM32M, RAM64M, RAM32X1S, RAM64X1S, ...). `(* ram_style = "distributed" *)` forces it; `(* ram_style = "registers" *)` forces flip-flops.
- *Typical effect:* FF down (storage bits live in LUTs), LUT up by about one LUT per 64 entries per data bit (a 64-deep, 8-bit memory is about 8 LUTs), BRAM down. Reads are asynchronous, so no latency is added.
- *Watch:* the array cannot be reset or have its content cleared in one cycle; every additional read port costs another copy of the memory.

### SRL_INFERENCE
- *Edit:* a pure delay line (`sr <= {sr[N-2:0], din};`, no reset, no enable on the taps other than a plain clock enable, only the last stage or a selected tap is read) maps to SRL16E / SRLC32E: up to 32 stages in one LUT.
  `(* shreg_extract = "no" *)` prevents it; `(* srl_style = "srl" *)` requests it (other values: `register`, `srl_reg`, `reg_srl`, `reg_srl_reg`).
- *Typical effect:* FF down by about N-1 per bit, LUT up by about N/32 per bit. The delay in cycles is unchanged.
- *Watch:* any reset on the chain, or a read of an intermediate stage that is not an addressable tap, prevents the SRL. A SRL output is slower than a flip-flop output; one extra register after the SRL is sometimes added by the tool.

### MULT_TO_DSP
- *Edit:* `(* use_dsp = "yes" *)` on the module or on the result register, or restructure so that Vivado recognises a multiply / multiply-add / multiply-accumulate with registers around it.
- *Typical effect:* LUT (and CARRY4) down, DSP48E1 up; with the surrounding registers absorbed (see DSP_ABSORB_PIPE_REGS), FF down and the Fmax of the slice is high.
- *Watch:* small operands and unregistered multiplies are frequently left in LUTs by the tool. Operands wider than 25 x 18 use several slices plus fabric adders.

### MULT_TO_LOGIC
- *Edit:* `(* use_dsp = "no" *)` (or `"logic"`) keeps the multiplication in LUT / CARRY4.
- *Typical effect:* DSP48E1 down, LUT up, usually lower Fmax than a fully registered DSP. Useful only when the DSP count is what must go down.

### DSP_ABSORB_PIPE_REGS
- *Edit:* write the registers directly on the operands, on the product and on the result of a multiply, with **synchronous** reset and a plain clock enable, and with no other consumer of the intermediate registers. Vivado then maps them to `AREG/BREG`, `MREG`, `PREG` of the DSP48E1.
- *Typical effect:* FF down, DSP48E1 unchanged, shorter paths (WNS up). The reports (`DSP Final Report` in the synthesis log) show the number of absorbed stages per register column.
- *Watch:* only registers that already exist are absorbed. Adding one to reach the full pipeline adds a cycle. An asynchronous reset or logic between the register and the DSP prevents the absorption.

### DSP_USE_PREADDER
- *Edit:* `(d_r + a_r) * b_r` with registered `d_r`, `a_r` is mapped to the pre-adder of the DSP48E1 (25 bit).
- *Typical effect:* LUT down (the adder leaves the fabric), DSP48E1 unchanged. *Watch:* the operands must be registered and no wider than the pre-adder.

### DSP_CASCADE
- *Edit:* in a chain of multiply-adds, the sum of the previous stage is the registered result of the previous slice added in the next one (`p2 <= p1 + a*b;` with `p1` the registered `P` of the previous stage).
- *Typical effect:* LUT down (the fabric adder is replaced by the `PCOUT -> PCIN` cascade), shorter paths. *Watch:* the slices must sit in the same column; the chain order must match the cascade direction.

### FANOUT_REPLICATION
- *Edit:* a register that drives many loads is duplicated; each copy drives a subset. In RTL: `(* max_fanout = N *)` on the register lets synthesis replicate it; written copies need `(* keep = "true" *)` or `(* dont_touch = "true" *)` or the tool merges them back as equivalent.
- *Typical effect:* FF up (one per copy), WNS up when the high-fanout net is on the critical path. *Watch:* implementation (`phys_opt_design`) may already replicate; the implementation log then lists `Replicated N times`.

### LOGIC_RETIME
- *Edit:* move combinational logic from one side of a register to the other so that the logic depth between registers is balanced (for example compute part of a sum before the register instead of after it).
- *Typical effect:* WNS up (the worst path is shorter), LUT and FF about the same, sometimes FF up (more bits cross the register). *Watch:* the register count on every path from the inputs to every output must stay the same, and the reset value of the moved register must still produce the same visible behavior.

### LOGIC_SIMPLIFICATION
- *Edit:* rewrite an expression, comparator, multiplexer or controller with less logic: drop bits that cannot change an output, share a sub-expression used twice, replace a comparator of a counter with a terminal-count flag, simplify a state encoding (`(* fsm_encoding = "one_hot" *)` or `"sequential"`), replace a multiplexer chain by an indexed select.
- *Typical effect:* LUT down, sometimes shorter paths. *Watch:* the behavior at the ports must be bit-exact, including truncation, signedness and widths.

### RESOURCE_SHARING
- *Edit:* one arithmetic unit or one memory port serves several operations in different cycles (time multiplexing) instead of one per operation.
- *Typical effect:* DSP48E1 or LUT down, a small multiplexer and control added. *Watch:* the throughput and the cycle at which each result appears must be unchanged, so it applies only where the reference has idle cycles on that unit.

### CONTROL_SET_REDUCTION
- *Edit:* use the same clock enable and the same synchronous reset for registers that are on the same logical path; remove a reset from registers that are always written before they are read and are not visible at the ports; turn an asynchronous reset into a synchronous one.
- *Typical effect:* fewer distinct control sets (see the control-set report), better packing, and it can enable SRL, DSP or RAM inference that a reset or enable had blocked. LUT / FF counts change little.
- *Watch:* the reset behavior visible at the ports must stay the same.

### OTHER
Anything else; describe it in the `rationale` field.

### Synthesis attributes (UG901)

| Attribute | Values | Effect |
|-----------|--------|--------|
| `use_dsp` | `yes`, `no` / `logic`, `simd` | multiplier / adder in DSP48E1 or in fabric |
| `ram_style` | `block`, `distributed`, `registers` | where an array is stored |
| `rom_style` | `block`, `distributed` | same, for read-only arrays |
| `shreg_extract` | `yes`, `no` | allow or prevent SRL inference |
| `srl_style` | `register`, `srl`, `srl_reg`, `reg_srl`, `reg_srl_reg` | how a shift register is built |
| `max_fanout` | integer | replicate a register above this fanout |
| `keep`, `dont_touch` | `true` | stop the tool from removing or merging the signal / cell |
| `fsm_encoding` | `auto`, `one_hot`, `sequential`, `gray`, `johnson`, `none` | state encoding of a recognised FSM |
| `keep_hierarchy` | `yes`, `no` | keep a module boundary (blocks cross-boundary optimisation) |
| `extract_enable`, `extract_reset` | `yes`, `no` | let the tool use the register enable / reset pins |

<!--
family: Xilinx 7-Series (Artix-7, Kintex-7, Virtex-7, Zynq-7000)
module: reports
references: UG901 (Vivado Synthesis), UG904 (Implementation), UG906 (Design Analysis and Closure Techniques), UG835 (Tcl command reference)
scope: observations only, no advice; message ids marked (*) were seen in Vivado 2024.1 logs; used with --knowledge primers+patterns
-->
## How to read the Vivado results

What the numbers and messages in the feedback mean, and what each stage can and cannot tell. Observations only: nothing here says what to change.

### 1. Synthesis versus implementation results
- The stage of a result is stated in the baseline text. A **synthesis** result (`--stop_stage synth`) has estimated timing: nets have no placement,
  so the net delays are estimates and the real Fmax is usually lower. Use it to compare designs against each other, not as signoff.
- An **implementation** result (placed and routed) has real routing delays. `opt_design`, placement, physical optimization and routing also change the
  netlist: logic can be removed, a high-fanout register can be replicated, DSP / block RAM registers can be moved. LUT and FF counts can therefore differ a little from synthesis.
- A change that only helps in implementation (placement, replication done by the tool) is invisible in a synthesis-only run.

### 2. Metrics
- `LUT_USED`, `FF_TOTAL`, `DSP48E1`, `BRAM` (RAMB36 equivalents; RAMB18 = 0.5): counted after optimization. Logic that is removed costs nothing.
- **WNS** (worst negative slack): clock period minus the delay of the worst register-to-register path; negative means the clock is too fast. **TNS** is the sum of the negative slacks of the endpoints.
  **WHS / THS** are the same for hold. Fmax is about 1 / (period - WNS).
- Logic levels: the number of LUT / CARRY stages on a path. Net delay is often the larger part of a path in a small design; the digest gives the split.

### 3. Synthesis log messages
| Id | Meaning |
|----|---------|
| Synth 8-6014 (*) | An unused sequential element was removed: a register that drives nothing visible (the text names it and the source line). |
| Synth 8-7129 (*) | A port is unconnected or has no load in a module. |
| Synth 8-5584 (*) | A memory is implemented as distributed (LUT) RAM; the text gives the reason. |
| Synth 8-7052 (*) | A block RAM output register could not be merged into the RAM; the read path is longer. |
| Synth 8-3332 | A sequential element is unused and is removed from a module. |
| Synth 8-3936 | A register was trimmed from N to M bits because the upper bits are unused. |
| Synth 8-3917 | A port is driven by a constant. |
| Synth 8-4471 | Two registers were merged (equivalent registers). |
| Synth 8-5544 / 8-5546 | A ROM is not mapped to block RAM / RAM (too shallow). |
| Synth 8-6157 / 8-6155 (*) | Start / end of the synthesis of a module (housekeeping). |

### 4. Implementation log messages
| Id | Meaning |
|----|---------|
| Opt 31-389 (*) | Per phase of `opt_design`: cells created and removed (retarget, constant propagation, sweep, BUFG, shift register, post-processing). All zero means `opt_design` found nothing to change. |
| Opt 31-1064 (*) | SRL remap: shift registers converted to registers and register chains to SRLs. |
| Physopt 32-81 (*) | A net was replicated N times by physical synthesis in the placer (high fanout). |
| Physopt 32-775 (*) | Result of one phys_opt pass: cells created, deleted, moved. |
| Physopt 32-619 (*) | Estimated timing summary (WNS, TNS) inside the placer. |
| Place 30-746 (*) | Post-placement timing estimate (WNS). |
| Place 30-612 (*) | Estimated congestion table per direction (region size; larger = more congested). |
| Route 35-416 (*) | Intermediate timing summary of the router (WNS, TNS, WHS, THS); the sequence shows how routing moved timing. |
| Route 35-57 (*) | Final timing estimate of the router. |
| Route 35-328 (*) | Router estimated timing not met (WARNING). |
| Route 35-16 (*) | Router completed successfully (every net routed; says nothing about timing). |

### 5. `report_design_analysis` and the path notation
- *Logical Path* `FDRE/C-(18)-LUT6-(2)-LUT6-(1)-CARRY4-...-FDRE/D`: the cells of the worst path from the start register to the end register; the number in parentheses is the fanout of the net after that cell.
- *Logic Delay / Net Delay* split (in ns and %): which part of the path is cells and which is routing.
- *Logic level distribution*: how many of the worst ~1000 endpoints have 0, 1, 2, ... logic levels. A tail of many endpoints at the highest levels means the limit is the whole design, not one path.
- *Congested windows above level 5*: none means placement and routing congestion are not the limit.

### 6. Resources in the utilization report
- `Slice LUTs` include `LUT as Logic` and `LUT as Memory` (distributed RAM and SRL); `Slice Registers` are the flip-flops.
- `Block RAM Tile` (RAMB36 count, RAMB18 as 0.5), `DSPs` (DSP48E1), `CARRY4` in the primitive table.
- *Control sets*: the distinct (clock, set / reset, enable) combinations of the registers. Many control sets prevent registers from sharing a slice.

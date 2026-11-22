<!--
family: Xilinx 7-Series (Artix-7, Kintex-7, Virtex-7, Zynq-7000)
module: bram
references: UG473 (7 Series FPGAs Memory Resources), UG474 (7 Series CLB, distributed RAM), UG901 (Vivado Synthesis, "RAM HDL Coding Techniques", ram_style, rw_addr_collision), UG953 (7 Series Libraries Guide, RAMB18E1/RAMB36E1/RAM64X1S/RAM64M/RAM32M)
scope: resource reference only (no design-specific or task rules)
-->
## Memories (7-Series): block RAM, distributed RAM and registers

Reference for the three ways Vivado can implement an array of words: block RAM (`RAMB18E1` / `RAMB36E1`), distributed (LUT) RAM, and flip-flops. They differ in read timing, port count and cost.

### 1. Overview

| Property | Registers (flip-flops) | Distributed RAM (LUT RAM) | Block RAM |
|----------|------------------------|---------------------------|-----------|
| Primitive | `FDRE`, `FDCE`, ... | `RAM32M`, `RAM64X1S`, `RAM64M`, `RAM64X1D`, `RAM128X1S`, `RAM256X1S`, ... | `RAMB18E1`, `RAMB36E1` |
| Write | Synchronous, any number of independent write enables | Synchronous, one write port per LUT RAM | Synchronous, up to two ports |
| Read | Combinational; any number of words read in the same cycle | Combinational (asynchronous) read; a registered read is an optional extra flip-flop | Synchronous: data appears one cycle after the address is sampled (two with the optional output register) |
| Ports | Unlimited | One read port per RAM primitive; extra read ports duplicate the LUTs (`RAM64X1D` has two) | Two ports (A and B); simple dual-port or true dual-port |
| Granularity | 1 bit | 1 LUT = 32 or 64 bits (depth 32 / 64 x 1) | 18 Kb (RAMB18E1) or 36 Kb (RAMB36E1) |
| Location | Slice flip-flops | `SLICEM` LUTs only | Dedicated block RAM columns |
| Reset of contents | Possible | Not possible | Not possible (only the output latch/register can be reset) |

- A very small memory fills a tiny part of a 18 Kb block: block RAM is a coarse resource, and its use makes sense only for memories that are big enough. A small memory is cheaper in LUT RAM or registers.
- A memory that needs several words in the same cycle cannot use a block RAM port for them; it needs registers or replicated LUT RAM.

### 2. Block RAM (UG473)
- `RAMB36E1` = 36 Kb (can be used as two `RAMB18E1`, 18 Kb each). Each 9th bit of the width is a parity bit that can also be used as data.
- Aspect ratios (depth x width, width includes the parity bit):
  - `RAMB18E1`: 16K x 1, 8K x 2, 4K x 4, 2K x 9, 1K x 18, 512 x 36 (simple dual-port only for 36 bit).
  - `RAMB36E1`: 32K x 1, 16K x 2, 8K x 4, 4K x 9, 2K x 18, 1K x 36, 512 x 72 (simple dual-port only for 72 bit).
- Larger memories are built from several blocks, wider words by blocks in parallel, deeper memories by blocks in series (with output multiplexing).
- Port modes: simple dual-port (one write port, one read port, different widths possible) and true dual-port (two independent ports, each read/write, optionally different clocks).
- Read-during-write on the same port (`WRITE_MODE_A` / `WRITE_MODE_B`):
  - `WRITE_FIRST` (default): the output shows the data being written.
  - `READ_FIRST`: the output shows the old content of the address, the write happens after the read.
  - `NO_CHANGE`: the output keeps its previous value during a write.
- Two ports accessing the same address in the same cycle is a collision; the result of a read colliding with a write on the other port is not guaranteed in all configurations (for the synchronous-clock case the read data is undefined; `rw_addr_collision` lets synthesis add logic to avoid it).
- Optional output register `DOA_REG` / `DOB_REG` (0 or 1): adds one more cycle of read latency and shortens the clock-to-output delay, which is the usual way to reach high frequencies with block RAM.
- Enables and resets: `ENA/ENB` (port enable), `REGCEA/REGCEB` (output register enable), `RSTRAMA/RSTRAMB` and `RSTREGA/RSTREGB` (synchronous reset of the output latch / register), `WEA/WEB` (byte-wide write enables; 4 bits for a 36 bit port, 1 bit per byte).
- Contents can be initialized (`INIT_xx`, `INITP_xx`), which the tool derives from an `initial` block or `$readmemh` / `$readmemb`. A reset of the whole array is not available.
- Latency of the read path: the address sampled at edge t gives the data after edge t (visible to logic in cycle t+1) without the output register, after edge t+1 with it. A write at edge t is visible to a read of the same address sampled at edge t+1 and later; the same-cycle behavior is defined by the write mode.

### 3. Distributed RAM (UG474)
- Built from `SLICEM` LUTs. One LUT6 holds 64 x 1 bit (`RAM64X1S`); 32 x 1 per half-LUT (`RAM32X1S`); `RAM32M` / `RAM64M` combine four LUTs for 6 / 4 bit words; `RAM128X1S` / `RAM256X1S` use 2 / 4 LUTs; the `D` variants (`RAM64X1D`, ...) have a second, independent read port.
- Write is synchronous (clock and write enable), read is asynchronous: the output changes combinationally with the read address. A registered output is built with a normal flip-flop after the LUT.
- Fast access to small tables, no extra latency, but the read address adds to the combinational path. Cost is about `width x ceil(depth / 64)` LUTs for single-port memories.
- The utilization report lists these LUTs under "LUT as Memory > LUT as Distributed RAM", separately from "LUT as Logic".

### 4. Registers
- A memory written to and read from through individual flip-flops: one flip-flop per bit, free read ports, any write pattern, any reset. The cost is the number of bits, plus a multiplexer in LUTs if the words are selected by an address.
- A memory is implemented in registers when the access pattern cannot be mapped to a RAM primitive (see section 6) or when `ram_style = "registers"` is requested.

### 5. Inference from behavioral RTL (UG901)
Declare the array and access it in clocked blocks.

```
reg [W-1:0] mem [0:DEPTH-1];

// block RAM, read-first (old data on a write to the same address), synchronous read
always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
    rdata <= mem[raddr];
end

// block RAM, write-first
always @(posedge clk) begin
    if (we) begin mem[addr] <= wdata; rdata <= wdata; end
    else        rdata <= mem[addr];
end

// block RAM, no-change
always @(posedge clk) begin
    if (we) mem[addr] <= wdata;
    else    rdata <= mem[addr];
end

// distributed RAM: asynchronous read
always @(posedge clk) if (we) mem[waddr] <= wdata;
assign rdata = mem[raddr];

// byte-wide write enable (block RAM)
always @(posedge clk) begin
    if (we[0]) mem[addr][ 7:0] <= wdata[ 7:0];
    if (we[1]) mem[addr][15:8] <= wdata[15:8];
end
```
- Block RAM needs a synchronous read: the read data (or the read address) must be registered inside the same clocked block. An asynchronous read leads to distributed RAM or registers.
- A register placed directly on the registered read data (`rdata2 <= rdata;`, no logic in between) can be absorbed into the optional output register `DOA_REG` / `DOB_REG`; the read latency in RTL stages is unchanged by the absorption.
- A synchronous reset or enable on the read-data register maps to `RSTRAM`/`RSTREG` and `ENA`/`REGCE`. An asynchronous reset on it prevents block RAM.
- True dual-port: two clocked blocks (or one block with two ports) that each access the array. Per port, at most one write and one read address.
- Initial content: `initial begin $readmemh("file.mem", mem); end` or assignments in an `initial` block.
- ROM: an array that is only read, usually initialized; `(* rom_style = "block" *)` or `"distributed"`.

### 6. Why an array is not inferred as RAM
- The whole array is reset in a loop (RAM cannot be reset), or contents are changed by an unrelated statement.
- Several words are read in the same cycle (more reads per cycle than RAM ports): all words are needed in parallel, which results in registers (and a wide multiplexer) instead of RAM.
- The array is read asynchronously and is too small or too wide to be worthwhile for LUT RAM.
- Individual words are held in separately named registers (`r0`, `r1`, ...) or accessed with constant indexes: they are plain registers; the tool does not merge them into one array.
- More write ports than the RAM primitive supports, or write ports in different clock domains.
- The array is written from several always blocks with conflicting control.
- Read data is used before the register (logic between the RAM output and the first flip-flop), or the read register has an asynchronous reset.

### 7. Controlling the choice (UG901)
- `(* ram_style = "block" *)`, `"distributed"`, `"registers"`, `"mixed"` (block RAM plus LUT RAM for the remainder), `"auto"` (tool decides). The attribute goes on the array declaration.
- `(* rom_style = "block" *)` / `"distributed"` for read-only arrays.
- `(* rw_addr_collision = "yes" | "no" | "auto" *)` controls whether Vivado adds logic to resolve a same-address read/write collision on a synchronous RAM.
- Without an attribute the tool chooses block RAM, distributed RAM or registers from the size, the number of ports, and whether the read is synchronous.

### 8. How memories show up in the Vivado reports
- Utilization report, section "3. Memory": `Block RAM Tile`, `RAMB36/FIFO*`, `RAMB18` (for example 135 tiles / 270 RAMB18 on `xc7a100t`). Section "Slice Logic": `LUT as Memory` (19000 available on `xc7a100t`), split in `LUT as Distributed RAM` and `LUT as Shift Register`.
- Primitives table: `RAMB18E1` / `RAMB36E1` for block RAM, `RAM32M`, `RAM64X1S`, `RAM64M`, ... for distributed RAM, `FDRE`/`FDCE` for registers.
- All zeros in "Memory" and "LUT as Memory" with many flip-flops means that the array was implemented in registers.
- Synthesis log: the inferred RAMs are listed with their depth x width and the number of `RAMB18` / `RAMB36` used; a memory implemented in registers is reported as such.
- Timing report: a path that starts at a `RAMB` clock pin shows the block RAM clock-to-output delay; a long path through a read multiplexer of register words is a sign of a register-based array.

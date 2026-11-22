"""
Compressed digest of the Vivado synthesis / implementation reports, for the LLM feedback
(--feedback_detail reports).

The loop always gives the LLM the performance metrics (LUT, FF, DSP, BRAM, Fmax). The digest adds WHERE they
come from, in a few lines:

  * hierarchy   what every module of the design costs (report_utilization -hierarchical), instances of the
                same module summed up, so 16 processing elements are one line
  * memories    which arrays became block RAM / LUT RAM / ROM / SRL
  * DSP         which DSP48E1 operating mode and which internal registers (AREG, MREG, PREG) were used
  * timing      the worst path (source, destination, logic levels, logic vs. route delay), the highest fanout
                nets, the number of control sets
  * removed     registers that synthesis removed, merged or turned into constants
  * changes     for every number, the value in the reference or in the best attempt so far

The digest states OBSERVATIONS only. It never suggests a transformation: choosing the RTL transformation is
the job of the LLM (with or without the primers, see --knowledge).

Two steps keep it testable without Vivado:
  build_digest(...)   parsed reports  -> small JSON-able dict (stored in fabric_report.json)
  format_digest(...)  dict (+ optional dict to compare with) -> text of at most `max_chars` characters
"""

import re

# Columns of the 'Utilization by Hierarchy' table -> keys of a hierarchy row.
_HIER_COLUMNS = {
    "Total LUTs": "luts", "LUTRAMs": "lutram", "SRLs": "srl", "FFs": "ffs",
    "RAMB36": "ramb36", "RAMB18": "ramb18", "DSP Blocks": "dsp",
}
_AREA_KEYS = ("luts", "ffs", "ramb36", "ramb18", "dsp", "lutram", "srl")
_AREA_LABELS = (("luts", "LUT"), ("ffs", "FF"), ("lutram", "LUTRAM"), ("srl", "SRL"),
                ("ramb36", "RAMB36"), ("ramb18", "RAMB18"), ("dsp", "DSP"))

DEFAULT_MAX_CHARS = 3000


# ---------------------------------------------------------------------- parsing

def _number(text):
    """Table cell -> int (or float when fractional); 0 when empty or not numeric."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return 0
    return int(value) if value == int(value) else value


def parse_hier_utilization(text):
    """
    Rows of the 'Utilization by Hierarchy' table of `report_utilization -hierarchical`:

        | Instance     | Module | Total LUTs | Logic LUTs | LUTRAMs | SRLs | FFs | RAMB36 | RAMB18 | DSP Blocks |
        | top          | (top)  |        310 |        310 |       0 |    0 | 120 |      2 |      0 |          0 |
        |   (top)      | (top)  |         40 |         40 | ...                       (own logic of the top)
        |   abuf       | os_buf |          3 |          3 | ...
        |   \\gen[0].pe | os_pe  | ...

    Returns [{"depth", "instance", "module", "luts", "lutram", "srl", "ffs", "ramb36", "ramb18", "dsp"}]; the
    depth is the indentation of the instance name (0 = top). The columns are found by their header names, so a
    different column order (or an extra column) of another Vivado version does not matter. [] when the table
    is absent.
    """
    lines = (text or "").splitlines()
    header, rows = None, []
    for line in lines:
        if not line.strip().startswith("|"):
            if header is not None and rows:
                break
            continue
        raw_cells = line.strip().strip("|").split("|")
        cells = [c.strip() for c in raw_cells]
        if header is None:
            if "Instance" in cells and "Module" in cells:
                header = cells
            continue
        if len(cells) != len(header):
            continue
        indent = len(raw_cells[0]) - len(raw_cells[0].lstrip(" "))
        row = {"depth": max(0, (indent - 1) // 2), "instance": cells[0], "module": cells[header.index("Module")]}
        for column, key in _HIER_COLUMNS.items():
            row[key] = _number(cells[header.index(column)]) if column in header else 0
        rows.append(row)
    return rows


def summarize_hierarchy(rows):
    """
    Hierarchy rows -> one entry per module under the top, instances of the same module summed up:
        {"top": {area of the whole design},
         "modules": [{"module", "instances", <area sums>}]}      (largest LUT count first)
    The logic of the top module itself (not inside a child) is listed as module "(top logic)".
    Only the direct children of the top are used: a deeper level is already inside its parent's totals.
    """
    if not rows:
        return {}
    top = {k: rows[0].get(k, 0) for k in _AREA_KEYS}
    children = [r for r in rows[1:] if r["depth"] == 1]
    names = {r["module"] for r in children}
    grouped = {}
    for row in children:
        # Vivado names the copies of a module os_pe, os_pe_1, os_pe_2 ... (and os_delay__parameterized0, ...):
        # count them as one module.
        base = re.sub(r"(__parameterized\d+|_\d+)+$", "", row["module"])
        module = base if base in names else row["module"]
        name = "(top logic)" if row["instance"].startswith("(") else module
        entry = grouped.setdefault(name, {"module": name, "instances": 0, **{k: 0 for k in _AREA_KEYS}})
        entry["instances"] += 1
        for key in _AREA_KEYS:
            entry[key] += row.get(key, 0)
    modules = sorted(grouped.values(), key=lambda m: (-m["luts"], -m["ffs"], m["module"]))
    return {"top": top, "modules": modules}


def build_digest(hier_text=None, facts=None, dsp_mapping=None, timing_paths=None, high_fanout=None,
                 control_sets=None, wns_ns=None):
    """
    Parsed reports -> digest dict. Every argument is optional; a missing report only leaves its part out.

      hier_text     text of reports/<stage>/hier_utilization.txt
      facts         MappingAnalyzer.analyze() result of the synthesis log (memories, removed registers)
      dsp_mapping   FabricReportParser.parse_dsp_mapping_table() rows of the synthesis log
      timing_paths  FabricReportParser.parse_timing_paths() of the stage (worst path first)
      high_fanout   FabricReportParser.parse_high_fanout_nets()
      control_sets  FabricReportParser.parse_control_sets()
      wns_ns        worst register-to-register slack; None when no such path exists
    """
    facts = facts or {}
    digest = {"hierarchy": summarize_hierarchy(parse_hier_utilization(hier_text))}

    memories = facts.get("memories") or {}
    digest["memories"] = (
        [{"kind": "block RAM", "object": r.get("object"), "shape": r.get("port_a"),
          "ramb18": r.get("ramb18"), "ramb36": r.get("ramb36")} for r in memories.get("block_ram") or []]
        + [{"kind": "ROM", "object": r.get("object"), "shape": r.get("depth_x_width"),
            "implemented_as": r.get("implemented_as")} for r in memories.get("rom") or []]
        + [{"kind": "shift register", "object": r.get("name"), "shape": f"{r.get('length')}x{r.get('width')}",
            "srl16e": r.get("srl16e"), "srlc32e": r.get("srlc32e")} for r in memories.get("shift_registers") or []]
    )
    for r in memories.get("distributed_ram") or []:
        digest["memories"].append({"kind": "array in LUT RAM", "object": r.get("object"), "reason": r.get("reason")})
    util_mem = facts.get("utilization_memory") or {}
    if util_mem.get("lut_as_distributed_ram"):
        digest["memories"].append({"kind": "LUT RAM", "object": "(all)", "luts": util_mem["lut_as_distributed_ram"]})
    digest["block_ram_without_output_register"] = memories.get("block_ram_no_output_register") or []

    groups = {}
    for row in dsp_mapping or []:
        regs = " ".join(f"{r}={row[r]}" for r in ("AREG", "BREG", "CREG", "DREG", "ADREG", "MREG", "PREG")
                        if row.get(r) is not None)
        key = (row.get("dsp_mapping"), regs)
        groups[key] = groups.get(key, 0) + 1
    digest["dsp"] = [{"mapping": k[0], "registers": k[1], "count": n} for k, n in groups.items()]
    dsp_report = facts.get("dsp") or {}
    digest["dsp_absorbed"] = {
        "registers": sorted({r for d in dsp_report.get("dsps") or [] for r in d["registers"]})[:12],
        "operators": sorted({o for d in dsp_report.get("dsps") or [] for o in d["operators"]})[:12],
    }

    digest["critical_path"] = None
    for path in timing_paths or []:
        # An 'inf' slack is an unconstrained path (port to port): not a register-to-register path.
        if path.get("slack_ns") is not None and path["slack_ns"] != float("inf"):
            digest["critical_path"] = {k: path.get(k) for k in (
                "slack_ns", "source", "destination", "logic_levels", "logic_delay_ns", "route_delay_ns",
                "breakdown")}
            break
    digest["wns_ns"] = wns_ns
    digest["high_fanout"] = [{"net": n["net"], "fanout": n["fanout"], "slack_ns": n.get("worst_slack_ns")}
                             for n in (high_fanout or [])[:3]]
    digest["control_sets"] = (control_sets or {}).get("total")
    digest["removed"] = {k: {"total": v["total"], "names": v["names"][:4]}
                         for k, v in (facts.get("removed") or {}).items()}
    return digest


# ---------------------------------------------------------------------- formatting

def _value(now, before, label):
    """'LUT 12' or, when `before` is known and different, 'LUT 12 (was 20)'."""
    if before is None or before == now:
        return f"{label} {now}"
    return f"{label} {now} (was {before})"


def _area_text(entry, before=None):
    """Non-zero area items of a hierarchy entry; an item that dropped to zero is still shown against `before`."""
    parts = []
    for key, label in _AREA_LABELS:
        now, was = entry.get(key, 0), (before or {}).get(key)
        if now or was:
            parts.append(_value(now, was, label))
    return ", ".join(parts) or "empty"


def _hierarchy_lines(hier, previous_hier):
    if not hier or not hier.get("modules"):
        return []
    before = {m["module"]: m for m in (previous_hier or {}).get("modules", [])}
    lines = [f"Hierarchy (instances of one module summed): total {_area_text(hier['top'], (previous_hier or {}).get('top'))}"]
    for entry in hier["modules"][:10]:
        old = before.get(entry["module"])
        count = f" x{entry['instances']}" if entry["instances"] > 1 else ""
        if previous_hier and old is None and entry["module"] != "(top logic)":
            note = " [new module]"
        else:
            note = ""
        lines.append(f"- {entry['module']}{count}: {_area_text(entry, old)}{note}")
    if previous_hier:
        gone = [m for m in before if m not in {e['module'] for e in hier['modules']} and m != "(top logic)"]
        if gone:
            lines.append("- modules no longer present: " + ", ".join(sorted(gone)))
    return lines


def _memory_lines(digest):
    lines = []
    for m in digest.get("memories") or []:
        if m["kind"] == "block RAM":
            lines.append(f"- block RAM {m['object']} {m['shape']}: RAMB18 x{m['ramb18']}, RAMB36 x{m['ramb36']}")
        elif m["kind"] == "ROM":
            lines.append(f"- ROM {m['object']} {m['shape']}: {m['implemented_as']}")
        elif m["kind"] == "shift register":
            lines.append(f"- shift register {m['object']} {m['shape']}: SRL16E x{m['srl16e']}, SRLC32E x{m['srlc32e']}")
        elif m["kind"] == "array in LUT RAM":
            lines.append(f"- array {m['object']} kept in LUT RAM (Vivado: {m['reason']})")
        else:
            lines.append(f"- LUT RAM: {m['luts']} LUT")
    for inst in (digest.get("block_ram_without_output_register") or [])[:3]:
        lines.append(f"- block RAM {inst} has no output register inside the RAM block")
    return ["Memories:"] + lines if lines else []


def _dsp_lines(digest):
    lines = [f"- DSP48E1 x{d['count']}: mode {d['mapping']}, {d['registers']}" for d in digest.get("dsp") or []]
    absorbed = digest.get("dsp_absorbed") or {}
    if absorbed.get("registers"):
        lines.append("- registers absorbed into DSP48E1: " + ", ".join(absorbed["registers"]))
    if absorbed.get("operators"):
        lines.append("- operators absorbed into DSP48E1: " + ", ".join(absorbed["operators"]))
    return ["DSP48E1 mapping:"] + lines if lines else []


def _timing_lines(digest, previous):
    path = digest.get("critical_path")
    lines = []
    if path:
        levels = path.get("logic_levels")
        text = f"- worst register-to-register path: {path.get('source')} -> {path.get('destination')}"
        text += f", slack {path['slack_ns']:.3f} ns"
        if levels is not None:
            text += f", {levels} logic level(s)"
            if path.get("breakdown"):
                text += " (" + " ".join(f"{k}x{v}" for k, v in path["breakdown"].items()) + ")"
        if path.get("logic_delay_ns") is not None and path.get("route_delay_ns") is not None:
            text += f", logic {path['logic_delay_ns']:.3f} ns + route {path['route_delay_ns']:.3f} ns"
        lines.append(text)
    elif digest.get("wns_ns") is None:
        lines.append("- no constrained register-to-register path (every register is inside a DSP48E1 or block RAM, "
                     "or the logic is combinational)")
    for net in digest.get("high_fanout") or []:
        slack = "n/a" if net.get("slack_ns") is None else f"{net['slack_ns']:.3f} ns"
        lines.append(f"- high-fanout net {net['net']}: fanout {net['fanout']}, worst slack {slack}")
    if digest.get("control_sets") is not None:
        was = (previous or {}).get("control_sets")
        lines.append("- " + _value(digest["control_sets"], was, "control sets"))
    return ["Timing:"] + lines if lines else []


def _removed_lines(digest):
    labels = {"unused_removed": "unused registers removed", "trimmed": "registers trimmed", "merged": "registers merged",
              "constant": "registers turned into constants", "unused_port": "ports without load"}
    lines = []
    for key, label in labels.items():
        entry = (digest.get("removed") or {}).get(key)
        if entry:
            lines.append(f"- {label}: {entry['total']} ({', '.join(entry['names'])})")
    return ["Removed by synthesis:"] + lines if lines else []


def format_digest(digest, previous=None, previous_label="the reference", max_chars=DEFAULT_MAX_CHARS):
    """
    Digest dict -> text for the prompt. With `previous` (the digest of the reference or of the best attempt) every
    hierarchy number that changed is shown as 'now (was before)', matched by module name.
    The text is cut line by line at `max_chars`: the hierarchy comes first, the least useful lines last.
    """
    if not digest:
        return ""
    head = "Report digest (observations from the Vivado reports"
    head += f"; '(was N)' = value in {previous_label}):" if previous else "):"
    sections = [
        _hierarchy_lines(digest.get("hierarchy"), (previous or {}).get("hierarchy")),
        _dsp_lines(digest),
        _memory_lines(digest),
        _timing_lines(digest, previous),
        _removed_lines(digest),
    ]
    lines = [head]
    for section in sections:
        lines.extend(section)
    text = "\n".join(lines)
    if len(text) > max_chars:
        kept = []
        for line in lines:
            if len("\n".join(kept + [line])) > max_chars - 40:
                break
            kept.append(line)
        text = "\n".join(kept) + "\n- (further lines omitted)"
    return text if len(lines) > 1 else ""

"""
Mapping facts of a Vivado synthesis log (and utilization report).

The synthesis log tells how the RTL was mapped onto the 7-Series fabric: which registers and operators were
absorbed into a DSP48E1, which arrays became block RAM / ROM LUTs / SRLs, which sequential elements were
removed. MappingAnalyzer.analyze() turns those lines into a JSON-able dict of facts; Analysis/ReportDigest.py
condenses them for the LLM feedback. The facts are observations only: no rule here says what to change.

Self-contained (no Vivado / LLM dependency). Formats were checked against Vivado 2024.1 logs
(Tests/fixtures/*). Every parser is tolerant: a missing section yields an empty result.
"""

import re
from collections import OrderedDict

# Synthesis messages about removed / merged / constant sequential elements and unused ports.
_REMOVAL_CODES = {
    "8-6014": "unused_removed",      # Unused sequential element X was removed.
    "8-3332": "unused_removed",      # Sequential element (X) is unused and will be removed from module M.
    "8-3936": "trimmed",             # Found unconnected internal register 'X' and it is trimmed from N to M bits.
    "8-3886": "merged",              # merging instance 'A' (FDRE) to 'B'
    "8-3333": "constant",            # propagating constant 0 across sequential element (X)
    "8-3917": "constant_output",     # design M has port P driven by constant 0
    "8-7129": "unused_port",         # Port P in module M is either unconnected or has no load
}
_MAX_NAMES = 12

_SYNTH_MESSAGE_RE = re.compile(r'^(?:INFO|WARNING): \[Synth (8-\d+)\] (.*?)\s*(?:\[[^\]\n]*\.v:\d+\])?\s*$', re.MULTILINE)


def _split_cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _parse_tables(content, title_regex):
    """
    Parse every ASCII table that follows a line matching `title_regex`.
    Returns a list of (title_line, rows); each row is an OrderedDict column -> cell (duplicate column
    names get a numeric suffix).
    """
    tables = []
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if not re.search(title_regex, line):
            continue
        header = None
        rows = []
        started = False
        for follow in lines[index + 1:index + 80]:
            stripped = follow.strip()
            if stripped.startswith('|'):
                started = True
                cells = _split_cells(follow)
                if header is None:
                    seen = {}
                    header = []
                    for name in cells:
                        seen[name] = seen.get(name, 0) + 1
                        header.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
                else:
                    rows.append(OrderedDict(zip(header, cells)))
            elif stripped.startswith('+'):
                started = True
            elif started:
                break
        tables.append((line.strip(), rows))
    return tables


def _preferred(tables):
    """Final report if present, else the last preliminary one."""
    final = [rows for title, rows in tables if "Final" in title]
    if final:
        return final[-1]
    return tables[-1][1] if tables else []


class MappingAnalyzer:
    """Namespace of static parsers and the rule table."""

    # ------------------------------------------------------------------ DSP

    @staticmethod
    def parse_dsp_report(content):
        """
        'DSP Report:' lines of the cross-boundary optimization step.

        Returns {"dsps": [{"dsp", "mode", "registers", "operators"}], "shared_registers": {reg: [dsp, ...]}}.
        A register listed under several DSPs is duplicated into the operand registers of each slice.
        """
        dsps = OrderedDict()
        for match in re.finditer(r'^DSP Report: Generating DSP (\S+), operation Mode is: (.*?)\.\s*$',
                                 content, re.MULTILINE):
            dsps.setdefault(match.group(1), {"dsp": match.group(1), "mode": match.group(2),
                                             "registers": [], "operators": []})
        for kind, key in (("register", "registers"), ("operator", "operators")):
            for match in re.finditer(rf'^DSP Report: {kind} (\S+) is absorbed into DSP (\S+?)\.\s*$',
                                     content, re.MULTILINE):
                entry = dsps.setdefault(match.group(2), {"dsp": match.group(2), "mode": None,
                                                         "registers": [], "operators": []})
                if match.group(1) not in entry[key]:
                    entry[key].append(match.group(1))
        users = OrderedDict()
        for entry in dsps.values():
            for reg in entry["registers"]:
                users.setdefault(reg, []).append(entry["dsp"])
        shared = {reg: names for reg, names in users.items() if len(names) > 1}
        return {"dsps": list(dsps.values()), "shared_registers": shared}

    # ------------------------------------------------------------------ RTL components

    @staticmethod
    def parse_rtl_components(content):
        """
        'Detailed RTL Component Info' (what the elaborator saw before technology mapping), e.g.
            +---Registers :
                8 Bit    Registers := 9
            +---RAMs :
                16K Bit  (1024 X 16 bit)  RAMs := 1
        Returns [{"group", "description", "count"}].
        """
        start = content.find("Detailed RTL Component Info")
        if start < 0:
            return []
        components = []
        group = None
        for line in content[start:].splitlines()[1:]:
            if line.startswith('---') or line.startswith('Finished'):
                break
            head = re.match(r'^\+---(.+?)\s*:\s*$', line)
            if head:
                group = head.group(1).strip()
                continue
            entry = re.match(r'^\s+(.*?)\s*:=\s*(\d+)\s*$', line)
            if entry and group:
                components.append({"group": group, "description": re.sub(r'\s+', ' ', entry.group(1)),
                                   "count": int(entry.group(2))})
        return components

    # ------------------------------------------------------------------ memories

    @classmethod
    def parse_memories(cls, content):
        """
        ROM / block RAM / shift-register mapping reports and the 'no output register' block RAM notes.

        Returns {"rom": [{"object", "depth_x_width", "implemented_as"}],
                 "block_ram": [{"object", "port_a", "port_b", "ramb18", "ramb36"}],
                 "shift_registers": [{"name", "length", "width", "srl16e", "srlc32e"}],
                 "distributed_ram": [{"object", "reason"}],        (arrays Vivado kept in LUT RAM, and why)
                 "block_ram_no_output_register": [instance, ...]}.
        """
        rom = [
            {"object": r.get("RTL Object"), "depth_x_width": r.get("Depth x Width"),
             "implemented_as": r.get("Implemented As")}
            for r in _preferred(_parse_tables(content, r'^ROM: .*Mapping Report'))
        ]
        # The ROM table repeats an internal alias (p_<n>_out) next to the named array; keep the named one.
        rom = [r for r in rom if not re.match(r'^p_\d+_out', r["object"] or "")]
        block_ram = [
            {"object": r.get("RTL Object"), "port_a": r.get("PORT A (Depth x Width)"),
             "port_b": r.get("PORT B (Depth x Width)") or None, "ramb18": _to_int(r.get("RAMB18")),
             "ramb36": _to_int(r.get("RAMB36"))}
            for r in _preferred(_parse_tables(content, r'^Block RAM: .*Mapping Report'))
        ]
        shift = [
            {"name": r.get("RTL Name"), "length": _to_int(r.get("Length")), "width": _to_int(r.get("Width")),
             "srl16e": _to_int(r.get("SRL16E")), "srlc32e": _to_int(r.get("SRLC32E"))}
            for r in _preferred(_parse_tables(content, r'^Static Shift Register Report'))
        ]
        # The block RAM table is only a preliminary report: an array listed there may be moved to LUT RAM later
        # (message 8-5584, with the reason). Such arrays are reported as LUT RAM, not as block RAM.
        distributed = [
            {"object": obj, "reason": reason.strip()}
            for obj, reason in re.findall(
                r'\[Synth 8-5584\] The signal "([^"]+)" is implemented as distributed LUT RAM for the following '
                r'reason\(s\): ([^\n]*)', content)
        ]
        moved = {d["object"] for d in distributed}
        block_ram = [r for r in block_ram if not any(m.endswith("/" + (r["object"] or "")) for m in moved)]
        no_out_reg = re.findall(r'\[Synth 8-7052\] The timing for the instance (\S+) \(implemented as a Block RAM\)',
                                content)
        return {"rom": rom, "block_ram": block_ram, "shift_registers": shift, "distributed_ram": distributed,
                "block_ram_no_output_register": no_out_reg}

    @staticmethod
    def parse_utilization_memory(util_content):
        """LUT-as-memory and block RAM usage from report_utilization: {"lut_as_distributed_ram", ...}."""
        def used(name):
            match = re.search(rf'^\|\s*{re.escape(name)}\*?\s*\|\s*([\d.]+)\s*\|', util_content or "", re.MULTILINE)
            return float(match.group(1)) if match else 0.0

        return {
            "lut_as_distributed_ram": int(used("LUT as Distributed RAM")),
            "lut_as_shift_register": int(used("LUT as Shift Register")),
            "block_ram_tiles": used("Block RAM Tile"),
            "ramb18": int(used("RAMB18")),
        }

    # ------------------------------------------------------------------ removed sequential elements

    @staticmethod
    def parse_removed_elements(content):
        """
        Registers / ports that synthesis removed, merged or reduced to constants.

        Returns {category: {"total": n, "names": [first names]}} for the categories unused_removed, trimmed,
        merged, constant, constant_output, unused_port (only those found).
        """
        found = OrderedDict()
        for match in _SYNTH_MESSAGE_RE.finditer(content):
            category = _REMOVAL_CODES.get(match.group(1))
            if not category:
                continue
            text = match.group(2)
            name = None
            for pattern in (r'^Unused sequential element (\S+) was removed', r'^Sequential element \((\S+?)\) is unused',
                            r"^Found unconnected internal register '([^']+)'", r"^merging instance '([^']+)'",
                            r'^propagating constant \d across sequential element \((\S+?)\)',
                            r'^design \S+ has port (\S+) driven', r'^Port (\S+) in module'):
                m = re.match(pattern, text)
                if m:
                    name = m.group(1)
                    break
            entry = found.setdefault(category, {"total": 0, "names": []})
            entry["total"] += 1
            if name and name not in entry["names"] and len(entry["names"]) < _MAX_NAMES:
                entry["names"].append(name)
        return found

    # ------------------------------------------------------------------ everything

    @classmethod
    def analyze(cls, synth_log, utilization=None):
        """All mapping facts of one synthesis run (JSON-serializable dict)."""
        synth_log = synth_log or ""
        facts = {
            "dsp": cls.parse_dsp_report(synth_log),
            "rtl_components": cls.parse_rtl_components(synth_log),
            "memories": cls.parse_memories(synth_log),
            "removed": cls.parse_removed_elements(synth_log),
        }
        if utilization:
            facts["utilization_memory"] = cls.parse_utilization_memory(utilization)
        return facts


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None

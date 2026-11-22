"""
Facts mined from the Vivado LOGS and from report_design_analysis, for the feedback and for the research record.

The reports (utilization, timing, ...) say WHAT the result is. The logs say what the tools DID to get there, which
is the missing half of the feedback for an RTL-optimisation loop:

  synthesis log        warnings / critical warnings and the informative messages, counted per message id, with
                       the first examples and their source location (file:line of the RTL that caused them)
  opt_design           cells created / removed per optimisation phase (retarget, constant propagation, sweep, ...)
  place_design         physical-synthesis replication of high-fanout nets, estimated congestion
  route_design         timing trajectory WNS(placer) -> WNS(post-place) -> WNS(each router iteration) -> final,
                       rip-up passes (node overlaps), completion and timing status
  report_design_analysis  critical-path structure (logic vs. net delay, logical path, fanout) and the logic-level
                       distribution of the worst endpoints

Every parser here works on TEXT (the content of a log or report) and returns plain JSON-able dicts, so it is
testable offline with real Vivado output (Tests/test_log_mining.py). A parser never raises on text it does not
understand: the corresponding keys are simply absent / None / empty. Nothing here suggests a transformation; the
facts are observations (same rule as Analysis/ReportDigest.py).

Stage rule (enforced by the caller, Pipeline/metrics.py): a synthesis-only run feeds ONLY the synthesis log and the
synthesis design analysis into this module; the implementation log is never read in that case.

Entry points:
  LogMiner.mine_synth_log(text)           synthesis log  -> {"messages", "modules_synthesized"}
  LogMiner.mine_impl_log(text)            implementation log -> {"opt", "physopt", "timing_trajectory", ...}
  LogMiner.mine_design_analysis(text)     report_design_analysis -> {"critical_path", "logic_levels", "congestion"}
  LogMiner.format_log_facts(facts)        the facts of a run -> a compact text block for the LLM feedback
"""

import re

# One log message: "INFO: [Synth 8-6157] synthesizing module 'x' [/path/x.v:12]".
_MESSAGE_RE = re.compile(
    r"^(INFO|WARNING|CRITICAL WARNING|ERROR): \[([A-Za-z_]+) (\d+-\d+)\] (.*)$", re.M)
# Source location Vivado appends to a message: "[/path/design.v:12]" (kept apart, it points into the RTL).
_LOCATION_RE = re.compile(r"\s*\[([^\[\]]+:\d+)\]\s*$")

# Severity order used to sort the message groups (most serious first).
_SEVERITY_RANK = {"ERROR": 0, "CRITICAL WARNING": 1, "WARNING": 2, "INFO": 3}

# INFO messages of these tools carry no information about the design (licences, project files, timer set-up,
# DRC run notices) or are already parsed into dedicated fields (opt_design / phys_opt / place / route INFO lines),
# so they are dropped from the message groups. WARNING / CRITICAL WARNING / ERROR are never dropped by tool, only
# by the explicit ids below.
_BOILERPLATE_INFO_TOOLS = {
    "Common", "Project", "Device", "Netlist", "filemgmt", "Vivado", "Timing", "Coretcl", "IP_Flow", "Constraints",
    "DRC", "Implflow", "Vivado_Tcl", "Opt", "Physopt", "Place", "Route", "Pwropt",
}
# Ids (any severity) that are pure tool housekeeping: multithreading notices, helper processes, module markers
# and the "parallel synthesis criteria not met" notice, which is always printed for small designs.
_BOILERPLATE_IDS = {
    "Synth 8-6157", "Synth 8-6155", "Synth 8-7075", "Synth 8-7078", "Synth 8-7079", "Synth 8-7080",
    "Place 30-611", "Route 35-254", "Physopt 32-721",
}

# How many message groups / examples per group are kept (the facts must stay small).
MAX_MESSAGE_GROUPS = 12
MAX_EXAMPLES_PER_GROUP = 3
MAX_TEXT_CHARS = 160


def _number(token):
    """Float of a report token ('-0.853', '5.000'); None for 'N/A' / 'NA' / unparsable text."""
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


class LogMiner:
    """Stateless parsers for the Vivado logs and for report_design_analysis (all methods are static)."""

    # ------------------------------------------------------------------ messages (both logs)

    @staticmethod
    def mine_messages(text):
        """
        Group the log messages by (severity, id): count, first examples, source locations.

        Returns a list of at most MAX_MESSAGE_GROUPS dicts, most serious severity first, then most frequent:
            {"severity": "WARNING", "id": "Synth 8-6014", "count": 6,
             "examples": [{"text": "Unused sequential element acc_reg was removed.", "location": "t.v:12"}, ...]}
        Housekeeping messages (see _BOILERPLATE_*) are left out.
        """
        groups = {}
        for severity, tool, number, body in _MESSAGE_RE.findall(text or ""):
            message_id = f"{tool} {number}"
            if message_id in _BOILERPLATE_IDS:
                continue
            if severity == "INFO" and tool in _BOILERPLATE_INFO_TOOLS:
                continue
            location_match = _LOCATION_RE.search(body)
            location = location_match.group(1) if location_match else None
            if location_match:
                body = body[:location_match.start()]
            group = groups.setdefault((severity, message_id), {"severity": severity, "id": message_id,
                                                               "count": 0, "examples": []})
            group["count"] += 1
            if len(group["examples"]) < MAX_EXAMPLES_PER_GROUP:
                group["examples"].append({"text": body.strip()[:MAX_TEXT_CHARS], "location": location})
        ordered = sorted(groups.values(), key=lambda g: (_SEVERITY_RANK[g["severity"]], -g["count"], g["id"]))
        return ordered[:MAX_MESSAGE_GROUPS]

    # ------------------------------------------------------------------ synthesis log

    @staticmethod
    def mine_synth_log(text):
        """Synthesis log -> {"messages": [...], "modules_synthesized": N}."""
        return {
            "messages": LogMiner.mine_messages(text),
            "modules_synthesized": len(re.findall(r"\[Synth 8-6157\]", text or "")),
        }

    # ------------------------------------------------------------------ implementation log

    @staticmethod
    def _opt_changes(text):
        """Rows of the 'Opt_design Change Summary' table -> [{"phase", "created", "removed"}] (all rows)."""
        marker = (text or "").find("Opt_design Change Summary")
        if marker < 0:
            return []
        rows, started = [], False
        row_re = re.compile(r"^\|\s+([A-Za-z][^|]*?)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|")
        for line in text[marker:].splitlines()[1:]:
            match = row_re.match(line)
            if match:
                started = True
                rows.append({"phase": match.group(1), "created": int(match.group(2)), "removed": int(match.group(3))})
            elif started and line.strip() and not line.startswith(("|", "-")):
                break
        return rows

    @staticmethod
    def _timing_trajectory(text):
        """
        Every timing estimate the implementation tools print, in log order. Each entry:
            {"step": "placer_estimate" | "post_placement" | "router_intermediate" | "post_route_estimate",
             "wns_ns", "tns_ns", "whs_ns", "ths_ns"}   (None where the message does not carry the value)
        The estimates come from the tools' own timers (not report_timing), so they are indicative; the final
        numbers of a run always come from the timing report. The trajectory shows WHERE timing was lost or won.
        """
        steps = {"Physopt 32-619": "placer_estimate", "Place 30-746": "post_placement",
                 "Route 35-416": "router_intermediate", "Route 35-57": "post_route_estimate"}
        trajectory = []
        for severity, tool, number, body in _MESSAGE_RE.findall(text or ""):
            step = steps.get(f"{tool} {number}")
            if step is None:
                continue
            entry = {"step": step}
            for key, label in (("wns_ns", "WNS"), ("tns_ns", "TNS"), ("whs_ns", "WHS"), ("ths_ns", "THS")):
                match = re.search(label + r"=\s*(-?\d+(?:\.\d+)?|N/A)", body)
                entry[key] = _number(match.group(1)) if match else None
            trajectory.append(entry)
        return trajectory

    @staticmethod
    def _placement_congestion(text):
        """'Post-Placement Estimated Congestion' table -> {"North": {"global": 1, "short": 2}, ..., "max_region": 2}."""
        marker = (text or "").find("Post-Placement Estimated Congestion")
        if marker < 0:
            return None
        row_re = re.compile(r"\|\s*(North|South|East|West)\s*\|\s*(\d+)x\d+\s*\|\s*(\d+)x\d+\s*\|")
        table = {}
        for direction, global_size, short_size in row_re.findall(text[marker:marker + 1500]):
            table[direction] = {"global": int(global_size), "short": int(short_size)}
        if not table:
            return None
        table["max_region"] = max(max(v["global"], v["short"]) for v in table.values())
        return table

    @staticmethod
    def mine_impl_log(text):
        """
        Implementation log -> what opt_design / place_design / route_design did:

          "opt":     {"changes": [phases that created or removed cells], "cells_created", "cells_removed",
                      "retargeted_cells", "srl_remap": {...} | None}
          "physopt": {"replicated_nets": [{"net", "copies"}], "cells_created", "cells_deleted", "cells_moved",
                      "no_candidates": [what the optimiser looked for and did not find]}
          "timing_trajectory": see _timing_trajectory; "first_wns_ns" / "final_wns_ns" / "wns_gain_ns" summarise it
          "placement_congestion": see _placement_congestion (None when not printed)
          "routing": {"overlap_passes", "peak_overlap_nodes", "initial_unrouted_nets", "completed",
                      "estimated_timing_met"}
          "messages": the non-boilerplate messages (see mine_messages)
        """
        text = text or ""
        rows = LogMiner._opt_changes(text)
        retargeted = re.search(r"\[Opt 31-49\] Retargeted (\d+) cell", text)
        srl = re.search(r"\[Opt 31-1064\] SRL Remap converted (\d+) SRLs to (\d+) registers and converted "
                        r"(\d+) registers of register chains to (\d+) SRLs", text)
        opt = {
            "changes": [r for r in rows if r["created"] or r["removed"]],
            "cells_created": sum(r["created"] for r in rows),
            "cells_removed": sum(r["removed"] for r in rows),
            "retargeted_cells": int(retargeted.group(1)) if retargeted else 0,
            "srl_remap": ({"srls_to_registers": int(srl.group(1)), "registers_to_srls": int(srl.group(4))}
                          if srl else None),
        }

        replicated = [{"net": net, "copies": int(copies)}
                      for net, copies in re.findall(r"\[Physopt 32-81\] Processed net (\S+)\. Replicated (\d+) times", text)]
        created = deleted = moved = 0
        for c, d, m in re.findall(r"\[Physopt 32-775\] .*?Created (\d+) new cells?, deleted (\d+) existing cells? "
                                  r"and moved (\d+) existing cells?", text):
            created, deleted, moved = created + int(c), deleted + int(d), moved + int(m)
        no_candidates = []
        for line in re.findall(r"\[Physopt 32-\d+\] (No candidate .*)", text):
            line = line.strip()[:MAX_TEXT_CHARS]
            if line not in no_candidates:
                no_candidates.append(line)
        physopt = {"replicated_nets": replicated, "cells_created": created, "cells_deleted": deleted,
                   "cells_moved": moved, "no_candidates": no_candidates}

        trajectory = LogMiner._timing_trajectory(text)
        wns_values = [e["wns_ns"] for e in trajectory if e["wns_ns"] is not None]
        first = wns_values[0] if wns_values else None
        final = wns_values[-1] if wns_values else None

        overlaps = [int(n) for n in re.findall(r"Number of Nodes with overlaps = (\d+)", text)]
        unrouted = re.search(r"Number of Unrouted Nets\s*=\s*(\d+)", text)
        if "[Route 35-328]" in text:
            timing_met = False                       # "Router estimated timing not met."
        elif any(e["step"] == "post_route_estimate" for e in trajectory):
            timing_met = True
        else:
            timing_met = None
        routing = {
            "overlap_passes": len(overlaps),
            "peak_overlap_nodes": max(overlaps) if overlaps else 0,
            "initial_unrouted_nets": int(unrouted.group(1)) if unrouted else None,
            "completed": "Router Completed Successfully" in text,
            "estimated_timing_met": timing_met,
        }
        return {
            "opt": opt, "physopt": physopt, "timing_trajectory": trajectory,
            "first_wns_ns": first, "final_wns_ns": final,
            "wns_gain_ns": round(final - first, 3) if first is not None and final is not None else None,
            "placement_congestion": LogMiner._placement_congestion(text),
            "routing": routing, "messages": LogMiner.mine_messages(text),
        }

    # ------------------------------------------------------------------ report_design_analysis

    # Row labels of 'Setup Path Characteristics' -> (key, kind); kind: "ns" float, "int", "split" (value + (pct%)), "text".
    _PATH_ROWS = {
        "Requirement": ("requirement_ns", "ns"), "Path Delay": ("path_delay_ns", "ns"),
        "Logic Delay": ("logic_delay", "split"), "Net Delay": ("net_delay", "split"),
        "Clock Skew": ("clock_skew_ns", "ns"), "Slack": ("slack_ns", "ns"),
        "Logic Levels": ("logic_levels", "int"), "Routes": ("routes", "int"), "High Fanout": ("high_fanout", "int"),
        "Logical Path": ("logical_path", "text"), "DSP Block": ("dsp_block", "text"),
        "RAM Registers": ("ram_registers", "text"),
        "Start Point Pin Primitive": ("start_primitive", "text"), "End Point Pin Primitive": ("end_primitive", "text"),
        "Start Point Pin": ("start_pin", "text"), "End Point Pin": ("end_pin", "text"),
    }

    @staticmethod
    def _table_cells(line):
        """'| a | b |' -> ['a', 'b']; None for a line that is not a table row."""
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            return None
        return [cell.strip() for cell in line.strip("|").split("|")]

    @staticmethod
    def _sections(text):
        """Numbered sections of the report: {"Setup Path Characteristics": body, ...} (table of contents skipped)."""
        pattern = re.compile(r"^\d+\. ([^\n]+?)(?: \d+-\d+)?\n-{5,}\n(.*?)(?=^\d+\. [^\n]+\n-{5,}\n|\Z)", re.M | re.S)
        return {title.strip(): body for title, body in pattern.findall(text or "")}

    @staticmethod
    def mine_design_analysis(text):
        """
        report_design_analysis -> {"critical_path": {...}, "logic_levels": {...}, "congestion": {...}}

        critical_path  the worst setup path: delay split (logic vs. net, in ns and %), logic levels, the
                       'Logical Path' string with the fanout of every hop ('FDRE/C-(18)-LUT6-(2)-...-FDRE/D'),
                       start / end pin, high fanout, DSP / RAM register use.
        logic_levels   histogram of the number of logic levels of the worst ~1000 endpoints, per clock
                       ({"clock", "requirement_ns", "histogram": {"0": 70, ...}, "endpoints", "mean", "max"}).
        congestion     {"placer_windows": N, "router_windows": N}: congested windows above level 5 (0 = none).
        """
        sections = LogMiner._sections(text)
        result = {"critical_path": None, "logic_levels": [], "congestion": {}}

        path = {}
        for line in sections.get("Setup Path Characteristics", "").splitlines():
            cells = LogMiner._table_cells(line)
            if not cells or len(cells) < 2 or cells[0] not in LogMiner._PATH_ROWS:
                continue
            key, kind = LogMiner._PATH_ROWS[cells[0]]
            value = cells[1]                         # first path column ("Path #1") only
            if kind == "split":                      # "2.445(44%)"
                match = re.match(r"(-?\d+(?:\.\d+)?)\((\d+)%\)", value)
                if match:
                    path[key + "_ns"], path[key + "_pct"] = float(match.group(1)), int(match.group(2))
            elif kind == "ns":
                path[key] = _number(value)
            elif kind == "int":
                path[key] = int(value) if value.isdigit() else None
            else:
                path[key] = value
        result["critical_path"] = path or None

        lines = sections.get("Logic Level Distribution", "").splitlines()
        for index, line in enumerate(lines):
            cells = LogMiner._table_cells(line)
            if not cells or cells[:2] != ["End Point Clock", "Requirement"]:
                continue
            levels = cells[2:]
            for row in lines[index + 1:]:
                row_cells = LogMiner._table_cells(row)
                if row_cells is None:
                    if row.startswith("+"):
                        continue                     # separator line
                    break
                if len(row_cells) != len(cells):
                    continue
                counts = [int(c) if c.isdigit() else 0 for c in row_cells[2:]]
                total = sum(counts)
                histogram = {lvl: n for lvl, n in zip(levels, counts)}
                result["logic_levels"].append({
                    "clock": row_cells[0], "requirement_ns": _number(row_cells[1].replace("ns", "")),
                    "histogram": histogram, "endpoints": total,
                    "mean": round(sum(int(l) * n for l, n in histogram.items() if l.isdigit()) / total, 2) if total else None,
                    "max": max((int(l) for l, n in histogram.items() if l.isdigit() and n), default=None),
                })
            break

        for title, key in (("Placer Final Level Congestion Reporting", "placer_windows"),
                           ("Initial Estimated Router Congestion Reporting", "router_windows")):
            body = sections.get(title)
            if body is not None:
                result["congestion"][key] = len(re.findall(r"^\|\s*(?:North|South|East|West)\s*\|", body, re.M))
        return result

    # ------------------------------------------------------------------ text for the LLM feedback

    @staticmethod
    def _format_messages(messages, limit=6):
        """Message groups -> one line each: '[WARNING Synth 8-6014] x6: text (file.v:12)'."""
        lines = []
        for group in messages[:limit]:
            example = group["examples"][0] if group["examples"] else {"text": "", "location": None}
            where = f" ({example['location']})" if example.get("location") else ""
            lines.append(f"  [{group['severity']} {group['id']}] x{group['count']}: {example['text']}{where}")
        return lines

    @staticmethod
    def format_log_facts(facts):
        """
        The facts of one run ({"stage", "synth", "impl", "design_analysis"}, as built by
        Pipeline/metrics.py: collect_log_facts) -> a compact text block; '' when there is nothing to say.
        Sections that are absent (for example "impl" in a synthesis-only run) are simply not printed.
        """
        if not facts:
            return ""
        out = []
        synth = facts.get("synth") or {}
        if synth.get("messages"):
            out.append("Synthesis log messages:")
            out.extend(LogMiner._format_messages(synth["messages"]))

        impl = facts.get("impl")
        if impl:
            opt, physopt = impl.get("opt") or {}, impl.get("physopt") or {}
            line = f"opt_design: {opt.get('cells_removed', 0)} cells removed, {opt.get('cells_created', 0)} created"
            changed = ", ".join(f"{c['phase']} (-{c['removed']}/+{c['created']})" for c in opt.get("changes", []))
            out.append("Implementation log:")
            out.append("  " + line + (f" [{changed}]" if changed else ""))
            if physopt.get("replicated_nets"):
                nets = ", ".join(f"{r['net']} x{r['copies']}" for r in physopt["replicated_nets"][:5])
                out.append(f"  phys_opt replicated high-fanout nets: {nets}")
            if physopt.get("cells_created") or physopt.get("cells_deleted") or physopt.get("cells_moved"):
                out.append(f"  phys_opt cells: +{physopt['cells_created']} / -{physopt['cells_deleted']} / "
                           f"moved {physopt['cells_moved']}")
            trajectory = impl.get("timing_trajectory") or []
            if trajectory:
                # Consecutive router iterations are folded into one item: 'router iterations a > b > c'.
                steps, router_run = [], []
                for e in trajectory + [{"step": None, "wns_ns": None}]:
                    if e["step"] == "router_intermediate":
                        if e["wns_ns"] is not None:
                            router_run.append(f"{e['wns_ns']:+.3f}")
                        continue
                    if router_run:
                        steps.append("router iterations " + " > ".join(router_run))
                        router_run = []
                    if e["step"] and e["wns_ns"] is not None:
                        steps.append(f"{e['step'].replace('_', ' ')} {e['wns_ns']:+.3f}")
                out.append("  WNS trajectory (ns, tool estimates): " + " -> ".join(steps))
            congestion = impl.get("placement_congestion")
            if congestion:
                out.append(f"  placement congestion: largest region {congestion['max_region']}x{congestion['max_region']}")
            routing = impl.get("routing") or {}
            if routing.get("overlap_passes"):
                out.append(f"  router: {routing['overlap_passes']} rip-up passes, peak {routing['peak_overlap_nodes']} "
                           f"overlapping nodes, estimated timing "
                           f"{'met' if routing.get('estimated_timing_met') else 'NOT met'}")
            if impl.get("messages"):
                out.extend(LogMiner._format_messages(impl["messages"], limit=4))

        analysis = facts.get("design_analysis") or {}
        path = analysis.get("critical_path")
        if path:
            parts = []
            if path.get("logic_levels") is not None:
                parts.append(f"{path['logic_levels']} logic levels")
            if path.get("logic_delay_ns") is not None and path.get("net_delay_ns") is not None:
                parts.append(f"logic {path['logic_delay_ns']} ns ({path.get('logic_delay_pct')}%) / "
                             f"net {path['net_delay_ns']} ns ({path.get('net_delay_pct')}%)")
            if path.get("high_fanout") is not None:
                parts.append(f"max fanout {path['high_fanout']}")
            out.append("Worst path structure: " + ", ".join(parts))
            if path.get("logical_path"):
                out.append(f"  logical path (cell-(fanout)-cell): {path['logical_path']}")
        for clock in analysis.get("logic_levels") or []:
            histogram = " ".join(f"{lvl}:{n}" for lvl, n in clock["histogram"].items())
            out.append(f"Logic levels of the worst {clock['endpoints']} endpoints (clock {clock['clock']}): "
                       f"{histogram} (mean {clock['mean']}, max {clock['max']})")
        return "\n".join(out)

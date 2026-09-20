"""OTA pipeline for LaMDA-Analog."""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from EDA_Interface.Spectre import SpectreInterface
from LLM_Interface.LLMClient import LLMClient
from utils import build_effective_prompt, create_outputs_folder, extract_spectre_deck, load_constraints, load_system_prompt, load_user_prompt


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse common boolean env values."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class OTAPipeline:
    """Class-based OTA flow that mirrors run_all_ota.sh steps."""

    def __init__(self, args):
        self.args = args
        self.root_dir = Path(__file__).resolve().parents[2]
        self.design_dir = Path(__file__).resolve().parent
        self.output_dir = self.design_dir / "output"
        self.run_root = Path(args.run_root) if args.run_root else self.output_dir
        model_tag = self._safe_tag(args.model)
        prompt_label = self._prompt_override_label(args.system_prompt, args.user_prompt)
        default_run_name = f"{time.strftime('%Y%m%d_%H%M%S')}_ota_{model_tag}"
        if args.exp_label:
            default_run_name = f"{default_run_name}_{self._safe_tag(args.exp_label)}"
        if prompt_label:
            default_run_name = f"{default_run_name}_{prompt_label}"
        self.run_dir = Path(args.run_dir) if args.run_dir else self.run_root / "gen_runs" / default_run_name
        self.spectre = SpectreInterface()
        self.llm = LLMClient(args.model)

    @staticmethod
    def _safe_tag(text: str) -> str:
        tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_.-")
        return tag or "model"

    @staticmethod
    def _prompt_override_label(system_prompt_path: str | None, user_prompt_path: str | None) -> str:
        return "customprompt" if (system_prompt_path or user_prompt_path) else ""

    @staticmethod
    def _load_prompt_override(path_value: str | None, design: str, prompt_kind: str) -> str:
        if not path_value:
            return load_system_prompt(design) if prompt_kind == "system" else load_user_prompt(design)

        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"{prompt_kind} prompt file not found: {path}")
        return path.read_text(encoding="utf-8")

    def run(self):
        create_outputs_folder(self.run_dir)

        system_prompt = self._load_prompt_override(self.args.system_prompt, "ota", "system")
        user_prompt = self._load_prompt_override(self.args.user_prompt, "ota", "user")
        constraints_on = not self.args.ablate_constraints
        constraints = load_constraints("ota") if constraints_on else {}

        effective_user = build_effective_prompt(user_prompt, constraints) if constraints_on else user_prompt.rstrip() + "\n"
        eff_user_file = self.run_dir / "user_effective_ota.txt"
        chatlog_file = self.run_dir / "chat_ota.jsonl"
        eff_user_file.write_text(effective_user, encoding="utf-8")

        llm_start = time.perf_counter()
        content, token_count = self.llm.generate_content(
            prompt=effective_user,
            system_prompt=system_prompt,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p,
        )
        llm_generation_time_s = time.perf_counter() - llm_start

        deck = extract_spectre_deck(content)
        run_id = self.run_dir.name
        raw_scs = self.run_dir / f"llm_raw_{run_id}.scs"
        raw_scs.write_text(deck, encoding="utf-8")

        with chatlog_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"role": "system", "content": system_prompt}) + "\n")
            f.write(json.dumps({"role": "user", "content": user_prompt}) + "\n")
            f.write(json.dumps({"role": "effective_user", "content": effective_user}) + "\n")

        binder_on = not self.args.ablate_binder
        tech_cfg = Path(self.args.tech_cfg)
        bound_scs = self.run_dir / f"ota_netlist_{run_id}.scs"
        simulation_scs = raw_scs
        if binder_on:
            self.spectre.tech_bind(raw_scs, bound_scs, tech_cfg)
            simulation_scs = bound_scs

        quick_log = self.run_dir / "quick_ota.log"
        quick_psf = self.run_dir / "quick_ota_psf"
        self.spectre.run_spectre(simulation_scs, quick_log, quick_psf, allow_fail=True)

        user_prompt_variant = "normal"
        if self.args.user_prompt:
            up_name = Path(self.args.user_prompt).name.lower()
            if "vague" in up_name:
                user_prompt_variant = "vague"
            elif "user_prompt" in up_name:
                user_prompt_variant = "normal"
            else:
                user_prompt_variant = "custom"

        system_prompt_on = bool(system_prompt.strip())

        metrics = self.spectre.parse_results(quick_psf)
        summary = {
            "design": "ota",
            "model": self.args.model,
            "exp_label": self.args.exp_label or "",
            "system_prompt_on": system_prompt_on,
            "constraints_on": constraints_on,
            "binder_on": binder_on,
            "user_prompt_variant": user_prompt_variant,
            "run_dir": str(self.run_dir),
            "token_count": token_count,
            "llm_generation_time_s": llm_generation_time_s,
            "raw_scs": str(raw_scs),
            "bound_scs": str(bound_scs) if binder_on else None,
            "simulation_input": str(simulation_scs),
            "quick_log": str(quick_log),
            "quick_psf": str(quick_psf),
            "metrics": metrics,
        }

        out_json = self.run_dir / "summary_ota.json"
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        if self.args.run_sweep:
            self._run_sweep(simulation_scs)

        print(f"RUN_DIR={self.run_dir}")
        print(f"WROTE RAW OTA DECK: {raw_scs}")
        print(f"WROTE OTA NETLIST: {bound_scs}")
        print(f"WROTE SUMMARY: {out_json}")

        return 1

    def _run_sweep(self, netlist_scs):
        """Run OTA sweep script."""
        import subprocess

        env = os.environ.copy()
        env["AC_PRIMARY_PARSER"] = "psf_ac_parser"
        env["TEMPLATE_NETLIST"] = str(netlist_scs)
        env["GEN_RUNS_DIR"] = str(self.run_root / "gen_runs")
        env["SWEEP_RESULTS_DIR"] = str(self.run_root / "sweep_results")
        prompt_label = self._prompt_override_label(self.args.system_prompt, self.args.user_prompt)
        env["SWEEP_RUN_LABEL"] = f"ota_{self._safe_tag(self.args.model)}"
        if self.args.exp_label:
            env["SWEEP_RUN_LABEL"] = f"{env['SWEEP_RUN_LABEL']}_{self._safe_tag(self.args.exp_label)}"
        if prompt_label:
            env["SWEEP_RUN_LABEL"] = f"{env['SWEEP_RUN_LABEL']}_{prompt_label}"
        env["SWEEP_RUN_LABEL"] = f"{env['SWEEP_RUN_LABEL']}_sweep"
        sweep_script = self.root_dir / "EDA_Interface" / "sweep_ota.py"
        if not sweep_script.exists():
            print(f"[WARN] OTA sweep script not found: {sweep_script}. Skipping sweep.")
            return

        subprocess.run(["python3", str(sweep_script)], cwd=self.root_dir, env=env, check=False)


def parse_args():
    parser = argparse.ArgumentParser(description="Run OTA flow for LaMDA-Analog")
    parser.add_argument("--model", default=os.getenv("MODEL", "gpt-4o-mini"))
    parser.add_argument("--max_tokens", type=int, default=int(os.getenv("MAX_TOKENS", "3000")))
    parser.add_argument("--temperature", type=float, default=float(os.getenv("TEMPERATURE", "1.0")))
    parser.add_argument("--top_p", type=float, default=float(os.getenv("TOP_P", "1.0")))
    parser.add_argument("--run_dir", default=os.getenv("RUN_DIR"))
    parser.add_argument("--run_root", default=os.getenv("RUN_ROOT"), help="Optional run-root for gen_runs/sweep_results.")
    parser.add_argument("--tech_cfg", default=os.getenv("TECH_CFG", "config/tech_config.yml"))
    parser.add_argument("--run_sweep", action="store_true")
    parser.add_argument("--system_prompt", default=os.getenv("SYSTEM_PROMPT"), help="Optional path to override system prompt file.")
    parser.add_argument("--user_prompt", default=os.getenv("USER_PROMPT"), help="Optional path to override user prompt file.")
    parser.add_argument("--ablate_constraints", action="store_true", default=_env_flag("ABLATE_CONSTRAINTS", False), help="Disable YAML constraints append in effective user prompt.")
    parser.add_argument("--ablate_binder", action="store_true", default=_env_flag("ABLATE_BINDER", False), help="Bypass tech binder and simulate raw LLM deck.")
    parser.add_argument("--exp_label", default=os.getenv("EXP_LABEL", ""), help="Experiment label appended to run naming and summary metadata.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    pipeline = OTAPipeline(args)
    sys.exit(0 if pipeline.run() else 1)

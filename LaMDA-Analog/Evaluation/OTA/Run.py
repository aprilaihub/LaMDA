"""OTA pipeline for LaMDA-Analog."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from EDA_Interface.Spectre import SpectreInterface
from LLM_Interface.LLMClient import LLMClient
from utils import build_effective_prompt, create_outputs_folder, extract_spectre_deck, load_constraints, load_system_prompt, load_user_prompt


class OTAPipeline:
    """Class-based OTA flow that mirrors run_all_ota.sh steps."""

    def __init__(self, args):
        self.args = args
        self.root_dir = Path(__file__).resolve().parents[2]
        self.design_dir = Path(__file__).resolve().parent
        self.output_dir = self.design_dir / "output"
        self.run_dir = Path(args.run_dir) if args.run_dir else self.output_dir / "gen_runs" / time.strftime("%Y%m%d_%H%M%S")
        self.spectre = SpectreInterface()
        self.llm = LLMClient(args.model)

    def run(self):
        create_outputs_folder(self.run_dir)

        system_prompt = load_system_prompt("ota")
        user_prompt = load_user_prompt("ota")
        constraints = load_constraints("ota")

        effective_user = build_effective_prompt(user_prompt, constraints)
        eff_user_file = self.run_dir / "user_effective_ota.txt"
        chatlog_file = self.run_dir / "chat_ota.jsonl"
        eff_user_file.write_text(effective_user, encoding="utf-8")

        content, token_count = self.llm.generate_content(
            prompt=effective_user,
            system_prompt=system_prompt,
            max_tokens=self.args.max_tokens,
            temperature=self.args.temperature,
            top_p=self.args.top_p,
        )

        deck = extract_spectre_deck(content)
        run_id = self.run_dir.name
        raw_scs = self.run_dir / f"llm_raw_{run_id}.scs"
        raw_scs.write_text(deck, encoding="utf-8")

        with chatlog_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"role": "system", "content": system_prompt}) + "\n")
            f.write(json.dumps({"role": "user", "content": user_prompt}) + "\n")
            f.write(json.dumps({"role": "effective_user", "content": effective_user}) + "\n")

        tech_cfg = Path(self.args.tech_cfg)
        bound_scs = self.run_dir / f"ota_netlist_{run_id}.scs"
        self.spectre.tech_bind(raw_scs, bound_scs, tech_cfg)

        quick_log = self.run_dir / "quick_ota.log"
        quick_psf = self.run_dir / "quick_ota_psf"
        self.spectre.run_spectre(bound_scs, quick_log, quick_psf, allow_fail=True)

        metrics = self.spectre.parse_results(quick_psf)
        summary = {
            "design": "ota",
            "model": self.args.model,
            "run_dir": str(self.run_dir),
            "token_count": token_count,
            "raw_scs": str(raw_scs),
            "bound_scs": str(bound_scs),
            "quick_log": str(quick_log),
            "quick_psf": str(quick_psf),
            "metrics": metrics,
        }

        out_json = self.run_dir / "summary_ota.json"
        out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        if self.args.run_sweep:
            self._run_sweep(bound_scs)

        print(f"RUN_DIR={self.run_dir}")
        print(f"WROTE RAW OTA DECK: {raw_scs}")
        print(f"WROTE OTA NETLIST: {bound_scs}")
        print(f"WROTE SUMMARY: {out_json}")

        return 1

    def _run_sweep(self, bound_scs):
        """Run OTA sweep script."""
        import subprocess

        env = os.environ.copy()
        env["AC_PRIMARY_PARSER"] = "psf_ac_parser"
        env["TEMPLATE_NETLIST"] = str(bound_scs)
        env["GEN_RUNS_DIR"] = str(self.output_dir / "gen_runs")
        env["SWEEP_RESULTS_DIR"] = str(self.output_dir / "sweep_results")
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
    parser.add_argument("--tech_cfg", default=os.getenv("TECH_CFG", "config/tech_config.yml"))
    parser.add_argument("--run_sweep", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    pipeline = OTAPipeline(args)
    sys.exit(0 if pipeline.run() else 1)

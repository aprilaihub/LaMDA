"""Lightweight pytest pipeline tests with one test item per pipeline step."""

import argparse
import json
import os
import sys
import tempfile

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from EDA_Interface.Vivado import VivadoInterface
from Evaluation.ResBench.Analysis.ResBenchAnalysis import ReportParser
from LLM_Interface.LLMClient import LLMClient
from utils import (
    SYSTEM_PROMPT,
    DESIGN_PROMPT,
    check_simulation_passed,
    create_outputs_folder,
    extract_design_info,
    extract_script,
)


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))

TESTBENCH_PROMPT = (
    "Considering the input design, provide these outputs, using these markers in the response: "
    "Verilog testbench: start marker '// Start Verilog Testbench', end marker '// End Verilog Testbench'. "
    "Please add timescale 1ns/1ps at the beginning of the testbench. "
    "Top module name must be the same as the design name + '_tb'. "
    "For each test case, save inputs and outputs in a text file named 'logic_sim.txt' as hex values. "
    "Stop the simulation after the last test case. "
    "Define the integer file variable outside the process. "
    "Do not use dump .vcd file generation. "
    "Define variables outside processes. Run clock only during the stimuli process, not forever "
    "The syntax of the top module is module module_name_tb();"
)

CHECKER_PROMPT = (
    "Considering the input testbench, provide these outputs, using these markers in the response: "
    "Python checker: start marker '// Start Python Checker', end marker '// End Python Checker'. "
    "Python has to manage any delay from registers (for example, if there is a pipeline stage, the results "
    "associated to the i-th input appear at i+1-th clock cycle). "
    "Data from the simulator are in hex format. "
    "Please manage overflow and underflow of the data. "
    "Python has to manage X states from the simulator properly to avoid data conflicts: "
    "If an 'X' state is found in the output, replace it with 0. "
    "Do the checks and report the results in the console if 'verbose' is set to True. "
    "If a computing error is found, do not interrupt the execution of the checker. "
    "The function name must be the same as the design name + '_checker'. "
    "This function take two arguments: first is the text file and second is the argument 'verbose'. "
    "Please do not include any main in this python file."
)

CONSTRAINTS_PROMPT = (
    "Considering the input design, provide these outputs, using these markers in the response: "
    "Constraints file (.xdc): start marker '// Start Constraints', end marker '// End Constraints'. "
    "Only clock information. No comments in the constraints file. "
    "For now, constraint the clock signal only as indicated by the user."
)

RECOMMENDATION_PROMPT = (
    "Considering the implementation reports, summarize key insights and provide possible optimization suggestions. "
    "start marker '// Start Recommendation', end marker '// End Recommendation'. "
    "Please summarize the timing, utilization, power and log status. "
    "If any problems are found (e.g. negative slack, critical warnings), suggest fixes."
)


def _parse_bool_env(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _runtime_config():
    return {
        "model": os.getenv("TEST_MODEL", "gpt-4o"),
        "max_tokens": int(os.getenv("TEST_MAX_TOKENS", "3000")),
        "temperature": float(os.getenv("TEST_TEMPERATURE", "1.0")),
        "top_p": float(os.getenv("TEST_TOP_P", "1.0")),
        "fpga_part": os.getenv("TEST_FPGA_PART", "xc7z020clg400-1"),
        "verbose": _parse_bool_env("TEST_VERBOSE", default=False),
    }


def _start(step_name):
    print(f"[RUNNING] {step_name}")


def _passed(step_name):
    print(f"[PASSED] {step_name}")


def _generate_and_extract(llm_client, prompt, output_file, start_marker, end_marker):
    cfg = _runtime_config()
    content, _ = llm_client.generate_content(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        max_tokens=cfg["max_tokens"],
        temperature=cfg["temperature"],
        top_p=cfg["top_p"],
    )

    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        extract_script(tmp_path, output_file, start_marker, end_marker, verbose=False)
    finally:
        os.unlink(tmp_path)


def _get_llm_client(ctx):
    if "llm_client" in ctx:
        return ctx["llm_client"]
    try:
        cfg = _runtime_config()
        ctx["llm_client"] = LLMClient(cfg["model"])
    except Exception as exc:
        pytest.skip(f"LLM setup unavailable: {exc}")
    return ctx["llm_client"]


def _get_vivado(ctx):
    if "vivado" in ctx:
        return ctx["vivado"]
    try:
        ctx["vivado"] = VivadoInterface()
    except Exception as exc:
        pytest.skip(f"Vivado unavailable: {exc}")
    return ctx["vivado"]


@pytest.fixture(scope="class")
def pipeline_ctx(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("pipeline")
    outputs_dir = tmp_path / "Outputs"
    design_files_dir = outputs_dir / "Design_Files"
    vivado_project_dir = outputs_dir / "vivado_project"
    vivado_logs_dir = outputs_dir / "vivado_logs"
    vivado_reports_dir = vivado_project_dir / "reports"

    create_outputs_folder(
        str(design_files_dir),
        str(vivado_project_dir),
        str(vivado_logs_dir),
        str(vivado_reports_dir / "Synthesis"),
        str(vivado_reports_dir / "Implementation"),
    )

    return {
        "outputs_dir": str(outputs_dir),
        "design_files_dir": str(design_files_dir),
        "vivado_project_dir": str(vivado_project_dir),
        "vivado_logs_dir": str(vivado_logs_dir),
        "vivado_reports_dir": str(vivado_reports_dir),
        "design_name": None,
        "design_file": None,
        "testbench_file": None,
        "checker_file": None,
        "constraints_file": None,
        "parsed_reports_file": None,
    }


class TestPipelineFlow:
    def test_01_design_generation(self, pipeline_ctx):
        step = "1. design_generation"
        _start(step)

        prompt_file = os.path.join(TESTS_DIR, "example_prompt.txt")
        assert os.path.exists(prompt_file)
        with open(prompt_file, "r") as f:
            prompt_content = f.read()

        llm_client = _get_llm_client(pipeline_ctx)
        cfg = _runtime_config()
        content, _ = llm_client.generate_content(
            prompt=f"{prompt_content}\n\n{DESIGN_PROMPT}",
            system_prompt=SYSTEM_PROMPT,
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            top_p=cfg["top_p"],
        )

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            design_name = extract_design_info(tmp_path)
            design_file = os.path.join(pipeline_ctx["design_files_dir"], f"{design_name}.v")
            extract_script(tmp_path, design_file, "// Start Verilog Design\n", "\n// End Verilog Design", verbose=False)
        finally:
            os.unlink(tmp_path)

        assert os.path.exists(design_file)
        pipeline_ctx["design_name"] = design_name
        pipeline_ctx["design_file"] = design_file
        _passed(step)

    def test_02_testbench_generation(self, pipeline_ctx):
        step = "2. testbench_generation"
        _start(step)

        if not pipeline_ctx["design_file"]:
            pytest.skip("Step 1 not completed")

        with open(pipeline_ctx["design_file"], "r") as f:
            design_content = f.read()

        llm_client = _get_llm_client(pipeline_ctx)
        testbench_file = os.path.join(pipeline_ctx["design_files_dir"], f"{pipeline_ctx['design_name']}_tb.v")
        _generate_and_extract(
            llm_client,
            f"{design_content}\n\n{TESTBENCH_PROMPT}",
            testbench_file,
            "// Start Verilog Testbench\n",
            "\n// End Verilog Testbench",
        )

        assert os.path.exists(testbench_file)
        pipeline_ctx["testbench_file"] = testbench_file
        _passed(step)

    def test_03_checker_generation(self, pipeline_ctx):
        step = "3. checker_generation"
        _start(step)

        if not pipeline_ctx["testbench_file"]:
            pytest.skip("Step 2 not completed")

        with open(pipeline_ctx["testbench_file"], "r") as f:
            testbench_content = f.read()

        llm_client = _get_llm_client(pipeline_ctx)
        checker_file = os.path.join(pipeline_ctx["design_files_dir"], f"{pipeline_ctx['design_name']}_checker.py")
        _generate_and_extract(
            llm_client,
            f"{testbench_content}\n\n{CHECKER_PROMPT}",
            checker_file,
            "// Start Python Checker\n",
            "\n// End Python Checker",
        )

        assert os.path.exists(checker_file)
        pipeline_ctx["checker_file"] = checker_file
        _passed(step)

    def test_04_constraints_generation(self, pipeline_ctx):
        step = "4. constraints_generation"
        _start(step)

        if not pipeline_ctx["design_file"]:
            pytest.skip("Step 1 not completed")

        with open(pipeline_ctx["design_file"], "r") as f:
            design_content = f.read()

        llm_client = _get_llm_client(pipeline_ctx)
        constraints_file = os.path.join(pipeline_ctx["design_files_dir"], f"{pipeline_ctx['design_name']}.xdc")
        _generate_and_extract(
            llm_client,
            f"{design_content}\n\n{CONSTRAINTS_PROMPT}",
            constraints_file,
            "// Start Constraints\n",
            "\n// End Constraints",
        )

        assert os.path.exists(constraints_file)
        pipeline_ctx["constraints_file"] = constraints_file
        _passed(step)

    def test_05_simulation(self, pipeline_ctx):
        step = "5. simulation"
        _start(step)

        if not pipeline_ctx["design_file"] or not pipeline_ctx["testbench_file"]:
            pytest.skip("Steps 1-2 not completed")

        vivado = _get_vivado(pipeline_ctx)
        cfg = _runtime_config()
        vivado.run_vivado(
            project_name=pipeline_ctx["design_name"],
            project_dir=pipeline_ctx["vivado_project_dir"],
            log_path=pipeline_ctx["vivado_logs_dir"],
            design_files_dir=pipeline_ctx["design_files_dir"],
            fpga_part=cfg["fpga_part"],
            mode="sim",
            verbose=cfg["verbose"],
        )

        sim_log = os.path.join(pipeline_ctx["vivado_logs_dir"], f"{pipeline_ctx['design_name']}_sim.log")
        assert os.path.exists(sim_log)
        assert check_simulation_passed(sim_log, verbose=cfg["verbose"])
        _passed(step)

    def test_06_synthesis(self, pipeline_ctx):
        step = "6. synthesis"
        _start(step)

        if not pipeline_ctx["design_file"]:
            pytest.skip("Step 1 not completed")

        vivado = _get_vivado(pipeline_ctx)
        cfg = _runtime_config()
        vivado.run_vivado(
            project_name=pipeline_ctx["design_name"],
            project_dir=pipeline_ctx["vivado_project_dir"],
            log_path=pipeline_ctx["vivado_logs_dir"],
            design_files_dir=pipeline_ctx["design_files_dir"],
            fpga_part=cfg["fpga_part"],
            mode="synth",
            verbose=cfg["verbose"],
        )
        _passed(step)

    def test_07_implementation(self, pipeline_ctx):
        step = "7. implementation"
        _start(step)

        if not pipeline_ctx["design_file"]:
            pytest.skip("Step 1 not completed")

        vivado = _get_vivado(pipeline_ctx)
        cfg = _runtime_config()
        vivado.run_vivado(
            project_name=pipeline_ctx["design_name"],
            project_dir=pipeline_ctx["vivado_project_dir"],
            log_path=pipeline_ctx["vivado_logs_dir"],
            design_files_dir=pipeline_ctx["design_files_dir"],
            fpga_part=cfg["fpga_part"],
            mode="impl",
            verbose=cfg["verbose"],
        )
        _passed(step)

    def test_08_parse_reports(self, pipeline_ctx):
        step = "8. parse_reports"
        _start(step)

        parser = ReportParser()
        impl_reports_dir = os.path.join(pipeline_ctx["vivado_reports_dir"], "implementation")
        parsed_reports = parser.batch_parse(impl_reports_dir)
        parsed_logs = parser.batch_parse(pipeline_ctx["vivado_logs_dir"])
        parsed_all = {**parsed_reports, **parsed_logs}

        parsed_reports_file = os.path.join(pipeline_ctx["outputs_dir"], "parsed_reports.json")
        with open(parsed_reports_file, "w") as f:
            json.dump(parsed_all, f, indent=4)

        assert os.path.exists(parsed_reports_file)
        pipeline_ctx["parsed_reports_file"] = parsed_reports_file
        _passed(step)

    def test_09_recommendations(self, pipeline_ctx):
        step = "9. recommendations"
        _start(step)

        if not pipeline_ctx["parsed_reports_file"]:
            pytest.skip("Step 8 not completed")

        with open(pipeline_ctx["parsed_reports_file"], "r") as f:
            parsed_content = json.dumps(json.load(f), indent=2)

        llm_client = _get_llm_client(pipeline_ctx)
        cfg = _runtime_config()
        rec_content, _ = llm_client.generate_content(
            prompt=f"Here are the parsed implementation reports:\n\n{parsed_content}\n\n{RECOMMENDATION_PROMPT}",
            system_prompt=SYSTEM_PROMPT,
            max_tokens=cfg["max_tokens"],
            temperature=cfg["temperature"],
            top_p=cfg["top_p"],
        )

        start_marker = "// Start Recommendation"
        end_marker = "// End Recommendation"
        start_index = rec_content.find(start_marker)
        end_index = rec_content.find(end_marker, start_index)
        assert start_index != -1 and end_index != -1

        recommendations = rec_content[start_index + len(start_marker):end_index].strip()
        recommendations_file = os.path.join(pipeline_ctx["outputs_dir"], "recommendations.txt")
        with open(recommendations_file, "w") as f:
            f.write(recommendations)

        assert os.path.exists(recommendations_file)
        assert len(recommendations) > 0
        _passed(step)


def _parse_cli_args():
    parser = argparse.ArgumentParser(description="Run the test pipeline with configurable runtime parameters.")
    parser.add_argument("--model", default="gpt-4o", help="LLM model to use for generation.")
    parser.add_argument("--max_tokens", type=int, default=3000, help="Max tokens for LLM generation.")
    parser.add_argument("--temperature", type=float, default=1.0, help="Sampling temperature for LLM generation.")
    parser.add_argument("--top_p", type=float, default=1.0, help="Top-p value for LLM generation.")
    parser.add_argument("--fpga_part", default="xc7z020clg400-1", help="Target FPGA part number.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose Vivado/checker logs.")
    return parser.parse_known_args()


if __name__ == "__main__":
    args, extra_pytest_args = _parse_cli_args()

    os.environ["TEST_MODEL"] = args.model
    os.environ["TEST_MAX_TOKENS"] = str(args.max_tokens)
    os.environ["TEST_TEMPERATURE"] = str(args.temperature)
    os.environ["TEST_TOP_P"] = str(args.top_p)
    os.environ["TEST_FPGA_PART"] = args.fpga_part
    os.environ["TEST_VERBOSE"] = "1" if args.verbose else "0"

    pytest_args = [__file__, "-v"]
    if args.verbose:
        pytest_args.append("-s")
    pytest_args.extend(extra_pytest_args)
    raise SystemExit(pytest.main(pytest_args))

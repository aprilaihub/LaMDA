# LaMDA

**Language Model-Assisted Electronic Design Automation Framework**

LaMDA is a modular framework that leverages Large Language Models (LLMs) to automate end-to-end Electronic Design Automation (EDA) workflows across multiple domains. Each feature-specific module combines LLM-driven artifact generation and LLM-assisted optimization recommendations with domain-standard tools for simulation, synthesis, evaluation, and reporting.

---

## 🎯 Overview

LaMDA addresses the challenge of design-space exploration and workflow automation by combining natural language interfaces with deterministic EDA backends. Across its modules, the framework typically:

1. **Reads** a user design request and structured constraints
2. **Builds** an effective prompt from user intent and machine-readable guidance
3. **Generates** domain-specific artifacts (e.g., netlists, RTL, test scripts)
4. **Binds/Adapts** generated artifacts to local technology/tool requirements
5. **Executes** tool flows (simulation, synthesis, implementation, analysis)
6. **Parses** tool outputs into structured metrics
7. **Produces** LLM-assisted optimization recommendations from parsed tool results
8. **Reports** reproducible artifacts, logs, and summaries for evaluation

Current LaMDA features in this repository:

- **LaMDA-Analog**: `LLM_Interface/` generates Spectre decks and proposes/recommends next sweep points, `EDA_Interface/` binds tech + runs Spectre + parses PSF, and `Evaluation/` orchestrates inverter/OTA pipelines.
- **LaMDA-FPGA**: `LLM_Interface/` supports design/code generation prompts, `EDA_Interface/` executes Vivado flows, and `Tests/` + `Evaluation/` parse reports and generate LLM-backed optimization recommendations.
- **LaMDA-RF**: `LLM_Interface/` selects libraries and generates iterative feedback, `EDA_Interface/` drives ADS simulation, and `Evaluation/Run.py` closes the loop with multi-iteration refinement based on simulation summaries.

---

## 📦 Repository Layout

```text
LaMDA/
├── README.md            # Top-level overview (this file)
├── LaMDA-Analog/        # Analog AMS automation
├── LaMDA-FPGA/          # FPGA design automation
└── LaMDA-RF/            # RF design automation
```

Each subfolder is independently runnable and includes its own:

- `README.md` (feature-specific setup and usage)
- environment/bootstrap scripts
- EDA interface modules
- LLM interface modules
- evaluation and test flows

---

## 🚀 Quick Start

### Prerequisites (General)

- **Python**: 3.9+ recommended
- **API Keys**: OpenAI and/or Gemini depending on chosen model/provider
- **EDA Tools** (feature-dependent):
  - Cadence Spectre for Analog
  - AMD Vivado for FPGA
  - Keysight ADS for RF
- **Designer Responsibility**: It is the responsibility of the designer to ensure the required EDA tools are correctly installed, licensed, and accessible on their own server/workstation.
- **Operating System**:
  - Analog/FPGA flows are Linux-oriented
  - RF flow is currently Windows-oriented

### Step 1: Choose a Feature

```bash
cd LaMDA-Analog
# or
cd LaMDA-FPGA
# or
cd LaMDA-RF
```

### Step 2: Follow the Feature README

Each feature README documents:

- exact dependencies and tool versions
- environment variable requirements
- configuration templates
- Makefile/script commands
- output locations and troubleshooting

---

## 🔨 Automation Patterns

LaMDA features expose automation through one or more of:

- **Makefile targets** (common in Analog and FPGA)
- **Shell scripts** (e.g., `run_analog.sh`)
- **PowerShell scripts** (e.g., RF `run.ps1`)
- **Python entrypoints** for pipeline orchestration

Common configurable parameters across flows include:

- `MODEL`
- `MAX_TOKENS`
- `TEMPERATURE`
- `TOP_P`

Some features also include domain-specific options such as FPGA part numbers, sweep toggles, or iteration count.

---

## 📂 Outputs (Generalized)

Output paths are feature-specific, but most pipelines emit:

- generated design artifacts (netlists/RTL/scripts)
- tool logs (simulation/synthesis/implementation)
- parsed summaries (`.json`, `.csv`)
- optional chat transcripts and recommendation artifacts
- optional sweep/iteration result directories

Refer to each feature README for exact directory conventions.

---

## 🔍 Troubleshooting (Cross-Feature)

| Issue | Typical Fix |
|------|-------------|
| API key not found | Export required provider key(s) in shell or `.env` |
| EDA binary not found | Add tool path to `PATH` or set project-specific env var |
| Missing local tech/config file | Copy example config and fill local paths |
| Pipeline runs but fails in tool stage | Inspect generated logs first (simulation/synthesis outputs) |
| Python dependency errors | Recreate venv and reinstall `requirements.txt` |

---

## 📚 Feature Docs

- `LaMDA-Analog/README.md`
- `LaMDA-FPGA/README.md`
- `LaMDA-RF/README.md`

---

## 📚 Citation

If you use LaMDA in your research, please cite:

```bibtex
@misc{sestito2026LaMDA,
  title={A flexible language model-assisted electronic design automation framework},
  author={Cristian Sestito and Panagiota Kontou and Pratibha Verma and Atish Dixit and Alexandros D. Keros and Michael O'Boyle and Christos-Savvas Bouganis and Themis Prodromakis},
  year={2026},
  eprint={2601.14098},
  archivePrefix={arXiv},
  primaryClass={eess.SY},
  url={https://arxiv.org/abs/2601.14098}
}
```

---

## 🔗 Stay Updated

For ongoing updates on LaMDA research, please see:

https://www.april.ac.uk/resources/resources/language-model-assisted-electronic-design-automation/

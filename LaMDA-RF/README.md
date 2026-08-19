# LaMDA-RF

**Language Model-Assisted Electronic Design Automation for RF Circuit Design**

LaMDA-RF is an automated framework that leverages Large Language Models (LLMs) to generate, simulate, and iteratively refine RF circuit designs from natural language descriptions. The tool integrates LLM-based netlist generation with Keysight ADS to create a complete end-to-end pipeline for RF design and evaluation.

---

## 🎯 Overview

LaMDA-RF addresses the challenge of RF circuit design automation by combining the natural language understanding capabilities of modern LLMs with the precision of industry-standard simulation tools. The framework automatically:

1. **Reads** a natural language design request from `Evaluation/prompt.txt`
2. **Selects** relevant ADS component libraries based on the design type
3. **Generates** an ADS netlist from the user request and library context
4. **Simulates** the netlist in Keysight ADS via a dedicated subprocess
5. **Extracts** simulation results (S-parameters) and identifies errors
6. **Feeds** results back to the LLM and requests a refined design
7. **Repeats** steps 3–6 for the configured number of iterations

---

## 🚀 Installation

### Prerequisites

- **Python**: 3.x
- **Keysight ADS**: 2025 or 2026 (sets `HPEESOF_DIR` environment variable)
- **ADS Python venv**: at `~/ads2026_venv` (or set `ADS_VENV_PYTHON`)
- **Operating System**: Windows (tested)
- **API Key**: for an OpenAI-compatible LLM endpoint, or an OpenRouter API key for OpenRouter-hosted models

### Step 1: Access the Repository

```powershell
cd LaMDA-RF
```

### Step 2: Set Up Python Environment

```powershell
# Create virtual environment and install dependencies
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 2b: Create an ADS-Based Python Virtual Environment

LaMDA-RF relies on an ADS Python venv to drive simulations. It is recommended to create this venv using the Python interpreter shipped with ADS so that ADS built-in packages are available.

> **Note:** Virtual environments created in one version of ADS may not work in another. Create a new venv for each ADS version you use.

#### Prerequisites

- `HPEESOF_DIR` must point to your ADS installation (set automatically by ADS).
- On Windows, `%HOME%` is not set by default. Set it to your user directory first:

```powershell
$env:HOME = $env:USERPROFILE
```

#### Create the venv

**Windows:**
```powershell
& "$env:HPEESOF_DIR\tools\python\python" -m venv --system-site-packages "$env:HOME\ads_venv"
```

**Linux:**
```bash
$HPEESOF_DIR/tools/python/bin/python3 -m venv --system-site-packages $HOME/ads_venv
```

The resulting venv path (`~/ads_venv`) should match the `ADS_VENV_PYTHON` environment variable described in Step 3.

---

### Step 3: Configure Environment Variables

Create a `.env` file in the project root:

```
LLM_API_KEY=your_api_key_here
HPEESOF_DIR=C:\Program Files\Keysight\ADS2026_Update1.2
```

Optional overrides:

```
LLM_BASE_URL=https://your-llm-endpoint
ADS_VENV_PYTHON=C:\path\to\ads2026_venv\Scripts\python.exe
```

OpenRouter option:

```
OPENROUTER_API_KEY=your_openrouter_key_here
LLM_PROVIDER=openrouter
LLM_BASE_URL=https://openrouter.ai/api/v1

# Optional: without LLM_PROVIDER, model names in provider/model format also route to OpenRouter
# Example model: openrouter/meta-llama/llama-3.1-8b-instruct
```

### Step 4: Generate ADS Component Libraries and Reference Netlist

LaMDA-RF uses pre-exported `.txt` files in `Data/ADS-Book/libraries/` to give
the LLM context about available ADS components, and a reference `Netlist.txt`
as an example for netlist generation. Neither is committed to the repository —
both must be generated from your local ADS installation.

**Libraries:** Run the script from `Data/ADS-Book/HOW_TO_GET_LIBRARIES.txt` and copy the
output files to `Data/ADS-Book/libraries/`.

**Reference Netlist:** Run the script from `Data/ADS-Book/HOW_TO_GET_NETLIST.txt`
and copy the output `Netlist.txt` to `Data/ADS-Book/Netlist.txt`.

> See the respective `HOW_TO_GET_*.txt` files for full instructions.

### Step 5: Verify Installation

```powershell
# Check Python dependencies
python -c "import openai, pandas, matplotlib; print('Dependencies OK')"

# Check environment variables and ADS setup
.\run.ps1 check_env
```

---

## 🔨 PowerShell Automation

LaMDA-RF provides PowerShell `run.ps1` scripts at multiple levels. Each script automatically loads the `.env` file from the project root before running.

### Root Script (Environment Setup)

Located at the project root. Covers configuration checks and dependency management.

#### Available Commands:

| Command | Description | Usage |
|---------|-------------|-------|
| `check_env` | Verify environment variables are set | `.\run.ps1 check_env` |
| `freeze_deps` | Save current dependencies to requirements.txt | `.\run.ps1 freeze_deps` |
| `clean_env` | Remove virtual environment | `.\run.ps1 clean_env` |
| `help` | Display help message | `.\run.ps1 help` |

#### Quick Start Examples:

```powershell
# Check environment variables
.\run.ps1 check_env

# Update requirements.txt after installing new packages
.\run.ps1 freeze_deps
```

---

### Tests Script (Smoke Testing)

Located in `Tests/`, this script runs a single-iteration smoke test to verify the full stack.

#### Available Commands:

| Command | Description | Usage |
|---------|-------------|-------|
| `run_test` | Run the single-iteration test pipeline | `.\run.ps1 run_test` |
| `clean` | Remove test output folders | `.\run.ps1 clean` |
| `help` | Display help message | `.\run.ps1 help` |

#### Quick Start Examples:

```powershell
cd Tests

# Run smoke test with o3
.\run.ps1 run_test -Model o3

# Run smoke test with an OpenRouter model
.\run.ps1 run_test -Model openrouter/meta-llama/llama-3.1-8b-instruct

# Run with custom parameters
.\run.ps1 run_test -Model o3 -Temperature 1.0 -VerboseOutput
```

#### Parameters:

- `-Model` — LLM model name (default: `o3`)
- `-Temperature` — Sampling temperature (default: `1.0`)
- `-TopP` — Top-p sampling parameter (default: `1.0`)
- `-VerboseOutput` — Print LLM responses to console

---

### Evaluation Script (Full Pipeline)

Located in `Evaluation/`, this script runs the full iterative LaMDA-RF pipeline on a user-supplied prompt.

#### Available Commands:

| Command | Description | Usage |
|---------|-------------|-------|
| `run_custom` | Run the full design pipeline | `.\run.ps1 run_custom -Model o3` |
| `organize_outputs` | Archive outputs to `runs\<Model>\` | `.\run.ps1 organize_outputs -Model o3` |
| `clean_outputs` | Remove all generated output folders | `.\run.ps1 clean_outputs` |
| `help` | Display help message | `.\run.ps1 help` |

#### Quick Start Examples:

> **Tip:** `Data/example_prompts.txt` contains ready-to-use design requests covering a range of RF circuits, including antennas, couplers, and filters. Copy any of them into `Evaluation/prompt.txt` to get started quickly.

```powershell
cd Evaluation

# Write your design request first
# Edit Evaluation/prompt.txt

# Run with o3, 5 iterations
.\run.ps1 run_custom -Model o3 -Iterations 5

# Run with an OpenRouter model, 5 iterations
.\run.ps1 run_custom -Model openrouter/meta-llama/llama-3.1-8b-instruct -Iterations 5

# Run with all options
.\run.ps1 run_custom -Model o3 -Iterations 5 -Temperature 1.0 -TopP 1.0 -VerboseOutput

# Archive outputs for later comparison
.\run.ps1 organize_outputs -Model o3

# Clean all outputs
.\run.ps1 clean_outputs
```

#### Parameters:

- `-Model` — LLM model name (default: `o3`)
- `-Iterations` — Number of design refinement iterations (default: `5`)
- `-Temperature` — Sampling temperature (default: `1.0`)
- `-TopP` — Top-p sampling parameter (default: `1.0`)
- `-VerboseOutput` — Print LLM responses to console

---

## 📂 Output

Each run produces the following folders in the project root (cleared automatically at the start of each run):

| Folder | Contents |
|--------|----------|
| `NetlistFiles/` | Generated netlists (`netlist_1.txt`, `netlist_2.txt`, …) |
| `LLM_Responses/` | Raw LLM responses per iteration |
| `LLM_Feedback/` | Feedback messages sent back to the LLM |
| `Outputs/ADS_outputs/` | ADS simulation output logs |
| `ADS_Workspaces/` | ADS workspace files created during simulation |

---

## 📁 Project Structure

```
LaMDA-RF/
├── .env                          # Environment variables (not committed)
├── requirements.txt              # Python dependencies
├── utils.py                      # Shared utilities and system prompt
├── run.ps1                       # Root helper script (check_env, freeze_deps, clean_env)
│
├── Data/
│   ├── example_prompts.txt           # Design request prompts
│   └── ADS-Book/
│       ├── Netlist.txt               # Reference netlist example
│       ├── HOW_TO_GET_LIBRARIES.txt  # Instructions to export ADS library files
│       ├── HOW_TO_GET_NETLIST.txt    # Instructions to export a reference netlist
│       └── libraries/                # ADS component library definitions (not committed)
│
├── EDA_Interface/
│   ├── ADS.py                    # ADS subprocess interface
│   └── ads_runner/
│       └── main.py               # ADS simulation entry point (runs inside ADS venv)
│
├── LLM_Interface/
│   ├── LLMClient.py              # OpenAI-compatible LLM client
│   ├── library_selector_utils.py # Library selection from design request
│   ├── requirements_utils.py     # Design requirement extraction and feedback generation
│   └── workspace_data_utils.py   # CSV workspace data utilities
│
├── Evaluation/
│   ├── Run.py                    # Main evaluation pipeline
│   ├── run.ps1                   # Evaluation PowerShell runner
│   └── prompt.txt                # Your design request goes here
│
└── Tests/
    ├── Run_Test.py               # Single-iteration test pipeline
    ├── run.ps1                   # Test PowerShell runner
    └── prompt.txt                # Test prompt
```

---
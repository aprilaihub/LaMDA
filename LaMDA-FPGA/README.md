# LaMDA-FPGA

**Language Model-Assisted Electronic Design Automation for FPGA Design and Analysis**

LaMDA-FPGA is an automated framework that leverages Large Language Models (LLMs) to generate, verify, and optimize FPGA designs from natural language descriptions. The tool integrates LLM-based code generation with industry-standard EDA tools (AMD Vivado) to create a complete end-to-end pipeline for FPGA development and evaluation.

---

## 🎯 Overview

LaMDA-FPGA addresses the challenge of FPGA design automation by combining the natural language understanding capabilities of modern LLMs with the precision of hardware synthesis tools. The framework automatically:

1. **Generates** Verilog RTL designs from natural language specifications
2. **Creates** testbenches and Python verification checkers
3. **Synthesizes** constraints files (XDC) for timing and resource optimization
4. **Executes** complete EDA workflows (simulation, synthesis, implementation)
5. **Analyzes** results and provides optimization recommendations
6. **Benchmarks** LLM performance across diverse hardware design categories

The tool supports multiple LLM providers (OpenAI GPT models, Google Gemini, Anthropic Claude, and OpenRouter/ELM including DeepSeek models) and provides comprehensive metrics for both LLM performance and FPGA resource utilization.

LLM outputs are normalized by the Python extractor, which strips Markdown fences and applies compatibility fallbacks for minor formatting differences to reduce model-specific breakage.

For benchmark evaluation using the ResBench dataset, see [Evaluation/ResBench/README.md](Evaluation/ResBench/README.md).

For LLM-driven RTL transformation of existing designs (fewer LUT/FF/DSP/BRAM or higher Fmax at the same latency, with Vivado feedback after every attempt), see [Evaluation/FPGA-RTL-Transform/README.md](Evaluation/FPGA-RTL-Transform/README.md).

---

## 🚀 Installation

### Prerequisites

- **Python**: 3.9 or higher
- **AMD Vivado**: 2024.1 or later (with valid license)
- **Operating System**: Linux (tested on Rocky Linux 9.7)
- **API Keys**: OpenAI and/or Google Gemini API keys

### Step 1: Access the Repository

```bash
cd LaMDA-FPGA
```

### Step 2: Set Up Python Environment

```bash
# Create virtual environment, install dependencies, and activate it
source activate.sh

# Or manually:
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3: Configure Environment Variables

Create a `.env` file or export variables:

```bash
# LLM API keys (set the one for your provider)
export OPENAI_API_KEY="your-openai-api-key-here"
export GEMINI_API_KEY="your-gemini-api-key-here"
export ANTHROPIC_API_KEY="your-anthropic-api-key-here"
export OPENROUTER_API_KEY="your-openrouter-api-key-here"
export ELM_API_KEY="your-elm-api-key-here"

# Required: provider (openai, gemini, anthropic, openrouter, elm).
# The model name is passed to the provider unchanged and is never used to pick the provider.
export LLM_PROVIDER="openrouter"

# Optional endpoint override for OpenAI-compatible providers other than openai
export LLM_BASE_URL="https://openrouter.ai/api/v1"

# Vivado Installation Path (Example)
export VIVADO_PATH="/opt/Xilinx/Vivado/2024.1/bin/vivado"
```

### Step 4: Verify Installation

```bash
# Check Python dependencies
python -c "import openai, pandas, matplotlib; print('Dependencies OK')"

# Check Vivado
$VIVADO_PATH -version

# Check environment variables are set
make check_env
```

---

## 🔨 Makefile Automation

LaMDA-FPGA provides Makefile automation at multiple levels:

### Root Makefile (Environment Setup)

Located at the project root. Environment setup (venv creation and dependency installation) is handled by `activate.sh`. The Makefile covers configuration checks and dependency management.

#### Available Targets:

| Target | Description | Usage |
|--------|-------------|-------|
| `check_env` | Verify VIVADO_PATH, LLM_PROVIDER and that an API key is set (OPENAI, GEMINI, ANTHROPIC, OPENROUTER or ELM `_API_KEY`) | `make check_env` |
| `freeze_deps` | Save current dependencies to requirements.txt | `make freeze_deps` |
| `clean_env` | Remove virtual environment | `make clean_env` |
| `help_root` | Display help message | `make help_root` |

#### Quick Start Examples:

```bash
# First-time setup (creates venv, installs deps, activates it, loads .env)
source activate.sh

# Check environment variables
make check_env

# Update requirements.txt after installing new packages
make freeze_deps
```

#### Parameters:

- `PYTHON` - Python executable (default: `python`)
- `VENV_DIR` - Virtual environment directory (default: `venv`)
- `REQ_FILE` - Requirements file (default: `requirements.txt`)

---

### Tests Makefile (Interactive Testing)

Located in `Tests/`, this Makefile automates the interactive testing pipeline.

#### Available Targets:

| Target | Description | Usage |
|--------|-------------|-------|
| `run_test` | Run testing pipeline | `make LLM_PROVIDER=openai MODEL=gpt-4o run_test` |
| `help` | Display help message | `make help` |

#### Quick Start Examples:

```bash
cd Tests

# Run with GPT-4o (default parameters)
make LLM_PROVIDER=openai MODEL=gpt-4o run_test

# Run with Gemini
make LLM_PROVIDER=gemini MODEL=gemini-2.0-flash-exp run_test

# Run with Claude (native Anthropic API)
make LLM_PROVIDER=anthropic MODEL=claude-sonnet-4-5 run_test

# Run with DeepSeek via OpenRouter
make LLM_PROVIDER=openrouter MODEL=deepseek/deepseek-chat run_test

# Run with custom parameters
make LLM_PROVIDER=openai MODEL=gpt-4o MAX_TOKENS=4000 TEMPERATURE=0.7 TOP_P=0.9 run_test

# Enable verbose output
make LLM_PROVIDER=openai MODEL=gpt-4o VERBOSE=1 run_test
```

#### Parameters:

- `LLM_PROVIDER` - **Required** (env var or `make` variable): `openai`, `gemini`, `anthropic`, `openrouter` or `elm`
- `MODEL` - LLM model name (default: `gpt-4o`)
- `MAX_TOKENS` - Maximum tokens for generation (default: `3000`)
- `TEMPERATURE` - Sampling temperature (default: `1.0`)
- `TOP_P` - Top-p sampling parameter (default: `1.0`)
- `FPGA_PART` - Target FPGA part number (default: `xc7z020clg400-1`)
- `VERBOSE` - Enable verbose logging (`0`/`1`, default: `0`)

---

### Custom Evaluation Makefile

Located in `Evaluation/Custom/`, this Makefile runs the full LaMDA-FPGA pipeline on a user-supplied prompt.

#### Available Targets:

| Target | Description | Usage |
|--------|-------------|-------|
| `run_custom` | Run the custom pipeline | `make LLM_PROVIDER=openai MODEL=gpt-4o run_custom` |
| `organize_outputs` | Move outputs to a design-specific subfolder | `make organize_outputs` |
| `clean_outputs` | Remove all generated outputs | `make clean_outputs` |
| `help` | Display help message | `make help` |

#### Quick Start Examples:

```bash
cd Evaluation/Custom

# Run with GPT-4o
make LLM_PROVIDER=openai MODEL=gpt-4o run_custom

# Run with DeepSeek via OpenRouter
make LLM_PROVIDER=openrouter MODEL=deepseek/deepseek-chat run_custom

# Run with custom parameters
make LLM_PROVIDER=openai MODEL=gpt-4o MAX_TOKENS=4000 TEMPERATURE=0.7 TOP_P=0.9 run_custom

# Organize outputs into a design-named subfolder
make organize_outputs

# Clean all outputs
make clean_outputs
```

#### Parameters:

- `LLM_PROVIDER` - **Required** (env var or `make` variable): `openai`, `gemini`, `anthropic`, `openrouter` or `elm`
- `MODEL` - **Required**. LLM model name (e.g., `gpt-4o`, `o1`, `gemini-2.0-flash-exp`)
- `MAX_TOKENS` - Maximum tokens for generation (default: `3000`)
- `TEMPERATURE` - Sampling temperature (default: `1.0`)
- `TOP_P` - Top-p sampling parameter (default: `1.0`)
- `FPGA_PART` - Target FPGA part number (default: `xc7z020clg400-1`)

---

### FPGA-RTL-Transform Evaluation Makefile

Located in `Evaluation/FPGA-RTL-Transform/`, this Makefile asks an LLM to rewrite working Verilog designs (systolic arrays, a FIR filter, a 3x3 convolution, a dot-product engine) to use fewer resources or run faster at the same latency. Vivado checks every attempt and feeds the result back. The full description (flow, dataset, judging rule, output layout, options) is in [Evaluation/FPGA-RTL-Transform/README.md](Evaluation/FPGA-RTL-Transform/README.md); `make help` lists every target and variable.

```bash
cd Evaluation/FPGA-RTL-Transform

make help                                  # targets, variables, defaults and valid values
make measure_baselines DESIGN_IDS=1        # Vivado numbers of the reference design (no LLM)
make LLM_PROVIDER=openai MODEL=gpt-4o DESIGN_ID=1 run_rtl_transform
```

---

## 📁 Project Structure

```text
LaMDA-FPGA/
├── activate.sh                 # Creates/activates venv and installs dependencies
├── Makefile                    # Root automation for environment checks and maintenance
├── requirements.txt            # Python dependencies
├── utils.py                    # Shared utility functions
├── EDA_Interface/
│   └── Vivado.py               # Vivado integration and EDA workflow execution
├── LLM_Interface/
│   └── LLMClient.py            # LLM provider abstraction (OpenAI, Gemini, Anthropic, OpenRouter, ELM)
├── Evaluation/
│   ├── Custom/                 # Run full pipeline on user-defined prompts
│   ├── FPGA-RTL-Transform/     # LLM-driven RTL transformation with Vivado feedback
│   └── ResBench/               # Benchmark evaluation pipeline and analysis tools
└── Tests/
  ├── Run_Test.py             # Interactive end-to-end test runner
  └── Makefile                # Test automation targets
```

---


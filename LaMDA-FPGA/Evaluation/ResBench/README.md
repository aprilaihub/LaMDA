# ResBench Evaluation

ResBench provides a standardized benchmark for evaluating LLM performance on hardware design tasks. It consists of 56 curated FPGA design problems across 12 categories.

---

## 🗂️ Dataset

### Categories (56 Problems)

| # | Category | Problems |
|---|----------|----------|
| 1 | Combinatorial Logic | 8 |
| 2 | Finite State Machines | 4 |
| 3 | Mathematical Functions | 5 |
| 4 | Basic Arithmetic | 5 |
| 5 | Bitwise & Logical Ops | 4 |
| 6 | Pipelining | 5 |
| 7 | Polynomial Evaluation | 5 |
| 8 | Machine Learning | 5 |
| 9 | Financial Computing | 4 |
| 10 | Encryption | 3 |
| 11 | Physics | 4 |
| 12 | Climate | 4 |

Each problem includes:
- **Problem Description**: Natural language specification
- **Module Header**: Verilog interface definition
- **Testbench**: Pre-written verification testbench
- **Constraints**: LUT and timing requirements
- **Reference Metrics**: Expected resource utilization

### Preprocessing

Before running evaluations, preprocess the dataset to embed the constraint information:

```bash
make preprocess_dataset
```

This reads `Dataset/Original/problems.json` and writes `Dataset/Preprocessed/problems_preprocessed.json`.

---

## 📊 Evaluation Metrics

#### LLM Metrics
- **Token Count**: Total tokens generated
- **LLM Time**: Time spent in LLM generation (seconds)
- **EDA Time**: Time spent in Vivado (seconds)

#### Verification Metrics
- **Functional Verification**: PASS/FAIL (testbench simulation)
- **Synthesis**: PASS/FAIL (synthesis completion)
- **Implementation**: PASS/FAIL (place & route completion)

#### Resource Metrics
- **LUTs**, **Registers**, **BRAMs**, **DSPs**, **I/O**

#### Timing Metrics
- **Slack**, **Data Path Delay**, **Logic Delay**, **Route Delay** (ns)

#### Power Metrics
- **Total Power**, **Dynamic Power**, **Static Power** (W)

#### Constraint Metrics
- **LUT Constraint**: PASS if LUTs ≤ LUTmin
- **Delay Constraint**: PASS if meets timing or delay requirements

### Example Result Entry

```json
{
  "ID": 1,
  "module": "parity_8bit",
  "llm_model": "gpt-4o",
  "token_count": 1247,
  "llm_time [s]": 3.42,
  "eda_time [s]": 47.15,
  "Functional Verification": "PASS",
  "Synthesis": "PASS",
  "Implementation": "PASS",
  "LUTs": 8,
  "Registers": 0,
  "BRAMs": 0,
  "DSPs": 0,
  "Slack [ns]": 8.234,
  "Data Path Delay [ns]": 1.766,
  "Total Power [W]": 0.085,
  "LUTConstraint": "PASS",
  "DelayConstraint": "PASS"
}
```

---

## 🔨 Makefile Automation

### Available Targets

| Target | Description | Usage |
|--------|-------------|-------|
| `preprocess_dataset` | Preprocess ResBench dataset with constraints | `make preprocess_dataset` |
| `run_llm_eda_resbench` | Run evaluation on ResBench problems | `make MODEL=gpt-4o run_llm_eda_resbench` |
| `json_to_csv` | Convert JSON results to full CSV | `make JSON_FILE=exp.json json_to_csv` |
| `compress_csv` | Compress full CSV to summary | `make compress_csv INPUT_CSV=full_results.csv OUTPUT_CSV=summary_results.csv` |
| `generate_csv` | Generate both full and summary CSV | `make JSON_FILE=exp.json generate_csv` |
| `reorganize_results` | Reorganize experiment results | `make JSON_FILE=exp.json reorganize_results` |
| `clean_experiments_log` | Clean experiment log files | `make clean_experiments_log` |
| `help` | Display help message | `make help` |

### Parameters

- `MODEL` - **Required** for `run_llm_eda_resbench`. LLM model name
- `ID_START` - Starting design ID (default: `1`)
- `ID_END` - Ending design ID (default: `56`)
- `ITER` - Number of iterations per design (default: `1`)
- `JSON_FILE` - JSON results filename (default: `exp_results.json`)
- `INPUT_CSV` - Input CSV filename (default: `full_results.csv`)
- `OUTPUT_CSV` - Output CSV filename (default: `summary_results.csv`)

### Examples

```bash
cd Evaluation/ResBench

# Preprocess dataset
make preprocess_dataset

# Evaluate all 56 problems with GPT-4o (1 iteration each)
make MODEL=gpt-4o run_llm_eda_resbench

# Evaluate a subset with multiple iterations (for statistical significance)
make MODEL=gpt-4o ID_START=1 ID_END=10 ITER=5 run_llm_eda_resbench

# Evaluate a specific category (e.g., Combinatorial Logic: IDs 1-8)
make MODEL=gemini-2.0-flash-exp ID_START=1 ID_END=8 ITER=3 run_llm_eda_resbench

# Generate CSV results from experiment JSON
make JSON_FILE=exp_gpt-4o_20260318_120000.json generate_csv

# Reorganize results into proper directory structure
make JSON_FILE=exp_gpt-4o_20260318_120000.json reorganize_results
```

### Complete Workflow

```bash
cd Evaluation/ResBench

# 1. Preprocess dataset (if constraints changed)
make preprocess_dataset

# 2. Run evaluation (5 iterations per run)
make MODEL=gpt-4o ID_START=1 ID_END=56 ITER=5 run_llm_eda_resbench

# 3. Convert results to CSV
make JSON_FILE=exp_gpt-4o_20260318_120000.json generate_csv

# 4. Reorganize results for archiving
make JSON_FILE=exp_gpt-4o_20260318_120000.json reorganize_results

# 5. Analyze results
cd Analysis
jupyter notebook ResBench_Plots.ipynb
```

---

## 🔍 Analyzing Results

Results are stored under `Analysis/experiments/`. Use `ResBenchAnalysis.py` to parse, aggregate, and visualize them.

```bash
cd Evaluation/ResBench/Analysis

# Convert JSON to CSV
python -c "
from ResBenchAnalysis import PostProcessor
PostProcessor.json_to_csv('exp_results.json', 'full_results.csv')
PostProcessor.compress_csv('full_results.csv', 'summary_results.csv')
"

# Generate visualizations
jupyter notebook ResBench_Plots.ipynb
```

The notebook produces category-level bar plots, box plots, and pass-rate heatmaps.

---

## 📚 References

```bibtex
@inproceedings{guo2025resbench,
  title={ResBench: A Resource-Aware Benchmark for LLM-Generated FPGA Designs},
  author={Guo, Ce and Zhao, Tong},
  booktitle={Proceedings of the 15th International Symposium on Highly Efficient Accelerators and Reconfigurable Technologies},
  pages={25--34},
  year={2025}
}
```
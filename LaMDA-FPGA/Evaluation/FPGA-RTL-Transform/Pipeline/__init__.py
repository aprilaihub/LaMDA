"""
Building blocks of the FPGA-RTL-Transform loop (Run.py keeps only the loop and the CLI):

  flow.py      Vivado flow of one design (simulation -> synthesis -> implementation) and the metrics /
               report digest collected from its reports
  prompts.py   system prompt, optional primers (--knowledge), user prompt, attempt history, edit record
  feedback.py  the note sent back to the LLM after an attempt (--feedback_detail)
  levels.py    the cumulative feedback levels (none < metrics < reports < reports+logs < reports+logs+edits)

The modules import the parsers of Analysis/ and the shared helpers of LaMDA-FPGA/, so both are put on
sys.path here (Run.py and the unit tests only need to import this package).
"""

import os
import sys

TRANSFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAMDA_FPGA_ROOT = os.path.dirname(os.path.dirname(TRANSFORM_DIR))

for _path in (LAMDA_FPGA_ROOT, os.path.join(TRANSFORM_DIR, "Analysis")):
    if _path not in sys.path:
        sys.path.append(_path)

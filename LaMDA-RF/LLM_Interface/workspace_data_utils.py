import csv
import glob
import os
import re
import shutil


def copy_workspace_csvs(base_dir=None, workspace_prefix="my_workspace_", logs_dir=None, data_folder_name="data"):
    """Copy CSV files from ADS workspace data folders into logs_dir."""
    if base_dir is None:
        base_dir = os.path.expanduser("~")
    if logs_dir is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        logs_dir = os.path.join(project_root, "Outputs", "Simulation_CSV_Results")

    os.makedirs(logs_dir, exist_ok=True)

    pattern = os.path.join(base_dir, f"{workspace_prefix}*")
    workspace_dirs = [p for p in glob.glob(pattern) if os.path.isdir(p)]

    copied_files = []
    for workspace_dir in workspace_dirs:
        workspace_name = os.path.basename(workspace_dir)
        iteration_suffix = (
            workspace_name[len(workspace_prefix):]
            if workspace_name.startswith(workspace_prefix)
            else workspace_name
        )
        data_dir = os.path.join(workspace_dir, data_folder_name)
        if not os.path.isdir(data_dir):
            continue
        for csv_path in glob.glob(os.path.join(data_dir, "*.csv")):
            destination = os.path.join(logs_dir, f"iteration_{iteration_suffix}.csv")
            shutil.copy2(csv_path, destination)
            copied_files.append(destination)

    return copied_files


def combine_csv_columns(source_dir=None, output_path=None, skip_header=True):
    """Combine frequency and S[x,y] columns from all iteration CSVs into one file."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if source_dir is None:
        source_dir = os.path.join(project_root, "Outputs", "Simulation_CSV_Results")
    if output_path is None:
        output_path = os.path.join(source_dir, "Combined_Results.csv")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    csv_files = sorted(glob.glob(os.path.join(source_dir, "*.csv")))
    if not csv_files:
        return {"output_path": output_path, "files": 0, "rows": 0}

    sparam_header_pattern = re.compile(r"^s\s*\[\s*\d+\s*,\s*\d+\s*\]$", re.IGNORECASE)
    columns = []
    max_len = 0

    for csv_path in csv_files:
        with open(csv_path, "r", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue

        header_row = rows[0]
        selected_indices = []
        selected_names = []

        for idx, header_name in enumerate(header_row):
            header_text = header_name.strip()
            header_lower = header_text.lower()
            if "freq" in header_lower or sparam_header_pattern.match(header_text):
                selected_indices.append(idx)
                selected_names.append(header_text or f"col{idx}")

        if not selected_indices:
            selected_indices = [0, 1, 3]
            selected_names = ["col1", "col2", "col3"]

        data_rows = rows[1:] if skip_header else rows
        selected_columns = [[] for _ in selected_indices]
        for row in data_rows:
            for col_list, idx in zip(selected_columns, selected_indices):
                col_list.append(row[idx] if len(row) > idx else "")

        base_name = os.path.splitext(os.path.basename(csv_path))[0]
        named_columns = []
        for col_name, values in zip(selected_names, selected_columns):
            named_columns.append((f"{base_name}_{col_name.replace(' ', '')}", values))
            max_len = max(max_len, len(values))
        columns.append(named_columns)

    header = [col_name for named_columns in columns for col_name, _ in named_columns]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for i in range(max_len):
            row_out = [
                values[i] if i < len(values) else ""
                for named_columns in columns
                for _, values in named_columns
            ]
            writer.writerow(row_out)

    return {"output_path": output_path, "files": len(csv_files), "rows": max_len}

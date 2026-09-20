# run_batch.ps1 - LaMDA-RF Batch Runner
# Runs Evaluation/run.ps1 for multiple models/configurations in sequence.
#
# Usage:
#   .\run_batch.ps1
#   .\run_batch.ps1 -Iterations 5 -Temperature 1.0 -TopP 1.0
#
# Edit the $Runs array below to define which models (and params) to run.

param(
    [int]   $Iterations   = 10,
    [float] $Temperature  = 1.0,
    [float] $TopP         = 1.0,
    [switch]$SkipOrganize,    # If set, skips moving outputs to runs\ after each run
    [switch]$VerboseOutput    # If set, passes -VerboseOutput to each evaluation run
)

$ROOT_DIR = $PSScriptRoot
$EvalScript = Join-Path $ROOT_DIR "Evaluation\run.ps1"

# ──────────────────────────────────────────────────────────────────────────────
# Define your runs here. Each entry is a hashtable with:
#   Model       - model name passed to Evaluation/run.ps1
#   Iterations  - (optional) override global Iterations for this run
#   Temperature - (optional) override global Temperature for this run
#   TopP        - (optional) override global TopP for this run
#   Tag         - (optional) suffix appended to the runs\ folder name
# ──────────────────────────────────────────────────────────────────────────────
#$model = "deepseek-chat-v3.1"
# $model = "codestral-2508"
#$model = "gpt-4o-mini"
#$model = "gpt-4o"
$model = "o1"
$name = "antenna_n"
$Runs = @(
    @{ Model = $model; Tag = $name },
    @{ Model = $model; Tag = $name },
    # @{ Model = $model; Tag = $name },
    # @{ Model = $model; Tag = $name },
    @{ Model = $model; Tag = $name }
)
# ──────────────────────────────────────────────────────────────────────────────

$totalRuns   = $Runs.Count
$batchStart  = Get-Date

Write-Host "============================================================"
Write-Host "LaMDA-RF Batch Runner"
Write-Host "Total runs:  $totalRuns"
Write-Host "Iterations:  $Iterations"
Write-Host "Temperature: $Temperature"
Write-Host "Top-p:       $TopP"
Write-Host "Started:     $($batchStart.ToString('HH:mm:ss'))"
Write-Host "============================================================"

for ($i = 0; $i -lt $totalRuns; $i++) {
    $run      = $Runs[$i]
    $model    = $run.Model
    $iters    = if ($run.ContainsKey("Iterations"))  { $run.Iterations }  else { $Iterations }
    $temp     = if ($run.ContainsKey("Temperature")) { $run.Temperature } else { $Temperature }
    $topP     = if ($run.ContainsKey("TopP"))        { $run.TopP }        else { $TopP }
    $tag      = if ($run.ContainsKey("Tag"))         { $run.Tag }         else { "" }

    Write-Host ""
    Write-Host "============================================================"
    Write-Host "Run $($i+1) / $totalRuns  |  Model: $model"
    Write-Host "============================================================"

    # --- run_custom ---
    $evalArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $EvalScript,
        "run_custom",
        "-Model",       $model,
        "-Temperature", $temp,
        "-TopP",        $topP,
        "-Iterations",  $iters
    )
    if ($VerboseOutput) { $evalArgs += "-VerboseOutput" }
    & powershell @evalArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Run $($i+1) ($model) exited with code $LASTEXITCODE. Continuing..."
    }

    # --- organize_outputs ---
    if (-not $SkipOrganize) {
        $runLabel = if ($tag) { "${model}_$($i+1)_$tag" } else { "${model}_$($i+1)" }
        $organizeArgs = @(
            "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", $EvalScript,
            "organize_outputs",
            "-Model", $runLabel
        )
        & powershell @organizeArgs
    }
}

$batchElapsed = (Get-Date) - $batchStart
$batchTime    = ("{0}m {1:D2}s" -f [int]$batchElapsed.TotalMinutes, $batchElapsed.Seconds)

Write-Host ""
Write-Host "============================================================"
Write-Host "Batch complete!  $totalRuns runs finished."
Write-Host "Total time: $batchTime"
Write-Host "============================================================"

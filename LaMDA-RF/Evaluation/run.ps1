# run.ps1 - LaMDA-RF Custom Evaluation Helper Script
# Usage: .\run.ps1 <command> [options]
# Commands: run_custom, clean_outputs, organize_outputs, help

param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    [string]$Model       = "o3",
    [float] $Temperature = 1.0,
    [float] $TopP        = 1.0,
    [int]   $Iterations  = 5,
    [switch]$VerboseOutput
)

$ROOT_DIR = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# Load .env file if it exists
$envFile = Join-Path $ROOT_DIR ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
        }
    }
}

switch ($Command) {
    "run_custom" {
        $startTime = Get-Date
        Write-Host "============================================================"
        Write-Host "Running LaMDA-RF Custom Pipeline"
        Write-Host "Model:       $Model"
        Write-Host "Temperature: $Temperature"
        Write-Host "Top-p:       $TopP"
        Write-Host "Iterations:  $Iterations"
        Write-Host "Started:     $($startTime.ToString('HH:mm:ss'))"
        Write-Host "============================================================"

        $pyArgs = @(
            "-B", "Evaluation/Run.py",
            "--model", $Model,
            "--temperature", $Temperature,
            "--top_p", $TopP,
            "--iterations", $Iterations
        )
        if ($VerboseOutput) { $pyArgs += "--verbose" }

        Push-Location $ROOT_DIR
        try { & python @pyArgs }
        finally { Pop-Location }

        $elapsed = (Get-Date) - $startTime
        $totalTimeStr = ("{0}m {1:D2}s" -f [int]$elapsed.TotalMinutes, $elapsed.Seconds)
        Write-Host "============================================================"
        Write-Host "Custom pipeline completed!"
        Write-Host "Total time: $totalTimeStr"
        Write-Host "============================================================"

        $statsPath = Join-Path $ROOT_DIR "Outputs\LLM_stats.csv"
        if (Test-Path $statsPath) {
            Add-Content -Path $statsPath -Value ""
            Add-Content -Path $statsPath -Value "total_run_time,$totalTimeStr"
        }
    }

    "clean_outputs" {
        Write-Host "Cleaning run outputs..."
        foreach ($dir in @("Outputs", "NetlistFiles", "ADS_Workspaces", "LLM_Feedback", "LLM_Responses")) {
            $path = Join-Path $ROOT_DIR $dir
            if (Test-Path $path) { Remove-Item -Recurse -Force $path }
        }
        Write-Host "Cleaned."
    }

    "organize_outputs" {
        $dest = Join-Path $ROOT_DIR "runs\$Model"
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        foreach ($dir in @("Outputs", "NetlistFiles", "ADS_Workspaces", "LLM_Feedback", "LLM_Responses")) {
            $src = Join-Path $ROOT_DIR $dir
            if (Test-Path $src) {
                Move-Item -Path $src -Destination (Join-Path $dest $dir)
            }
        }
        Write-Host "Outputs moved to runs\$Model\"
    }

    default {
        Write-Host "============================================================"
        Write-Host "LaMDA-RF Custom Evaluation Helper Script"
        Write-Host "============================================================"
        Write-Host ""
        Write-Host "Usage: .\run.ps1 <command> [options]"
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  run_custom             Run the iterative LLM + ADS pipeline"
        Write-Host "  clean_outputs          Remove generated output folders"
        Write-Host "  organize_outputs       Archive outputs to runs\<Model>\"
        Write-Host "  help                   Show this message"
        Write-Host ""
        Write-Host "Options:"
        Write-Host "  -Model <name>          LLM model (default: o1)"
        Write-Host "  -Iterations <n>        Number of LLM iterations (default: 5)"
        Write-Host "  -Temperature <f>       Sampling temperature (default: 1.0)"
        Write-Host "  -TopP <f>              Top-p value (default: 1.0)"
        Write-Host "  -VerboseOutput         Enable verbose output"
        Write-Host ""
        Write-Host "Examples:"
        Write-Host "  .\run.ps1 run_custom"
        Write-Host "  .\run.ps1 run_custom -Model o3 -Iterations 3 -VerboseOutput"
        Write-Host "  .\run.ps1 clean_outputs"
        Write-Host "  .\run.ps1 organize_outputs -Model o3"
    }
}

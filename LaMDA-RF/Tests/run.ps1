# run.ps1 - LaMDA-RF Tests Helper Script
# Usage: .\run.ps1 <command> [options]
# Commands: run_test, clean, help

param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    [string]$Model       = "o3",
    [float] $Temperature = 1.0,
    [float] $TopP        = 1.0,
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
    "run_test" {
        Write-Host "============================================================"
        Write-Host "Running LaMDA-RF Test Pipeline"
        Write-Host "Model: $Model"
        Write-Host "============================================================"

        $args = @(
            "-B", "Tests/Run_Test.py",
            "--model", $Model,
            "--temperature", $Temperature,
            "--top_p", $TopP
        )
        if ($VerboseOutput) { $args += "--verbose" }

        Push-Location $ROOT_DIR
        try { & python @args }
        finally { Pop-Location }

        Write-Host "Test pipeline completed!"
    }

    "clean" {
        Write-Host "Cleaning test outputs..."
        foreach ($dir in @("Outputs", "NetlistFiles", "ADS_Workspaces", "LLM_Feedback", "LLM_Responses")) {
            $path = Join-Path $ROOT_DIR $dir
            if (Test-Path $path) { Remove-Item -Recurse -Force $path }
        }
        Write-Host "Cleaned."
    }

    default {
        Write-Host "============================================================"
        Write-Host "LaMDA-RF Tests Helper Script"
        Write-Host "============================================================"
        Write-Host ""
        Write-Host "Usage: .\run.ps1 <command> [options]"
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  run_test               Run the single-design test pipeline"
        Write-Host "  clean                  Remove generated output folders"
        Write-Host "  help                   Show this message"
        Write-Host ""
        Write-Host "Options:"
        Write-Host "  -Model <name>          LLM model (default: o1)"
        Write-Host "  -Temperature <f>       Sampling temperature (default: 1.0)"
        Write-Host "  -TopP <f>              Top-p value (default: 1.0)"
        Write-Host "  -VerboseOutput         Enable verbose output"
        Write-Host ""
        Write-Host "Examples:"
        Write-Host "  .\run.ps1 run_test"
        Write-Host "  .\run.ps1 run_test -Model o3 -VerboseOutput"
    }
}

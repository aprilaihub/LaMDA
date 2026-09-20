# run.ps1 - LaMDA-RF Root Helper Script
# Usage: .\run.ps1 <command>
# Commands: check_env, freeze_deps, clean_env, help

param(
    [Parameter(Position=0)]
    [string]$Command = "help",
    [string]$VenvDir = "venv",
    [string]$ReqFile = "requirements.txt"
)

$ROOT_DIR = $PSScriptRoot

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
    "check_env" {
        Write-Host "============================================================"
        Write-Host "Checking environment variables"
        Write-Host "============================================================"
        $hpeesof = [System.Environment]::GetEnvironmentVariable("HPEESOF_DIR", "Process")
        $apiKey   = [System.Environment]::GetEnvironmentVariable("LLM_API_KEY",  "Process")
        if ($hpeesof) { Write-Host "HPEESOF_DIR: $hpeesof" } else { Write-Host "HPEESOF_DIR: Not set" }
        if ($apiKey)  { Write-Host "LLM_API_KEY: Set"       } else { Write-Host "LLM_API_KEY: Not set" }
        Write-Host "============================================================"
    }

    "freeze_deps" {
        Write-Host "============================================================"
        Write-Host "Freezing dependencies to $ReqFile"
        Write-Host "============================================================"
        $pip = Join-Path $ROOT_DIR "$VenvDir\Scripts\pip.exe"
        & $pip freeze | Set-Content (Join-Path $ROOT_DIR $ReqFile)
        Write-Host "Dependencies frozen to $ReqFile"
    }

    "clean_env" {
        $venvPath = Join-Path $ROOT_DIR $VenvDir
        if (Test-Path $venvPath) {
            Remove-Item -Recurse -Force $venvPath
            Write-Host "Virtual environment removed."
        } else {
            Write-Host "No virtual environment found at $venvPath"
        }
    }

    default {
        Write-Host "============================================================"
        Write-Host "LaMDA-RF Root Helper Script"
        Write-Host "============================================================"
        Write-Host ""
        Write-Host "Usage: .\run.ps1 <command> [options]"
        Write-Host ""
        Write-Host "Commands:"
        Write-Host "  check_env              Check required environment variables"
        Write-Host "  freeze_deps            Freeze pip dependencies to requirements.txt"
        Write-Host "  clean_env              Remove the Python virtual environment"
        Write-Host "  help                   Show this message"
        Write-Host ""
        Write-Host "Options:"
        Write-Host "  -VenvDir <dir>         Venv directory (default: venv)"
        Write-Host "  -ReqFile <file>        Requirements file (default: requirements.txt)"
    }
}

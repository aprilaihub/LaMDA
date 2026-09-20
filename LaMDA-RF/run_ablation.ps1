# run_ablation.ps1 - Antenna ablation runner (PowerShell only)
#
# Factors:
#   S: system prompt enabled (0/1)
#   A: ADS-book context enabled (0/1)
#   P: prompt type (0=unconstrained, 1=constrained)
#
# Usage examples:
#   .\run_ablation.ps1
#   .\run_ablation.ps1 -Repeats 5 -Iterations 5
#   .\run_ablation.ps1 -ConditionIds ANT_S0_A0_P0 -Models deepseek-chat-v3.1 -Repeats 5

param(
    [string[]]$Models = @("gpt-4o-mini", "gpt-4o", "o1", "codestral-2508", "deepseek-chat-v3.1"),
    [string]$Model = "",
    [string[]]$ConditionIds = @(),
    [string]$ConditionId = "",
    [int]$Repeats = 5,
    [int]$Iterations = 5,
    [float]$Temperature = 1.0,
    [float]$TopP = 1.0,
    [switch]$VerboseOutput,
    [switch]$DryRun,
    [switch]$ListConditions
)

$ROOT_DIR = $PSScriptRoot
$EvalScript = Join-Path $ROOT_DIR "Evaluation\run.ps1"
$ConstrainedPrompt = "prompt_constrained.txt"
$UnconstrainedPrompt = "prompt_unconstrained.txt"

$AblationRoot = Join-Path $ROOT_DIR "runs\ablation"
$ManifestPath = Join-Path $AblationRoot "manifest.csv"
New-Item -ItemType Directory -Force -Path $AblationRoot | Out-Null

if (-not (Test-Path $ManifestPath)) {
    "run_id,timestamp,status,exit_code,S,A,P,provider,model,repeat,iterations,temperature,top_p,prompt_file,archive_path" | Out-File -FilePath $ManifestPath -Encoding utf8
}

# Model families for explicit provider routing.
$OpenAiModels = @("gpt-4o-mini", "gpt-4o")
$OpenRouterModels = @("o1", "codestral-2508", "deepseek-chat-v3.1")

$conditions = @(
    @{ S = 0; A = 0; P = 0; Id = "ANT_S0_A0_P0" },
    @{ S = 0; A = 0; P = 1; Id = "ANT_S0_A0_P1" },
    @{ S = 0; A = 1; P = 0; Id = "ANT_S0_A1_P0" },
    @{ S = 0; A = 1; P = 1; Id = "ANT_S0_A1_P1" },
    @{ S = 1; A = 0; P = 0; Id = "ANT_S1_A0_P0" },
    @{ S = 1; A = 0; P = 1; Id = "ANT_S1_A0_P1" }
)

if ($ListConditions) {
    Write-Host "Available ablation conditions:"
    $conditions | ForEach-Object { Write-Host "- $($_.Id)" }
    exit 0
}

if ($Model.Trim()) {
    $Models = @($Model.Trim())
}

if ($ConditionId.Trim()) {
    $ConditionIds = @($ConditionId.Trim())
}

if ($ConditionIds.Count -gt 0) {
    $requested = @()
    foreach ($item in $ConditionIds) {
        $parts = $item -split ","
        foreach ($part in $parts) {
            $id = $part.Trim()
            if ($id) {
                $requested += $id
            }
        }
    }

    $conditions = $conditions | Where-Object { $requested -contains $_.Id }
    $missing = $requested | Where-Object { -not ($conditions.Id -contains $_) }
    if ($missing.Count -gt 0) {
        Write-Error "Unknown condition id(s): $($missing -join ', ')"
        Write-Host "Use -ListConditions to see valid IDs."
        exit 1
    }
}

if ($conditions.Count -eq 0) {
    Write-Error "No ablation conditions selected."
    Write-Host "Use -ConditionIds with one or more IDs, or omit it to run all default conditions."
    exit 1
}

$totalRuns = $conditions.Count * $Models.Count * $Repeats
$runCounter = 0
$batchStart = Get-Date

Write-Host "============================================================"
Write-Host "LaMDA-RF Antenna Ablation Runner"
Write-Host "Conditions:  $($conditions.Count)"
Write-Host "Models:      $($Models.Count)"
Write-Host "Repeats:     $Repeats"
Write-Host "Total runs:  $totalRuns"
Write-Host "Iterations:  $Iterations"
Write-Host "Temperature: $Temperature"
Write-Host "Top-p:       $TopP"
Write-Host "Dry-run:     $DryRun"
Write-Host "Started:     $($batchStart.ToString('HH:mm:ss'))"
Write-Host "Selected condition IDs: $($conditions.Id -join ', ')"
Write-Host "Selected models:        $($Models -join ', ')"
Write-Host "============================================================"

foreach ($condition in $conditions) {
    foreach ($model in $Models) {
        for ($repeat = 1; $repeat -le $Repeats; $repeat++) {
            $runCounter++
            $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
            $repeatTag = "R{0:D2}" -f $repeat
            $safeModel = ($model -replace "[^a-zA-Z0-9._-]", "_")
            $runId = "$($condition.Id)_${safeModel}_${repeatTag}_${timestamp}"
            $archiveRel = "runs/ablation/$runId"
            $archiveModelArg = "ablation\$runId"
            $promptFile = if ($condition.P -eq 1) { $ConstrainedPrompt } else { $UnconstrainedPrompt }
            $provider = ""

            if ($OpenAiModels -contains $model) {
                $provider = "openai"
            } elseif ($OpenRouterModels -contains $model) {
                $provider = "openrouter"
            } else {
                Write-Warning "Skipping unsupported model '$model'. Add it to either OpenAI or OpenRouter model list in run_ablation.ps1."
                continue
            }

            Write-Host ""
            Write-Host "============================================================"
            Write-Host "Run $runCounter / $totalRuns"
            Write-Host "Condition: $($condition.Id)  Model: $model  Provider: $provider  Repeat: $repeat"
            Write-Host "run_id: $runId"
            Write-Host "============================================================"

            if ($DryRun) {
                continue
            }

            $runArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", $EvalScript,
                "run_custom",
                "-Model", $model,
                "-Temperature", $Temperature,
                "-TopP", $TopP,
                "-Iterations", $Iterations,
                "-PromptFile", $promptFile,
                "-SystemPromptEnabled", $condition.S,
                "-AdsBookEnabled", $condition.A
            )
            if ($VerboseOutput) { $runArgs += "-VerboseOutput" }

            $originalProvider = $env:LLM_PROVIDER
            try {
                $env:LLM_PROVIDER = $provider
                & powershell @runArgs
            } finally {
                if ($null -eq $originalProvider -or $originalProvider -eq "") {
                    Remove-Item Env:\LLM_PROVIDER -ErrorAction SilentlyContinue
                } else {
                    $env:LLM_PROVIDER = $originalProvider
                }
            }
            $exitCode = $LASTEXITCODE
            $status = if ($exitCode -eq 0) { "ok" } else { "failed" }

            $organizeArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", $EvalScript,
                "organize_outputs",
                "-Model", $archiveModelArg
            )
            & powershell @organizeArgs

            $csvLine = "$runId,$timestamp,$status,$exitCode,$($condition.S),$($condition.A),$($condition.P),$provider,$safeModel,$repeat,$Iterations,$Temperature,$TopP,$promptFile,$archiveRel"
            Add-Content -Path $ManifestPath -Value $csvLine
        }
    }
}

$batchElapsed = (Get-Date) - $batchStart
$batchTime = ("{0}m {1:D2}s" -f [int]$batchElapsed.TotalMinutes, $batchElapsed.Seconds)
Write-Host ""
Write-Host "============================================================"
Write-Host "Ablation batch complete!"
Write-Host "Total runs processed: $runCounter"
Write-Host "Manifest: $ManifestPath"
Write-Host "Total time: $batchTime"
Write-Host "============================================================"

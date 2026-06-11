param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status"
)

$ProjectRoot = "D:\bdm-guild-karte"
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

$OllamaBaseUrl = "http://127.0.0.1:11434"

function Test-Ollama {
    try {
        Invoke-RestMethod -Uri "$OllamaBaseUrl/api/tags" -TimeoutSec 3 | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Start-OllamaIfNeeded {
    if (Test-Ollama) {
        Write-Host "Ollamaは起動中です。"
        return
    }

    Write-Host "Ollamaが起動していないため、起動を試みます。"

    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if (!$ollamaCommand) {
        Write-Host "ollama コマンドが見つかりません。Ollamaを手動で起動してください。"
        return
    }

    Start-Process `
        -FilePath $ollamaCommand.Source `
        -ArgumentList "serve" `
        -WindowStyle Hidden

    for ($i = 1; $i -le 30; $i++) {
        Start-Sleep -Seconds 1

        if (Test-Ollama) {
            Write-Host "Ollamaの起動を確認しました。"
            return
        }
    }

    Write-Host "Ollamaの起動確認に失敗しました。"
}




if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

function Get-MBotProcess {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -like "*-m bot.main*" -and
            $_.CommandLine -like "*D:\bdm-guild-karte*"
        }
}

function Stop-MBot {
    $processes = Get-MBotProcess

    if (!$processes) {
        Write-Host "起動中のMぼっとは見つかりません。"
        return
    }

    foreach ($process in $processes) {
        Write-Host "Mぼっとを停止します: PID=$($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force
    }

    Start-Sleep -Seconds 2
    Write-Host "Mぼっとを停止しました。"
}

function Start-MBot {
    # startは安全のため、既存のMぼっとを全部止めてから1体だけ起動する
    $existing = Get-MBotProcess
    if ($existing) {
        Write-Host "既存のMぼっとを停止してから起動します。"
        Stop-MBot
    }

    if (!(Test-Path $PythonExe)) {
        Write-Host "Pythonが見つかりません: $PythonExe"
        exit 1
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"

    $stdout = Join-Path $LogDir "bot_stdout.log"
    $stderr = Join-Path $LogDir "bot_stderr.log"

    $started = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList "-X utf8 -m bot.main" `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    Start-Sleep -Seconds 3

    $running = Get-MBotProcess

    if (!$running) {
        Write-Host "Mぼっとの起動に失敗したか、すぐ終了しました。logsフォルダを確認してください。"
        exit 1
    }

    Write-Host "Mぼっとを起動しました。PID=$($started.Id)"
    $running | Select-Object ProcessId, CommandLine
}

function Show-MBotStatus {
    $processes = Get-MBotProcess

    if (!$processes) {
        Write-Host "Mぼっとは停止中です。"
        return
    }

    Write-Host "Mぼっとは起動中です。"
    $processes | Select-Object ProcessId, CommandLine
}

switch ($Action) {
    "start" {
        Start-MBot
    }
    "stop" {
        Stop-MBot
    }
    "status" {
        Show-MBotStatus
    }
}


# Unloads the model the Strata desktop app holds on the GPU, so the nightly crawl + ingest (01:00) has the GPU.
# Run by the scheduled task nknu-strata-unload at 00:45 through unload_strata.vbs (no window). See README.md.
#
# Strata's local host (node, E:\Strata\host\server) listens on 127.0.0.1 at a port the desktop app picks; it takes
# local calls without a key. GET /api/runtime lists the loaded models; POST /api/runtime/<id>/unload unloads one and
# answers 409 while it is still answering a request, so a busy model is retried every 5 minutes; after -WaitMinutes
# POST /api/models/unload unloads it anyway. Nothing loaded or Strata not running: it only writes a line in the log.
param([switch]$DryRun, [int]$WaitMinutes = 60)

$ErrorActionPreference = 'Stop'
$log = Join-Path $PSScriptRoot 'unload.log'

function Say([string]$text) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $text
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Output $line
}

function GpuUsed {
    try { return (& nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | Select-Object -First 1).Trim() }
    catch { return '?' }
}

function Find-StrataHost {
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" |
        Where-Object { $_.CommandLine -like '*Strata*host*server*' }
    foreach ($p in $procs) {
        $ports = Get-NetTCPConnection -State Listen -OwningProcess $p.ProcessId -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -eq '127.0.0.1' } | Select-Object -ExpandProperty LocalPort -Unique
        foreach ($port in $ports) {
            try {
                $mode = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/app/mode" -TimeoutSec 5
                if ($null -ne $mode.PSObject.Properties['canQuit']) { return "http://127.0.0.1:$port" }
            } catch {}
        }
    }
    return $null
}

function StatusOf($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return 0 }
}

try {
    $base = Find-StrataHost
    if (-not $base) { Say "Strata 没开，不用卸。显卡已用 $(GpuUsed) MiB。"; exit 0 }

    $deadline = (Get-Date).AddMinutes($WaitMinutes)
    while ($true) {
        $ids = @((Invoke-RestMethod -Uri "$base/api/runtime" -TimeoutSec 15).instances | ForEach-Object { $_.id })
        if ($ids.Count -eq 0) { Say "Strata 开着、没载模型，不用卸。显卡已用 $(GpuUsed) MiB。"; exit 0 }
        if ($DryRun) { Say "空跑：载着 $($ids -join ', ')，正式跑会卸下。显卡已用 $(GpuUsed) MiB。"; exit 0 }

        $before = GpuUsed
        $busy = $false
        foreach ($id in $ids) {
            $url = "$base/api/runtime/$([uri]::EscapeDataString($id))/unload"
            try {
                Invoke-RestMethod -Method Post -Uri $url -ContentType 'application/json' -Body '{}' -TimeoutSec 300 | Out-Null
                Say "卸下了 $id。"
            } catch {
                $code = StatusOf $_
                if ($code -eq 409) { $busy = $true; Say "$id 还在回答问题，5 分钟后再试。" }
                elseif ($code -ne 404) { throw }
            }
        }
        if (-not $busy) { break }
        if ((Get-Date) -gt $deadline) {
            Invoke-RestMethod -Method Post -Uri "$base/api/models/unload" -ContentType 'application/json' -Body '{}' `
                -TimeoutSec 300 | Out-Null
            Say "等了 $WaitMinutes 分钟还在回答，强制卸下。"
            break
        }
        Start-Sleep -Seconds 300
    }
    Start-Sleep -Seconds 15
    Say "卸完。显卡已用 $before → $(GpuUsed) MiB。"
    exit 0
} catch {
    Say "出错：$($_.Exception.Message)"
    exit 1
}

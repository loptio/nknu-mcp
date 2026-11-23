# What the inspection panel cannot see from inside Docker (11-23): how much of E: is the project's (the crawler's raw files in
# E:\nknu, Docker's data disk E:\DockerData), the test model's (E:\Strata) and the rest. Sizes only, never a file's
# contents. Kept ASCII: Windows PowerShell 5 reads a script without a BOM in the system code page.
# Written whole to machine.json beside this script (C:\nknu-transfer\inspect\machine), which ops reads
# read-only at /machine (compose.yaml, --machine). Run every 30 minutes by the task nknu-machine-probe (probe.vbs).
$ErrorActionPreference = 'SilentlyContinue'

function SizeOf([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $sum = (Get-ChildItem -LiteralPath $path -Recurse -Force -File -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0 } else { return [int64]$sum }
}

$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='E:'"
$out = [ordered]@{
    at     = (Get-Date).ToString('yyyy-MM-ddTHH:mm:sszzz')
    drives = [ordered]@{
        E = [ordered]@{
            total = [int64]$disk.Size
            free  = [int64]$disk.FreeSpace
            parts = [ordered]@{
                nknu   = SizeOf 'E:\nknu'
                docker = SizeOf 'E:\DockerData'
                strata = SizeOf 'E:\Strata'
            }
        }
    }
}
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path $dir 'machine.json.tmp'
$out | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $tmp -Encoding UTF8
Move-Item -LiteralPath $tmp -Destination (Join-Path $dir 'machine.json') -Force

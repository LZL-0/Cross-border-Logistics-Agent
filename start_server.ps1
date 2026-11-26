# 物流引擎 · 检修台服务常驻脚本
# 用法：
#   .\start_server.ps1           启动（已在运行则跳过），进程脱离当前窗口，关掉终端也不会停
#   .\start_server.ps1 -Stop     停止 8000 端口上的服务
#   .\start_server.ps1 -Install  注册登录自启（当前用户，登录后自动拉起）
#   .\start_server.ps1 -Install -Uninstall  取消自启

param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = 'D:\Application\ToolsInComputer\anaconda\python.exe'
$port = 8000
$taskName = 'LogisticsEngineDebug'
$logDir = Join-Path $root '.cache\logs'

function Get-ServerPid {
    $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return @($conn | Select-Object -ExpandProperty OwningProcess -Unique) }
    return @()
}

# 停止
if ($Stop) {
    $pids = Get-ServerPid
    if (-not $pids) { Write-Host "8000 端口没有在跑的服务" }
    foreach ($id in $pids) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Write-Host "已停止 PID $id"
    }
    Start-Sleep -Milliseconds 600
    exit 0
}

# 取消自启
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "已取消登录自启：$taskName"
    exit 0
}

# 注册登录自启
if ($Install) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -WindowStyle Hidden -File `"$PSCommandPath`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    try {
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Settings $settings -Description '物流引擎检修台（127.0.0.1:8000）' -Force | Out-Null
        Write-Host "已注册登录自启：$taskName（下次登录自动启动）"
    } catch {
        Write-Host "注册自启失败（可能需要管理员）：$($_.Exception.Message)"
        Write-Host "可直接运行 .\start_server.ps1 手动启动，进程同样常驻"
    }
    exit 0
}

# 启动：幂等，已运行则直接返回
$running = Get-ServerPid
if ($running) {
    Write-Host "服务已在运行：http://127.0.0.1:$port/debug （PID $($running -join ',')）"
    exit 0
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONPATH = "$root\.cache\pylibs;$root"
$env:TEMP = "$root\.cache\temp"
$env:TMP = "$root\.cache\temp"
$env:DEBUG_TOOLKIT = '1'

$proc = Start-Process -FilePath $python `
    -ArgumentList '-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', "$port" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDir 'server.out.log') `
    -RedirectStandardError (Join-Path $logDir 'server.err.log')

# 等健康检查通过
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 700
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$port/api/debug/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {
        if ($proc.HasExited) { break }
    }
}

if ($ok) {
    Write-Host "已启动：http://127.0.0.1:$port/debug （PID $($proc.Id)）"
    Write-Host "日志：$logDir\server.err.log"
} else {
    Write-Host "启动失败，最后 20 行日志："
    Get-Content (Join-Path $logDir 'server.err.log') -Tail 20 -ErrorAction SilentlyContinue
    exit 1
}

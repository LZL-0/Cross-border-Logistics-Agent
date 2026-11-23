# 一键准备 Android 构建环境（仅用户目录，不改系统）
# 用途：新机器上重建 APK 前，先跑一次本脚本。
# 依赖：Windows + PowerShell 7 + Python 3（仅用于解压 zip）
$ErrorActionPreference = "Stop"

$root = Join-Path $env:LOCALAPPDATA "LogiAndroid"
$dl = Join-Path $root "dl"
$sdk = Join-Path $root "sdk"
New-Item -ItemType Directory -Force -Path $dl, $sdk | Out-Null

$curl = Join-Path $env:SystemRoot "System32\curl.exe"

function Get-Zip([string]$url, [string]$out) {
    if (Test-Path $out) {
        $len = (Get-Item $out).Length
        if ($len -gt 0) { Write-Host "已存在 $out（$([math]::Round($len/1MB,1)) MB），跳过"; return }
    }
    Write-Host "下载 $url"
    & $curl -L --retry 3 --retry-delay 2 -o $out $url
    if ($LASTEXITCODE -ne 0) { throw "下载失败：$url" }
}

function Expand-Zip([string]$zip, [string]$dest) {
    python -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$zip" "$dest"
    if ($LASTEXITCODE -ne 0) { throw "解压失败：$zip" }
}

# 1) JDK 17
$jdkZip = Join-Path $dl "jdk17.zip"
Get-Zip "https://mirrors.huaweicloud.com/openjdk/17.0.2/openjdk-17.0.2_windows-x64_bin.zip" $jdkZip
if (-not (Get-ChildItem $root -Directory -Filter "jdk-*" -ErrorAction SilentlyContinue)) {
    $ext = Join-Path $root "jdk_tmp"
    Expand-Zip $jdkZip $ext
    $inner = Get-ChildItem $ext -Directory | Select-Object -First 1
    Move-Item $inner.FullName (Join-Path $root "jdk-17.0.2")
}

# 2) build-tools 34 与 platform 34
if (-not (Test-Path (Join-Path $sdk "build-tools\34.0.0\aapt2.exe"))) {
    $btZip = Join-Path $dl "build-tools-34.zip"
    Get-Zip "https://mirrors.cloud.tencent.com/AndroidSDK/build-tools_r34-windows.zip" $btZip
    $x = Join-Path $root "x_bt"
    Expand-Zip $btZip $x
    New-Item -ItemType Directory -Force -Path (Join-Path $sdk "build-tools") | Out-Null
    Move-Item (Get-ChildItem $x -Directory | Select-Object -First 1).FullName (Join-Path $sdk "build-tools\34.0.0")
}

if (-not (Test-Path (Join-Path $sdk "platforms\android-34\android.jar"))) {
    $pfZip = Join-Path $dl "platform-34.zip"
    Get-Zip "https://mirrors.cloud.tencent.com/AndroidSDK/platform-34-ext12_r01.zip" $pfZip
    $x2 = Join-Path $root "x_pf"
    Expand-Zip $pfZip $x2
    New-Item -ItemType Directory -Force -Path (Join-Path $sdk "platforms") | Out-Null
    Move-Item (Get-ChildItem $x2 -Directory | Select-Object -First 1).FullName (Join-Path $sdk "platforms\android-34")
}

$java = Get-ChildItem $root -Directory -Filter "jdk-*" | Select-Object -First 1
& (Join-Path $java.FullName "bin\java.exe") -version
Write-Host "构建环境就绪：$sdk"

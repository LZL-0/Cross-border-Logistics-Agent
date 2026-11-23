# 构建 APK（无需 Gradle / Android Studio）
# 用法： & .\build.ps1            # 默认自签 release
#       & .\build.ps1 -SkipData  # 跳过知识库内联
# 说明：aapt2 在 Windows 下不认中文路径，构建在 ASCII 临时目录进行，产物拷回 dist。
param(
    [switch]$SkipData
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$project = Split-Path -Parent $root
$toolchain = Join-Path $env:LOCALAPPDATA "LogiAndroid"
$sdk = if (Test-Path (Join-Path $toolchain "sdk")) { Join-Path $toolchain "sdk" } else { $env:ANDROID_HOME }

$jdkDir = Get-ChildItem $toolchain -Directory -Filter "jdk-*" | Select-Object -First 1
if (-not $jdkDir) { throw "未找到 JDK，请先执行 scripts\setup_toolchain.ps1" }
$jdk = $jdkDir.FullName
$env:JAVA_HOME = $jdk
$env:PATH = "$jdk\bin;$env:PATH"

$bt = Join-Path $sdk "build-tools\34.0.0"
$androidJar = Join-Path $sdk "platforms\android-34\android.jar"
foreach ($p in @($bt, $androidJar)) {
    if (-not (Test-Path $p)) { throw "缺少构建依赖：$p" }
}

$stage = Join-Path $env:TEMP "logi_apk_stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null

$assets = Join-Path $root "app\assets"
if (-not $SkipData) {
    Write-Host "[1/8] 内联知识库 -> data.js"
    & (Join-Path $project ".venv\Scripts\python.exe") (Join-Path $root "scripts\gen_data.py")
    if ($LASTEXITCODE -ne 0) { throw "知识库内联失败" }
} else {
    Write-Host "[1/8] 跳过知识库内联"
}

Copy-Item (Join-Path $root "app\AndroidManifest.xml") $stage
Copy-Item (Join-Path $root "app\res") (Join-Path $stage "res") -Recurse
Copy-Item (Join-Path $root "app\src") (Join-Path $stage "src") -Recurse
Copy-Item $assets (Join-Path $stage "assets") -Recurse

Write-Host "[2/8] 编译资源 (aapt2 compile)"
& (Join-Path $bt "aapt2.exe") compile --dir (Join-Path $stage "res") -o (Join-Path $stage "res.zip")
if ($LASTEXITCODE -ne 0) { throw "aapt2 compile 失败" }

Write-Host "[3/8] 编译 Java (javac)"
$classes = Join-Path $stage "classes"
New-Item -ItemType Directory -Force -Path $classes | Out-Null
$javaFiles = Get-ChildItem (Join-Path $stage "src") -Recurse -Filter *.java | ForEach-Object { $_.FullName }
$javacArgs = @("-encoding", "UTF-8", "-source", "8", "-target", "8", "-classpath", $androidJar, "-d", $classes) + @($javaFiles)
& (Join-Path $jdk "bin\javac.exe") @javacArgs
if ($LASTEXITCODE -ne 0) { throw "javac 失败" }

Write-Host "[4/8] 转 dex (d8)"
$dexDir = Join-Path $stage "dex"
New-Item -ItemType Directory -Force -Path $dexDir | Out-Null
$classFiles = Get-ChildItem $classes -Recurse -Filter *.class | ForEach-Object { $_.FullName }
$d8Args = @("--release", "--min-api", "21", "--lib", $androidJar, "--output", $dexDir) + @($classFiles)
& (Join-Path $bt "d8.bat") @d8Args
if ($LASTEXITCODE -ne 0) { throw "d8 失败" }

Write-Host "[5/8] 链接资源 (aapt2 link)"
$unsigned = Join-Path $stage "app-unsigned.apk"
& (Join-Path $bt "aapt2.exe") link -o $unsigned -I $androidJar `
    --manifest (Join-Path $stage "AndroidManifest.xml") `
    -A (Join-Path $stage "assets") --auto-add-overlay (Join-Path $stage "res.zip")
if ($LASTEXITCODE -ne 0) { throw "aapt2 link 失败" }

Write-Host "[6/8] 打入 classes.dex"
& (Join-Path $project ".venv\Scripts\python.exe") -c @"
import zipfile, sys
apk = sys.argv[1]; dex = sys.argv[2]
with zipfile.ZipFile(apk, 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(dex, 'classes.dex')
print('classes.dex ->', apk)
"@ $unsigned (Join-Path $dexDir "classes.dex")
if ($LASTEXITCODE -ne 0) { throw "写入 dex 失败" }

Write-Host "[7/8] 对齐 (zipalign)"
$aligned = Join-Path $stage "app-aligned.apk"
& (Join-Path $bt "zipalign.exe") -f 4 $unsigned $aligned
if ($LASTEXITCODE -ne 0) { throw "zipalign 失败" }

Write-Host "[8/8] 签名 (apksigner)"
$ks = Join-Path $toolchain "logistics.keystore"
if (-not (Test-Path $ks)) {
    & (Join-Path $jdk "bin\keytool.exe") -genkeypair -keystore $ks -storepass logistics2026 `
        -keypass logistics2026 -alias logistics -keyalg RSA -keysize 2048 -validity 10950 `
        -dname "CN=Cross-border Logistics Engine,O=Logistics,C=CN"
    if ($LASTEXITCODE -ne 0) { throw "生成签名证书失败" }
}
$finalStage = Join-Path $stage "logistics.apk"
& (Join-Path $bt "apksigner.bat") sign --ks $ks --ks-pass pass:logistics2026 `
    --key-pass pass:logistics2026 --v1-signing-enabled true --v2-signing-enabled true `
    --out $finalStage $aligned
if ($LASTEXITCODE -ne 0) { throw "apksigner 签名失败" }

& (Join-Path $bt "apksigner.bat") verify --verbose $finalStage
if ($LASTEXITCODE -ne 0) { throw "签名校验失败" }

$final = Join-Path $dist "跨境物流引擎.apk"
if (Test-Path $final) { Remove-Item $final -Force }
Copy-Item $finalStage $final
$info = Get-Item $final
Write-Host ""
Write-Host ("完成：{0}  {1:N1} MB" -f $info.FullName, ($info.Length / 1MB))

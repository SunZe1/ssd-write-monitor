param(
    [string]$PythonPath = "$env:USERPROFILE\.conda\envs\query\python.exe",
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath $PythonPath)) {
        $PythonPath = (Get-Command python -ErrorAction Stop).Source
    }
    if (-not $SkipTests) {
        & $PythonPath -m unittest discover -s tests -v
        if ($LASTEXITCODE -ne 0) { throw '测试失败，停止打包。' }
    }
    & $PythonPath -m PyInstaller --noconfirm ssd_monitor.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败。' }
    Copy-Item -LiteralPath 'dist/SSDMonitor.exe' -Destination 'SSDMonitor.exe' -Force
    Compress-Archive -Path 'dist/SSDMonitor.exe','使用说明.txt','third_party' -DestinationPath 'dist/SSDMonitor-2.0.1-portable.zip' -Force
    Write-Host '已生成 SSDMonitor.exe 和 dist/SSDMonitor-2.0.1-portable.zip'
} finally {
    Pop-Location
}

# setup_dbgeng.ps1 — re-derive vendor/dbgeng (Debugging Tools for Windows) from
# the WinDbg MSIX. No admin needed; requires winget + internet.
#
# Why: vendor/dbgeng (dbgeng.dll + cdb.exe, ~135MB of Microsoft binaries) is
# gitignored. Run this once after cloning to make the runtime work.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot                 # project root (scripts/..)
$vendor = Join-Path $root 'vendor\dbgeng'

if (Test-Path (Join-Path $vendor 'dbgeng.dll')) {
    Write-Host "dbgeng.dll already present, skipping."
    exit 0
}

$tmp = Join-Path $env:TEMP ('dsh_dbgeng_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
New-Item -ItemType Directory -Path $vendor -Force | Out-Null

try {
    # 1. download WinDbg installer (msixbundle) via winget
    Write-Host "[1/5] winget download Microsoft.WinDbg ..."
    winget download --id Microsoft.WinDbg -e `
        --download-directory (Join-Path $tmp 'dl') `
        --accept-package-agreements --accept-source-agreements | Out-Host
    $bundle = Get-ChildItem (Join-Path $tmp 'dl') -Filter '*.msix*' | Select-Object -First 1

    # 2. extract the outer bundle (a zip)
    Write-Host "[2/5] extract outer bundle ..."
    $z1 = Join-Path $tmp 'bundle.zip'
    Copy-Item $bundle.FullName $z1
    Expand-Archive $z1 -DestinationPath (Join-Path $tmp 'outer') -Force

    # 3. locate the inner x64 package
    $x64 = Get-ChildItem (Join-Path $tmp 'outer') -Filter 'windbg_win-x64.msix' | Select-Object -First 1
    if (-not $x64) { throw "windbg_win-x64.msix not found in bundle" }

    # 4. extract the inner package (a zip)
    Write-Host "[3/5] extract inner x64 package ..."
    $z2 = Join-Path $tmp 'x64.zip'
    Copy-Item $x64.FullName $z2
    Expand-Archive $z2 -DestinationPath (Join-Path $tmp 'inner') -Force

    # 5. copy amd64 -> vendor/dbgeng
    Write-Host "[4/5] copy amd64 -> vendor/dbgeng ..."
    Copy-Item -Path (Join-Path $tmp 'inner\amd64\*') -Destination $vendor -Recurse -Force

    Write-Host "[5/5] done."
    & (Join-Path $vendor 'cdb.exe') -version | Select-Object -First 1
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

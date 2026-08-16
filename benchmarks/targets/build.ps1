# build.ps1 — compile the benchmark targets with mingw-w64 gcc.
# Locates gcc.exe from PATH or common per-user install locations.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Gcc {
    $cmd = Get-Command gcc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @()
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter gcc.exe -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs" -Recurse -Filter gcc.exe -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "C:\msys64\mingw64\bin\gcc.exe" -ErrorAction SilentlyContinue
    $candidates += Get-ChildItem "C:\mingw64\bin\gcc.exe" -ErrorAction SilentlyContinue
    foreach ($c in $candidates) {
        if ($c) { return $c.FullName }
    }
    return $null
}

$gcc = Find-Gcc
if (-not $gcc) {
    Write-Error "gcc.exe not found. Install mingw-w64 (e.g. `winget install BrechtSanders.WinLibs.POSIX.UCRT`)."
}

Write-Host "Using gcc: $gcc"
$ver = & $gcc --version | Select-Object -First 1
Write-Host $ver

Push-Location $here
try {
    & $gcc -O0 -o crash_target.exe crash_target.c
    if ($LASTEXITCODE -ne 0) { throw "gcc failed with exit $LASTEXITCODE" }
    Write-Host "Built crash_target.exe ->" (Join-Path $here 'crash_target.exe')
}
finally {
    Pop-Location
}

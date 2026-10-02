param([string]$VcVars = 'C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    New-Item -ItemType Directory -Force build-wire | Out-Null
    if (!(Test-Path -LiteralPath $VcVars)) { throw 'Supply -VcVars pointing to the installed vcvars64.bat.' }
    if ($VcVars.Contains('"') -or $VcVars.Contains('%')) { throw 'Unsupported characters in compiler setup path.' }
    cmd /c "`"$VcVars`" >nul && cl /nologo /std:c11 /W4 /WX /LD main\observation_wire.c /Fo:build-wire\observation_wire.obj /Fe:build-wire\observation_wire.dll /link /EXPORT:wh_wire_valid /EXPORT:wh_wire_encode /EXPORT:wh_ds18b20_decode"
    if ($LASTEXITCODE) { throw 'C codec compilation failed.' }
    $previousLibrary = $env:WASMHOME_WIRE_LIBRARY
    try {
        $env:WASMHOME_WIRE_LIBRARY = Join-Path $taskRoot 'build-wire\observation_wire.dll'
        python -m unittest discover -s tests -v
        if ($LASTEXITCODE) { throw 'Platform tests failed.' }
    } finally {
        $env:WASMHOME_WIRE_LIBRARY = $previousLibrary
    }
} finally { Pop-Location }

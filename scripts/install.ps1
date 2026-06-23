#!/usr/bin/env pwsh
param(
    [string] $Source = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-UvPackageSpec {
    param(
        [Parameter(Mandatory)]
        [string] $Value
    )

    if ($Value.StartsWith('git+', [System.StringComparison]::OrdinalIgnoreCase) -or
        $Value.StartsWith('file:', [System.StringComparison]::OrdinalIgnoreCase)) {
        return $Value
    }

    $expanded = [Environment]::ExpandEnvironmentVariables($Value)
    if (Test-Path -LiteralPath $expanded) {
        return (Resolve-Path -LiteralPath $expanded).Path
    }

    if ($Value -match '^(https?|ssh)://') {
        return "git+$Value"
    }

    return $Value
}

$sourceValue = if ($Source) {
    $Source
} elseif ($env:IMLADRIS_SOURCE) {
    $env:IMLADRIS_SOURCE
} elseif ($env:IMLADRIS_REPO) {
    $env:IMLADRIS_REPO
} else {
    'https://github.com/FeanorsCodeSL/imladris'
}

$packageSpec = Resolve-UvPackageSpec -Value $sourceValue
$requiresGit = $packageSpec.StartsWith('git+', [System.StringComparison]::OrdinalIgnoreCase)

if ($requiresGit -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is required for the current git-URL install source. Install Git, then rerun this command. A future PyPI release will remove this prerequisite.'
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Output 'Installing uv (astral.sh/uv)...'
    $installer = Join-Path $env:TEMP 'uv-install.ps1'
    Invoke-RestMethod 'https://astral.sh/uv/install.ps1' -OutFile $installer
    & $installer
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

$toolBin = (& uv tool dir --bin).Trim()
if ($toolBin) {
    $env:Path = "$toolBin;$env:Path"
}

$pythonCmd = (& uv python find 3.13 2>$null).Trim()
if (-not $pythonCmd) {
    Write-Output 'Installing Python 3.13 with uv...'
    uv python install 3.13
    $pythonCmd = (& uv python find 3.13).Trim()
}

Write-Output "Installing imladris from $packageSpec ..."
Write-Output 'Refreshing any previous imladris tool environment...'
try {
    & uv tool uninstall imladris *> $null
} catch {
    Write-Verbose "No previous imladris tool environment to remove: $($_.Exception.Message)"
}
try {
    & uv cache clean imladris *> $null
} catch {
    Write-Verbose "Could not clean imladris cache entry: $($_.Exception.Message)"
}
uv tool install --force --reinstall --refresh --python "$pythonCmd" "$packageSpec"
try {
    uv tool update-shell | Out-Null
} catch {
    Write-Verbose "Could not update shell PATH automatically: $($_.Exception.Message)"
}

$imladrisCommand = Get-Command imladris -ErrorAction SilentlyContinue
$imladrisPath = if ($imladrisCommand) {
    $imladrisCommand.Source
} else {
    Join-Path $toolBin 'imladris.exe'
}

if (-not (Test-Path -LiteralPath $imladrisPath)) {
    throw "Installed imladris executable was not found at $imladrisPath."
}

& $imladrisPath --help *> $null
if ($LASTEXITCODE -ne 0) {
    $toolDir = (& uv tool dir).Trim()
    $pyvenv = Join-Path $toolDir 'imladris\pyvenv.cfg'
    Write-Error "Installed imladris did not launch. Inspect $pyvenv for the Python home used by uv."
    exit $LASTEXITCODE
}

Write-Output ''
Write-Output 'Installed two commands:'
Write-Output '  imladris      - the configurator TUI (run this next)'
Write-Output '  imladris-mcp  - the stdio MCP server your harnesses spawn'
Write-Output ''
if (Get-Command imladris -ErrorAction SilentlyContinue) {
    Write-Output "Next: run 'imladris' to assemble your council."
} else {
    Write-Output "Next: open a new terminal, then run 'imladris' to assemble your council."
    if ($toolBin) {
        Write-Output "If needed, add this directory to PATH: $toolBin"
    }
}

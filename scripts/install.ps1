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
} elseif ($env:MANDOS_SOURCE) {
    $env:MANDOS_SOURCE
} elseif ($env:MANDOS_REPO) {
    $env:MANDOS_REPO
} else {
    'https://github.com/Fox-Islam/mandos'
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

Write-Output "Installing mandos from $packageSpec ..."
Write-Output 'Refreshing any previous mandos tool environment...'
try {
    & uv tool uninstall mandos *> $null
} catch {
    Write-Verbose "No previous mandos tool environment to remove: $($_.Exception.Message)"
}
try {
    & uv cache clean mandos *> $null
} catch {
    Write-Verbose "Could not clean mandos cache entry: $($_.Exception.Message)"
}
uv tool install --force --reinstall --refresh --python "$pythonCmd" "$packageSpec"
try {
    uv tool update-shell | Out-Null
} catch {
    Write-Verbose "Could not update shell PATH automatically: $($_.Exception.Message)"
}

$mandosCommand = Get-Command mandos -ErrorAction SilentlyContinue
$mandosPath = if ($mandosCommand) {
    $mandosCommand.Source
} else {
    Join-Path $toolBin 'mandos.exe'
}

if (-not (Test-Path -LiteralPath $mandosPath)) {
    throw "Installed mandos executable was not found at $mandosPath."
}

& $mandosPath --help *> $null
if ($LASTEXITCODE -ne 0) {
    $toolDir = (& uv tool dir).Trim()
    $pyvenv = Join-Path $toolDir 'mandos\pyvenv.cfg'
    Write-Error "Installed mandos did not launch. Inspect $pyvenv for the Python home used by uv."
    exit $LASTEXITCODE
}

Write-Output ''
Write-Output 'Installed two commands:'
Write-Output '  mandos      - the configurator TUI (run this next)'
Write-Output '  mandos-mcp  - the stdio MCP server your harnesses spawn'
Write-Output ''
if (Get-Command mandos -ErrorAction SilentlyContinue) {
    Write-Output "Next: run 'mandos' to assemble your council."
} else {
    Write-Output "Next: open a new terminal, then run 'mandos' to assemble your council."
    if ($toolBin) {
        Write-Output "If needed, add this directory to PATH: $toolBin"
    }
}

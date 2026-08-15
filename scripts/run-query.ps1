# run-query.ps1 - Fallback wrapper when SQLcl MCP is unavailable.
# Runs a .sql file via SQLcl (or sqlplus) and prints the result.
# Usage: powershell.exe -ExecutionPolicy Bypass -File scripts\run-query.ps1 -Connection dev -SqlFile sql\tune\sql_stats.sql
# Notes:
#  - Connection is a saved SQLcl connection name (connmgr) or an EZConnect string.
#  - Binds are not supported here; replace :binds manually or pass -Define values.

param(
    [Parameter(Mandatory=$true)][string]$Connection,
    [Parameter(Mandatory=$true)][string]$SqlFile,
    [int]$TimeoutSec = 120
)

$ErrorActionPreference = "Stop"

if (-not $env:JAVA_HOME) {
    $bundledJdk = Join-Path $PSScriptRoot "..\tools\jdk"
    if (Test-Path (Join-Path $bundledJdk "bin\java.exe")) { $env:JAVA_HOME = (Resolve-Path $bundledJdk).Path }
}

if (-not (Test-Path $SqlFile)) { throw "SQL file not found: $SqlFile" }

$sqlcl = $null
$candidates = @(
    (Join-Path $PSScriptRoot "..\tools\sqlcl\bin\sql.exe"),
    "sql.exe",
    "sql"
)
foreach ($c in $candidates) {
    try { $sqlcl = (Get-Command $c -ErrorAction Stop).Source; break } catch {}
}
if (-not $sqlcl) { throw "SQLcl not found. Install under tools\sqlcl or add to PATH." }

$tmp = [System.IO.Path]::GetTempFileName() + ".sql"
$header = "set pagesize 200 linesize 250 trimspool on feedback off" + [Environment]::NewLine +
          "set sqlformat ansiconsole" + [Environment]::NewLine
$body = Get-Content -Raw -Encoding UTF8 $SqlFile
$body = $body.TrimStart([char]0xFEFF)
$footer = [Environment]::NewLine + "exit"
Set-Content -Path $tmp -Value ($header + $body + $footer) -Encoding ASCII

try {
    # Feed script via cmd stdin redirect: SQLcl console init fails when stdin is the
    # null device, and PowerShell pipes can prepend a BOM depending on $OutputEncoding.
    cmd /c "`"$sqlcl`" -S -name $Connection < `"$tmp`""
    if ($LASTEXITCODE -ne 0) {
        # -name works only for saved connections; retry treating it as connect string
        cmd /c "`"$sqlcl`" -S $Connection < `"$tmp`""
    }
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

# run-query.ps1 - Fallback wrapper when SQLcl MCP is unavailable.
# Runs a .sql file via SQLcl (or sqlplus) and prints the result.
# Usage: powershell.exe -ExecutionPolicy Bypass -File scripts\run-query.ps1 -Connection dev -SqlFile sql\tune\sql_stats.sql
# Notes:
#  - Connection is a saved SQLcl connection name (connmgr) or an EZConnect string.
#  - Connection "env" resolves PLSQLFLOW_USER / PLSQLFLOW_PWD / PLSQLFLOW_DSN from
#    the process environment or from a .env file in the current directory (process
#    environment wins). The credential is never placed on the SQLcl command line;
#    it is written as a "connect" line inside the temp .sql script instead.
#  - Use -DryRun to print the resolved user/DSN (never the password) and exit
#    before invoking SQLcl. Useful to test resolution without a database.
#  - Binds are not supported here; replace :binds manually or pass -Define values.

param(
    [Parameter(Mandatory=$true)][string]$Connection,
    [Parameter(Mandatory=$true)][string]$SqlFile,
    [int]$TimeoutSec = 120,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Parse-DotEnv {
    param([string]$Path)

    $result = @{}
    if (-not (Test-Path $Path)) { return $result }

    $lines = Get-Content -Path $Path -Encoding UTF8
    foreach ($line in $lines) {
        $t = $line.Trim()
        if ($t.Length -eq 0) { continue }
        if ($t.StartsWith("#")) { continue }
        if ($t.StartsWith("export ")) { $t = $t.Substring(7).Trim() }

        $idx = $t.IndexOf("=")
        if ($idx -lt 0) { continue }

        $key = $t.Substring(0, $idx).Trim()
        $val = $t.Substring($idx + 1).Trim()

        if ($val.Length -ge 2) {
            $first = $val.Substring(0, 1)
            $last = $val.Substring($val.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }

        if ($key.Length -gt 0) { $result[$key] = $val }
    }
    return $result
}

function Resolve-EnvCredential {
    param([string]$DotEnvPath)

    $dotenv = Parse-DotEnv -Path $DotEnvPath
    $names = @("PLSQLFLOW_USER", "PLSQLFLOW_PWD", "PLSQLFLOW_DSN")
    $resolved = @{}
    $missing = @()

    foreach ($n in $names) {
        $v = [Environment]::GetEnvironmentVariable($n)
        if (-not $v) {
            if ($dotenv.ContainsKey($n) -and $dotenv[$n]) { $v = $dotenv[$n] }
        }
        if ($v) { $resolved[$n] = $v } else { $missing += $n }
    }

    if ($missing.Count -gt 0) {
        $missingList = $missing -join ", "
        throw "Faltam variaveis para -Connection env: $missingList. Defina no ambiente do processo ou em um .env em $DotEnvPath (chaves esperadas: PLSQLFLOW_USER, PLSQLFLOW_PWD, PLSQLFLOW_DSN)."
    }

    return $resolved
}

$useEnvConnection = ($Connection -eq "env")
$envUser = $null
$envPwd = $null
$envDsn = $null

if ($useEnvConnection) {
    $dotEnvPath = Join-Path (Get-Location).Path ".env"
    $cred = Resolve-EnvCredential -DotEnvPath $dotEnvPath
    $envUser = $cred["PLSQLFLOW_USER"]
    $envPwd = $cred["PLSQLFLOW_PWD"]
    $envDsn = $cred["PLSQLFLOW_DSN"]
}

if ($DryRun) {
    if ($useEnvConnection) {
        Write-Output "User: $envUser"
        Write-Output "DSN: $envDsn"
        Write-Output "Password: [SET]"
    } else {
        Write-Output "Connection: $Connection"
    }
    exit 0
}

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

if ($useEnvConnection) {
    # Credential goes inside the temp script only -- never on the SQLcl command
    # line, which is visible to any user on the machine via the process list.
    $connectLine = "connect " + $envUser + "/" + $envPwd + "@" + $envDsn + [Environment]::NewLine
    Set-Content -Path $tmp -Value ($header + $connectLine + $body + $footer) -Encoding ASCII
} else {
    Set-Content -Path $tmp -Value ($header + $body + $footer) -Encoding ASCII
}

try {
    if ($useEnvConnection) {
        # /nolog: no connection info passed on the command line, the temp
        # script above supplies "connect user/pwd@dsn" instead.
        cmd /c "`"$sqlcl`" -S /nolog < `"$tmp`""
    } else {
        # Feed script via cmd stdin redirect: SQLcl console init fails when stdin is the
        # null device, and PowerShell pipes can prepend a BOM depending on $OutputEncoding.
        cmd /c "`"$sqlcl`" -S -name $Connection < `"$tmp`""
        if ($LASTEXITCODE -ne 0) {
            # -name works only for saved connections; retry treating it as connect string
            cmd /c "`"$sqlcl`" -S $Connection < `"$tmp`""
        }
    }
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}

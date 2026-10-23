# Cursor hook launcher for Windows PowerShell and pwsh: `hook.ps1 cursor <event>` runs `session_log.py hook cursor <event>` with the
# hook input on stdin (Cursor delivers it through `$input`, which is piped on to Python). It exits 0 without starting Python while logging is off (neither `.log_enabled` nor the old release's
# `.enabled` exists) or when the entry script is missing. The interpreter is the first of `py -3`, `python3`, `python` found on
# PATH; once one is found no other is started, whatever it returns, so the Microsoft Store stand-in is never tried first.
$ErrorActionPreference = 'SilentlyContinue'
$logs = Join-Path (Join-Path $HOME '.cursor') 'prompt-logs'
if (-not ((Test-Path -LiteralPath (Join-Path $logs '.log_enabled') -PathType Leaf) -or (Test-Path -LiteralPath (Join-Path $logs '.enabled') -PathType Leaf))) { exit 0 }
$script = Join-Path $PSScriptRoot 'session_log.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { exit 0 }
$py = Get-Command py -CommandType Application | Select-Object -First 1
if ($py) { $input | & $py.Source -3 $script hook @args; exit 0 }
foreach ($name in 'python3', 'python') {
    $interpreter = Get-Command $name -CommandType Application | Select-Object -First 1
    if ($interpreter) { $input | & $interpreter.Source $script hook @args; exit 0 }
}
exit 0

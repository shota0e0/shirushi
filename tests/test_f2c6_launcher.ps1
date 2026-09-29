[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$LauncherPath = Join-Path $ProjectRoot 'scripts\canary\Run-Canary.ps1'
$HostPath = Join-Path $ProjectRoot 'desktop\src\host.rs'
$LibPath = Join-Path $ProjectRoot 'desktop\src\lib.rs'
$LauncherText = Get-Content -LiteralPath $LauncherPath -Raw -Encoding UTF8

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Import-LauncherFunction {
    param([string]$Name, [System.Management.Automation.Language.ScriptBlockAst]$Ast)
    $definition = $Ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
    }, $true)
    if ($null -eq $definition) {
        throw "Launcher function not found: $Name"
    }
    return $definition.Extent.Text
}

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($LauncherPath, [ref]$tokens, [ref]$parseErrors)
Assert-True ($parseErrors.Count -eq 0) 'Run-Canary.ps1 must parse without errors.'
Assert-True ($LauncherText -match '\$process\s*=\s*Invoke-WithCanaryEnvironment') 'Start-Process result must be captured by the local monitored process variable.'
Assert-True ($LauncherText -notmatch '\$script:process') 'Launcher must not leak Start-Process into script scope.'
Assert-True ($LauncherText -match 'function Get-DescendantProcessSnapshot') 'Launcher must define recursive descendant traversal.'
Assert-True ($LauncherText -match 'Get-DescendantProcessSnapshot -RootProcessId \$process\.Id') 'Launch monitoring must use recursive descendant traversal.'
Assert-True ($LauncherText -notmatch '(?i)Stop-Process|taskkill') 'Launcher must not broadly kill processes.'
Assert-True ($LauncherText -match "StartsWith\('\.venv-py312/'") 'Only the exact venv runtime prefix may be excluded from payload closure.'
Assert-True ($LauncherText -match "StartsWith\('demo-profile/Shirushi/canary-webview/'") 'Only the exact WebView runtime prefix may be excluded from payload closure.'
Assert-True ($LauncherText -match '\.IndexOf\(\$BridgePath, \[System\.StringComparison\]::OrdinalIgnoreCase\) -ge 0') 'Windows PowerShell-compatible command-path corroboration is required.'

$functionDefinitions = foreach ($name in @(
    'Stop-Canary',
    'Get-ContainedPath',
    'Assert-NoReparsePoint',
    'Assert-NoReparseChain',
    'Get-PackageRelativePath',
    'Test-RuntimeRelativePath',
    'Assert-ImmutablePackage',
    'Invoke-WithCanaryEnvironment',
    'Get-ExactPackageProcesses',
    'Get-DescendantProcessSnapshot',
    'Test-ExplicitPolicyBlock',
    'Invoke-Launch'
)) {
    Import-LauncherFunction -Name $name -Ast $ast
}
Invoke-Expression ($functionDefinitions -join "`n`n")

$oldLocal = [Environment]::GetEnvironmentVariable('LOCALAPPDATA', 'Process')
$oldUnavailable = [Environment]::GetEnvironmentVariable('SHIRUSHI_CANARY_SIDECAR_UNAVAILABLE', 'Process')
try {
    $PackageRoot = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) ("shirushi-f2c6-launcher-test-" + [Guid]::NewGuid().ToString('N'))))
    $ExecutablePath = Join-Path $PackageRoot 'shirushi-desktop.exe'
    $ManifestPath = Join-Path $PackageRoot 'manifest.json'
    $SumsPath = Join-Path $PackageRoot 'SHA256SUMS'
    $ProfilePath = Join-Path $PackageRoot 'demo-profile'
    $VenvPath = Join-Path $PackageRoot '.venv-py312'
    $VenvPythonPath = Join-Path $VenvPath 'Scripts\python.exe'
    $BridgePath = Join-Path $PackageRoot 'scripts\shirushi_bridge.py'
    $WebViewRuntimePath = Join-Path $ProfilePath 'Shirushi\canary-webview'
    $UnavailableVariable = 'SHIRUSHI_CANARY_SIDECAR_UNAVAILABLE'

    [Environment]::SetEnvironmentVariable('LOCALAPPDATA', 'real-profile-sentinel', 'Process')
    [Environment]::SetEnvironmentVariable($UnavailableVariable, 'inherited-failure-sentinel', 'Process')
    $insideNormal = Invoke-WithCanaryEnvironment -Unavailable $false -Body {
        [pscustomobject]@{
            Local = [Environment]::GetEnvironmentVariable('LOCALAPPDATA', 'Process')
            Unavailable = [Environment]::GetEnvironmentVariable($UnavailableVariable, 'Process')
        }
    }
    Assert-True ($insideNormal.Local -ceq $ProfilePath) 'Normal launch must force package-local LOCALAPPDATA.'
    Assert-True ([string]::IsNullOrEmpty([string]$insideNormal.Unavailable)) 'Normal launch must clear an inherited failure flag.'
    Assert-True ([Environment]::GetEnvironmentVariable('LOCALAPPDATA', 'Process') -ceq 'real-profile-sentinel') 'LOCALAPPDATA must be restored after the child is created.'
    Assert-True ([Environment]::GetEnvironmentVariable($UnavailableVariable, 'Process') -ceq 'inherited-failure-sentinel') 'Failure flag must be restored after the child is created.'
    $insideFailure = Invoke-WithCanaryEnvironment -Unavailable $true -Body {
        [Environment]::GetEnvironmentVariable($UnavailableVariable, 'Process')
    }
    Assert-True ($insideFailure -ceq '1') 'Sidecar-unavailable launch must set only the exact value 1.'

    New-Item -ItemType Directory -Path (Split-Path -Parent $BridgePath) -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $ProfilePath 'Shirushi\personal-mark') -Force | Out-Null
    [System.IO.File]::WriteAllBytes($ExecutablePath, [byte[]](1, 2, 3))
    [System.IO.File]::WriteAllText($BridgePath, 'bridge-fixture')
    $files = @()
    foreach ($relative in @('shirushi-desktop.exe', 'scripts/shirushi_bridge.py')) {
        $full = Join-Path $PackageRoot ($relative.Replace('/', '\'))
        $files += [ordered]@{
            path = $relative
            classification = 'test'
            size = (Get-Item -LiteralPath $full).Length
            sha256 = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
            source = @{ kind = 'test'; identifier = $relative; sha256 = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant() }
        }
    }
    $manifest = [ordered]@{ schemaVersion = 1; artifactType = 'DEVELOPMENT_CANARY'; files = $files }
    [System.IO.File]::WriteAllText($ManifestPath, ($manifest | ConvertTo-Json -Depth 8), [System.Text.UTF8Encoding]::new($false))
    $sumLines = @()
    foreach ($relative in @('manifest.json', 'scripts/shirushi_bridge.py', 'shirushi-desktop.exe')) {
        $full = Join-Path $PackageRoot ($relative.Replace('/', '\'))
        $sumLines += "$(Get-FileHash -LiteralPath $full -Algorithm SHA256 | Select-Object -ExpandProperty Hash | ForEach-Object { $_.ToLowerInvariant() })  $relative"
    }
    [System.IO.File]::WriteAllText($SumsPath, (($sumLines -join "`n") + "`n"), [System.Text.UTF8Encoding]::new($false))
    Assert-ImmutablePackage
    [System.IO.File]::AppendAllText($ExecutablePath, 'tamper')
    $tamperRejected = $false
    try {
        Assert-ImmutablePackage
    } catch {
        $tamperRejected = $true
    }
    Assert-True $tamperRejected 'Manifest/hash validation must reject a changed payload byte.'

    New-Item -ItemType Directory -Path (Split-Path -Parent $VenvPythonPath) -Force | Out-Null
    [System.IO.File]::WriteAllBytes($VenvPythonPath, [byte[]](4, 5, 6))

    function Get-ExactPackageProcesses { return @() }
    function Assert-NoReparseChain { param([string]$Path, [string]$Label) }
    function Start-Sleep { param([int]$Milliseconds) }
    function Start-Process {
        param([string]$FilePath, [switch]$PassThru)
        $fake = [pscustomobject]@{
            Id = 100
            StartTime = $script:F2C6StartTime
            ExitCode = $(if ($script:F2C6Scenario -ceq 'nonzero-exit') { 7 } else { 0 })
        }
        $fake | Add-Member -MemberType ScriptProperty -Name HasExited -Value {
            $script:F2C6ExitChecks += 1
            return $script:F2C6ExitChecks -gt 1
        }
        $fake | Add-Member -MemberType ScriptMethod -Name Refresh -Value { }
        return $fake
    }
    function Get-CimInstance {
        param([string]$ClassName, [string]$Filter, [string]$ErrorAction)
        if ($script:F2C6Scenario -ceq 'cim-error') {
            throw 'mock CIM unavailable'
        }
        $root = [pscustomobject]@{
            ProcessId = 100
            ParentProcessId = 1
            ExecutablePath = $ExecutablePath
            CommandLine = $ExecutablePath
            CreationDate = $script:F2C6StartTime
        }
        if (-not [string]::IsNullOrEmpty($Filter)) {
            return $root
        }
        $script:F2C6CimSnapshots += 1
        if (($script:F2C6CimSnapshots % 2) -eq 1) {
            $children = @($root)
            if ($script:F2C6Scenario -ne 'no-python' -and $script:F2C6Scenario -ne 'unavailable-clean') {
                $children += [pscustomobject]@{
                    ProcessId = 101
                    ParentProcessId = 100
                    ExecutablePath = 'C:\Program Files\WebView2\msedgewebview2.exe'
                    CommandLine = "msedgewebview2.exe --user-data-dir=`"$WebViewRuntimePath`""
                    CreationDate = $script:F2C6StartTime.AddMilliseconds(10)
                }
                $children += [pscustomobject]@{
                    ProcessId = 102
                    ParentProcessId = 101
                    ExecutablePath = 'C:\Program Files\Python312\python.exe'
                    CommandLine = "python.exe -I -u -B `"$BridgePath`""
                    CreationDate = $script:F2C6StartTime.AddMilliseconds(20)
                }
            }
            return $children
        }
        if ($script:F2C6Scenario -ceq 'linger') {
            return @([pscustomobject]@{
                ProcessId = 102
                ParentProcessId = 101
                ExecutablePath = 'C:\Program Files\Python312\python.exe'
                CommandLine = "python.exe -I -u -B `"$BridgePath`""
                CreationDate = $script:F2C6StartTime.AddMilliseconds(20)
            })
        }
        if ($script:F2C6Scenario -ceq 'pid-reuse') {
            return @([pscustomobject]@{
                ProcessId = 102
                ParentProcessId = 1
                ExecutablePath = 'C:\Windows\System32\notepad.exe'
                CommandLine = 'notepad.exe'
                CreationDate = $script:F2C6StartTime.AddMinutes(1)
            })
        }
        return @()
    }

    function Assert-LaunchScenario {
        param([string]$Name, [bool]$Unavailable, [bool]$ShouldFail, [string]$ExpectedError = '')
        $script:F2C6Scenario = $Name
        $script:F2C6ExitChecks = 0
        $script:F2C6CimSnapshots = 0
        $script:F2C6StartTime = [DateTime]::UtcNow
        $failed = $false
        $failureMessage = ''
        try {
            Invoke-Launch -Unavailable $Unavailable
        } catch {
            $failed = $true
            $failureMessage = $_.Exception.Message
        }
        Assert-True ($failed -eq $ShouldFail) "Unexpected Invoke-Launch result for scenario: $Name ($failureMessage)"
        if ($ShouldFail -and -not [string]::IsNullOrEmpty($ExpectedError)) {
            Assert-True ($failureMessage.IndexOf($ExpectedError, [System.StringComparison]::Ordinal) -ge 0) "Wrong failure for scenario: $Name ($failureMessage)"
        }
    }

    Assert-LaunchScenario -Name 'pid-reuse' -Unavailable $false -ShouldFail $false
    Assert-LaunchScenario -Name 'nonzero-exit' -Unavailable $false -ShouldFail $true -ExpectedError 'APP_EXIT_NONZERO'
    Assert-LaunchScenario -Name 'linger' -Unavailable $false -ShouldFail $true -ExpectedError 'CANARY_CHILD_REMAINS'
    Assert-LaunchScenario -Name 'no-python' -Unavailable $false -ShouldFail $true -ExpectedError 'SIDECAR_NOT_OBSERVED'
    Assert-LaunchScenario -Name 'cim-error' -Unavailable $false -ShouldFail $true -ExpectedError 'MONITOR_UNAVAILABLE'
    Assert-LaunchScenario -Name 'unavailable-clean' -Unavailable $true -ShouldFail $false
    Assert-LaunchScenario -Name 'unavailable-python' -Unavailable $true -ShouldFail $true -ExpectedError 'SIDECAR_UNAVAILABLE_SPAWNED'
} finally {
    [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $oldLocal, 'Process')
    [Environment]::SetEnvironmentVariable('SHIRUSHI_CANARY_SIDECAR_UNAVAILABLE', $oldUnavailable, 'Process')
    if ($PackageRoot -and (Test-Path -LiteralPath $PackageRoot)) {
        Remove-Item -LiteralPath $PackageRoot -Recurse -Force
    }
}

$hostText = Get-Content -LiteralPath $HostPath -Raw -Encoding UTF8
$libText = Get-Content -LiteralPath $LibPath -Raw -Encoding UTF8
Assert-True ($hostText -match 'SHIRUSHI_CANARY_SIDECAR_UNAVAILABLE') 'Native host must use the fixed unavailable flag.'
Assert-True ($hostText -match 'arguments\.insert\(2, "-B"\.into\(\)\)') 'Only the canary config may add Python -B.'
Assert-True ($libText -match 'compile_error!') 'Release-like manual-canary builds must fail compilation.'
Assert-True ($libText -match 'std::process::exit\(2\)') 'Canary preparation failure must exit nonzero before WebView startup.'

Write-Output 'F2C6_LAUNCHER_TEST_PASS'

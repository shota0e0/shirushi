[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'Launch', 'SidecarUnavailable')]
    [string]$Action = 'Launch',

    [string]$PythonPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$PackageRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$ExecutablePath = Join-Path $PackageRoot 'shirushi-desktop.exe'
$ManifestPath = Join-Path $PackageRoot 'manifest.json'
$SumsPath = Join-Path $PackageRoot 'SHA256SUMS'
$ProfilePath = Join-Path $PackageRoot 'demo-profile'
$VenvPath = Join-Path $PackageRoot '.venv-py312'
$VenvPythonPath = Join-Path $VenvPath 'Scripts\python.exe'
$BridgePath = Join-Path $PackageRoot 'scripts\shirushi_bridge.py'
$WebViewRuntimePath = Join-Path $ProfilePath 'Shirushi\canary-webview'
$UnavailableVariable = 'SHIRUSHI_CANARY_SIDECAR_UNAVAILABLE'

function Stop-Canary {
    param([Parameter(Mandatory = $true)][string]$Message)
    throw $Message
}

function Get-ContainedPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    if ([System.IO.Path]::IsPathRooted($RelativePath) -or $RelativePath.Contains('\') -or $RelativePath.Contains(':')) {
        Stop-Canary "Unsafe manifest path: $RelativePath"
    }
    $segments = $RelativePath.Split('/')
    if ($segments.Count -eq 0 -or @($segments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' }).Count -ne 0) {
        Stop-Canary "Unsafe manifest path: $RelativePath"
    }
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $PackageRoot ($RelativePath.Replace('/', '\'))))
    $prefix = $PackageRoot.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-Canary "Manifest path escapes the package root: $RelativePath"
    }
    return $candidate
}

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        Stop-Canary "$Label must not be a link or reparse point: $Path"
    }
}

function Assert-NoReparseChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $current = [System.IO.Path]::GetFullPath($Path)
    while ($true) {
        Assert-NoReparsePoint -Path $current -Label $Label
        if ($current -ieq $PackageRoot) {
            break
        }
        if (-not $current.StartsWith($PackageRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
            Stop-Canary "$Label escaped the package root: $Path"
        }
        $parent = [System.IO.Directory]::GetParent($current)
        if ($null -eq $parent) {
            Stop-Canary "$Label has no package-root ancestor: $Path"
        }
        $current = $parent.FullName
    }
}

function Get-PackageRelativePath {
    param([Parameter(Mandatory = $true)][string]$FullName)
    $prefix = $PackageRoot.TrimEnd('\') + '\'
    if (-not $FullName.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-Canary "Path is outside the package root: $FullName"
    }
    return $FullName.Substring($prefix.Length).Replace('\', '/')
}

function Test-RuntimeRelativePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    return $RelativePath.StartsWith('.venv-py312/', [System.StringComparison]::OrdinalIgnoreCase) -or
        $RelativePath.StartsWith('demo-profile/Shirushi/canary-webview/', [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-ImmutablePackage {
    foreach ($required in @($ManifestPath, $SumsPath, $ExecutablePath, $BridgePath, $ProfilePath)) {
        if (-not (Test-Path -LiteralPath $required)) {
            Stop-Canary "Required package component is missing: $required"
        }
    }
    Assert-NoReparsePoint -Path $PackageRoot -Label 'Package root'
    Assert-NoReparsePoint -Path $ProfilePath -Label 'Demo profile'
    if (Test-Path -LiteralPath $VenvPath) {
        Assert-NoReparsePoint -Path $VenvPath -Label 'Prepared venv'
    }
    if (Test-Path -LiteralPath $WebViewRuntimePath) {
        Assert-NoReparsePoint -Path $WebViewRuntimePath -Label 'WebView runtime directory'
    }

    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schemaVersion -ne 1 -or $manifest.artifactType -cne 'DEVELOPMENT_CANARY') {
        Stop-Canary 'manifest.json is not the expected DEVELOPMENT_CANARY schemaVersion 1 manifest.'
    }

    $declared = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
    foreach ($entry in @($manifest.files)) {
        $relative = [string]$entry.path
        if ($declared.ContainsKey($relative)) {
            Stop-Canary "Duplicate manifest path: $relative"
        }
        $full = Get-ContainedPath -RelativePath $relative
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            Stop-Canary "Manifest file is missing: $relative"
        }
        Assert-NoReparseChain -Path $full -Label 'Manifest file'
        $item = Get-Item -LiteralPath $full -Force
        if ([Int64]$entry.size -ne [Int64]$item.Length) {
            Stop-Canary "Manifest size mismatch: $relative"
        }
        $hash = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -cne ([string]$entry.sha256).ToLowerInvariant()) {
            Stop-Canary "Manifest SHA-256 mismatch: $relative"
        }
        $declared.Add($relative, $hash)
    }

    $actualPayload = @(
        Get-ChildItem -LiteralPath $PackageRoot -File -Recurse -Force | ForEach-Object {
            $relative = Get-PackageRelativePath -FullName $_.FullName
            if ($relative -cne 'manifest.json' -and $relative -cne 'SHA256SUMS' -and -not (Test-RuntimeRelativePath $relative)) {
                $relative
            }
        }
    )
    if ($actualPayload.Count -ne $declared.Count) {
        Stop-Canary 'Package payload closure does not match manifest.json.'
    }
    foreach ($relative in $actualPayload) {
        if (-not $declared.ContainsKey($relative)) {
            Stop-Canary "Unmanifested package payload: $relative"
        }
    }

    $sumEntries = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
    foreach ($line in @(Get-Content -LiteralPath $SumsPath -Encoding UTF8)) {
        if ($line -notmatch '^([0-9a-f]{64})  ([^\\]+)$') {
            Stop-Canary "Malformed SHA256SUMS line: $line"
        }
        $relative = $Matches[2]
        if ($relative -ceq 'SHA256SUMS' -or $sumEntries.ContainsKey($relative)) {
            Stop-Canary "Invalid or duplicate SHA256SUMS path: $relative"
        }
        $full = Get-ContainedPath -RelativePath $relative
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
            Stop-Canary "SHA256SUMS file is missing: $relative"
        }
        $actualHash = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -cne $Matches[1]) {
            Stop-Canary "SHA256SUMS mismatch: $relative"
        }
        $sumEntries.Add($relative, $actualHash)
    }
    if (-not $sumEntries.ContainsKey('manifest.json') -or $sumEntries.Count -ne ($declared.Count + 1)) {
        Stop-Canary 'SHA256SUMS must cover every manifest payload plus manifest.json, and exclude only itself.'
    }
    foreach ($relative in $declared.Keys) {
        if (-not $sumEntries.ContainsKey($relative)) {
            Stop-Canary "SHA256SUMS does not cover manifest payload: $relative"
        }
    }
}

function Get-ValidatedPython {
    param([Parameter(Mandatory = $true)][string]$Candidate)
    if (-not [System.IO.Path]::IsPathRooted($Candidate)) {
        Stop-Canary '-PythonPath must be an explicit absolute path to the already permitted Python executable.'
    }
    $item = Get-Item -LiteralPath $Candidate -Force -ErrorAction Stop
    if ($item.PSIsContainer -or $item.Name -ine 'python.exe') {
        Stop-Canary '-PythonPath must identify a python.exe file.'
    }
    Assert-NoReparsePoint -Path $item.FullName -Label 'Allowed Python interpreter'
    $identity = & $item.FullName -I -B -c 'import platform,struct,sys; print("%d.%d.%d|%d|%s" % (sys.version_info[:3] + (struct.calcsize("P")*8, platform.python_implementation())))'
    if ($LASTEXITCODE -ne 0 -or @($identity).Count -ne 1 -or [string]$identity -cne '3.12.10|64|CPython') {
        Stop-Canary 'The explicit interpreter must be CPython 3.12.10 x64.'
    }
    return [System.IO.Path]::GetFullPath($item.FullName)
}

function Invoke-WithCanaryEnvironment {
    param(
        [Parameter(Mandatory = $true)][bool]$Unavailable,
        [Parameter(Mandatory = $true)][scriptblock]$Body
    )
    $oldLocalAppData = [Environment]::GetEnvironmentVariable('LOCALAPPDATA', 'Process')
    $oldUnavailable = [Environment]::GetEnvironmentVariable($UnavailableVariable, 'Process')
    try {
        [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $ProfilePath, 'Process')
        if ($Unavailable) {
            [Environment]::SetEnvironmentVariable($UnavailableVariable, '1', 'Process')
        } else {
            [Environment]::SetEnvironmentVariable($UnavailableVariable, $null, 'Process')
        }
        & $Body
    } finally {
        [Environment]::SetEnvironmentVariable('LOCALAPPDATA', $oldLocalAppData, 'Process')
        [Environment]::SetEnvironmentVariable($UnavailableVariable, $oldUnavailable, 'Process')
    }
}

function Get-ExactPackageProcesses {
    try {
        return @(Get-CimInstance Win32_Process -Filter "Name = 'shirushi-desktop.exe'" | Where-Object {
            $_.ExecutablePath -and ([System.IO.Path]::GetFullPath([string]$_.ExecutablePath) -ieq $ExecutablePath)
        })
    } catch {
        Stop-Canary "Unable to inspect exact process identity; no launch was attempted and the result remains PENDING. $($_.Exception.Message)"
    }
}

function Get-DescendantProcessSnapshot {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)
    $all = @(Get-CimInstance Win32_Process)
    $ids = New-Object 'System.Collections.Generic.HashSet[int]'
    [void]$ids.Add($RootProcessId)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($candidate in $all) {
            if (-not $ids.Contains([int]$candidate.ProcessId) -and $ids.Contains([int]$candidate.ParentProcessId)) {
                [void]$ids.Add([int]$candidate.ProcessId)
                $changed = $true
            }
        }
    }
    return @($all | Where-Object { $_.ProcessId -ne $RootProcessId -and $ids.Contains([int]$_.ProcessId) })
}

function Test-ExplicitPolicyBlock {
    param([Parameter(Mandatory = $true)][System.Exception]$Exception)
    if ($Exception -is [System.ComponentModel.Win32Exception] -and $Exception.NativeErrorCode -eq 1260) {
        return $true
    }
    return $Exception.Message -match 'AppLocker|WDAC|application control|blocked by group policy|blocked by your administrator|security policy'
}

function Invoke-Prepare {
    if ([string]::IsNullOrWhiteSpace($PythonPath)) {
        Stop-Canary 'Prepare requires -PythonPath with the explicit absolute path to already permitted CPython 3.12.10 x64.'
    }
    if (Test-Path -LiteralPath $VenvPath) {
        Stop-Canary 'The package-local .venv-py312 already exists. Use a fresh extracted package; Prepare never overwrites it.'
    }
    $python = Get-ValidatedPython -Candidate $PythonPath
    Invoke-WithCanaryEnvironment -Unavailable $false -Body {
        & $python -I -B -m venv --without-pip $VenvPath
        if ($LASTEXITCODE -ne 0) {
            Stop-Canary 'Python could not create the package-local no-pip venv. Use a fresh extracted package before retrying.'
        }
    }
    if (-not (Test-Path -LiteralPath $VenvPythonPath -PathType Leaf)) {
        Stop-Canary 'The no-pip venv did not produce its fixed python.exe.'
    }
    Assert-NoReparsePoint -Path $VenvPath -Label 'Prepared venv'
    Assert-NoReparseChain -Path $VenvPythonPath -Label 'Prepared venv Python'
    $venvIdentity = & $VenvPythonPath -I -B -c 'import platform,struct,sys; print("%d.%d.%d|%d|%s" % (sys.version_info[:3] + (struct.calcsize("P")*8, platform.python_implementation())))'
    if ($LASTEXITCODE -ne 0 -or @($venvIdentity).Count -ne 1 -or [string]$venvIdentity -cne '3.12.10|64|CPython') {
        Stop-Canary 'The prepared venv identity check failed.'
    }
    Write-Host 'Prepared fresh package-local CPython 3.12.10 x64 venv without pip.'
}

function Invoke-Launch {
    param([Parameter(Mandatory = $true)][bool]$Unavailable)
    if (-not $Unavailable -and -not (Test-Path -LiteralPath $VenvPythonPath -PathType Leaf)) {
        Stop-Canary 'Normal launch requires Prepare to create the fixed package-local no-pip venv first.'
    }
    if (-not $Unavailable) {
        Assert-NoReparseChain -Path $VenvPythonPath -Label 'Prepared venv Python'
    }
    if (@(Get-ExactPackageProcesses).Count -ne 0) {
        Stop-Canary 'This exact canary executable is already running; duplicate concurrent launch was refused.'
    }

    $createdNew = $false
    $mutex = New-Object System.Threading.Mutex($true, 'Local\Shirushi-F2C6-Development-Canary', [ref]$createdNew)
    if (-not $createdNew) {
        $mutex.Dispose()
        Stop-Canary 'Another Run-Canary launcher owns the canary run lock.'
    }
    try {
        $process = $null
        try {
            $process = Invoke-WithCanaryEnvironment -Unavailable $Unavailable -Body {
                Start-Process -FilePath $ExecutablePath -PassThru
            }
        } catch [System.UnauthorizedAccessException] {
            if (Test-ExplicitPolicyBlock -Exception $_.Exception) {
                Stop-Canary "LOCAL POLICY BLOCKED: Windows reported an explicit policy rejection. $($_.Exception.Message)"
            }
            Stop-Canary "Windows denied or failed to execute the canary; no policy cause was established and the result remains PENDING. $($_.Exception.Message)"
        } catch [System.ComponentModel.Win32Exception] {
            if (Test-ExplicitPolicyBlock -Exception $_.Exception) {
                Stop-Canary "LOCAL POLICY BLOCKED: Windows reported an explicit policy rejection. $($_.Exception.Message)"
            }
            Stop-Canary "Windows failed to execute the canary; no policy cause was established and the result remains PENDING. $($_.Exception.Message)"
        }
        if ($null -eq $process) {
            Stop-Canary 'The canary process was not created.'
        }

        $processStartTicks = $process.StartTime.ToUniversalTime().Ticks
        $observedChildren = @{}
        $observedPython = $false
        $monitoringEstablished = $true
        try {
            $native = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
            if ($null -eq $native -or -not $native.ExecutablePath -or
                ([System.IO.Path]::GetFullPath([string]$native.ExecutablePath) -ine $ExecutablePath)) {
                Stop-Canary 'Created process identity could not be corroborated with the fixed canary executable path.'
            }
            $nativeStartTicks = ([DateTime]$native.CreationDate).ToUniversalTime().Ticks
            if ([Math]::Abs($nativeStartTicks - $processStartTicks) -gt [TimeSpan]::FromSeconds(2).Ticks) {
                Stop-Canary 'Created process creation time could not be corroborated.'
            }
        } catch {
            $monitoringEstablished = $false
            Write-Warning "Unable to establish exact process monitoring: $($_.Exception.Message)"
        }

        Write-Host "Started DEVELOPMENT CANARY PID $($process.Id). Close the Shirushi window to complete this run."
        while (-not $process.HasExited) {
            if ($monitoringEstablished) {
                try {
                    foreach ($candidate in @(Get-DescendantProcessSnapshot -RootProcessId $process.Id)) {
                        if (-not $candidate.ExecutablePath -or -not $candidate.CreationDate) {
                            Stop-Canary 'A descendant process could not be corroborated by executable path and creation time.'
                        }
                        $kind = 'descendant'
                        $candidatePath = [System.IO.Path]::GetFullPath([string]$candidate.ExecutablePath)
                        $candidateName = [System.IO.Path]::GetFileName($candidatePath)
                        if ($candidateName -match '^python(w)?\.exe$' -and $candidate.CommandLine -and
                            ([string]$candidate.CommandLine).IndexOf($BridgePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                            $kind = 'python'
                            $observedPython = $true
                        } elseif ($candidate.CommandLine -and
                            ([string]$candidate.CommandLine).IndexOf($WebViewRuntimePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                            $kind = 'webview'
                        }
                        $observedChildren[[int]$candidate.ProcessId] = @{
                            CreationDate = [string]$candidate.CreationDate
                            ExecutablePath = [System.IO.Path]::GetFullPath([string]$candidate.ExecutablePath)
                            Kind = $kind
                        }
                    }
                } catch {
                    $monitoringEstablished = $false
                    Write-Warning "Exact child-process monitoring became unavailable: $($_.Exception.Message)"
                }
            }
            Start-Sleep -Milliseconds 300
            $process.Refresh()
        }

        $remaining = New-Object 'System.Collections.Generic.HashSet[int]'
        $remainingPython = New-Object 'System.Collections.Generic.HashSet[int]'
        if ($monitoringEstablished) {
            try {
                foreach ($candidate in @(Get-CimInstance Win32_Process)) {
                    $pidValue = [int]$candidate.ProcessId
                    if ($observedChildren.ContainsKey($pidValue)) {
                        $record = $observedChildren[$pidValue]
                        if ($candidate.ExecutablePath -and
                            ([string]$candidate.CreationDate -ceq [string]$record.CreationDate) -and
                            ([System.IO.Path]::GetFullPath([string]$candidate.ExecutablePath) -ieq [string]$record.ExecutablePath)) {
                            [void]$remaining.Add($pidValue)
                            if ($record.Kind -ceq 'python') {
                                [void]$remainingPython.Add($pidValue)
                            }
                        }
                    } elseif ($candidate.ExecutablePath -and $candidate.CommandLine -and $candidate.CreationDate -and
                        ([DateTime]$candidate.CreationDate).ToUniversalTime().Ticks -ge $processStartTicks) {
                        $candidatePath = [System.IO.Path]::GetFullPath([string]$candidate.ExecutablePath)
                        $candidateName = [System.IO.Path]::GetFileName($candidatePath)
                        if ($candidateName -match '^python(w)?\.exe$' -and
                            ([string]$candidate.CommandLine).IndexOf($BridgePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                            [void]$remaining.Add($pidValue)
                            [void]$remainingPython.Add($pidValue)
                        } elseif (([string]$candidate.CommandLine).IndexOf($WebViewRuntimePath, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                            [void]$remaining.Add($pidValue)
                        }
                    }
                }
            } catch {
                $monitoringEstablished = $false
            }
            if ($monitoringEstablished) {
                Write-Host "Observed remaining canary-owned child processes: $($remaining.Count)"
                Write-Host "Observed remaining canary-owned Python processes: $($remainingPython.Count)"
            }
        }
        Write-Host "Canary PID $($process.Id), creation ticks $processStartTicks, exited with code $($process.ExitCode)."
        if (-not $monitoringEstablished) {
            Stop-Canary 'MONITOR_UNAVAILABLE: exact remaining-child evidence could not be established; the launcher result is failure/PENDING.'
        }
        if ($process.ExitCode -ne 0) {
            Stop-Canary "APP_EXIT_NONZERO: the canary exited with code $($process.ExitCode); the launcher result is failure/PENDING."
        }
        if ($Unavailable -and $observedPython) {
            Stop-Canary 'SIDECAR_UNAVAILABLE_SPAWNED: a Python sidecar was observed in the no-spawn scenario.'
        }
        if (-not $Unavailable -and -not $observedPython) {
            Stop-Canary 'SIDECAR_NOT_OBSERVED: normal launch did not establish the expected Python child; no zero count is claimed.'
        }
        if ($remaining.Count -ne 0) {
            Stop-Canary "CANARY_CHILD_REMAINS: $($remaining.Count) corroborated canary-owned child process(es) remain."
        }
    } finally {
        $mutex.ReleaseMutex()
        $mutex.Dispose()
    }
}

try {
    Assert-ImmutablePackage
    if ($Action -ne 'Prepare' -and -not [string]::IsNullOrEmpty($PythonPath)) {
        Stop-Canary '-PythonPath is accepted only by Prepare; launches use only the fixed package executable and venv.'
    }
    switch ($Action) {
        'Prepare' { Invoke-Prepare }
        'Launch' { Invoke-Launch -Unavailable $false }
        'SidecarUnavailable' { Invoke-Launch -Unavailable $true }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}

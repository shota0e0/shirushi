[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PackageDir,
    [Parameter(Mandatory = $true)][string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$caseRoot = Join-Path $env:RUNNER_TEMP ('f2c7-equivalence-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $caseRoot -ErrorAction Stop | Out-Null

function New-CasePair {
    param([string]$Name)
    $psPath = Join-Path $caseRoot ($Name + '-ps')
    $nativePath = Join-Path $caseRoot ($Name + '-native')
    Copy-Item -LiteralPath $PackageDir -Destination $psPath -Recurse -Force
    Copy-Item -LiteralPath $PackageDir -Destination $nativePath -Recurse -Force
    return @($psPath, $nativePath)
}

function Invoke-Reference {
    param([string]$Root, [string]$Action, [string]$Candidate)
    $arguments = @('-NoProfile', '-File', (Join-Path $Root 'Run-Canary.ps1'), '-Action', $Action)
    if ($null -ne $Candidate) { $arguments += @('-PythonPath', $Candidate) }
    $output = & pwsh @arguments 2>&1
    return @{ Code = $LASTEXITCODE; Output = @($output) -join '\n' }
}

function Invoke-Native {
    param([string]$Root, [string]$Action, [string]$Candidate)
    $arguments = @($Action)
    if ($null -ne $Candidate) { $arguments += @('--python', $Candidate) }
    $output = & (Join-Path $Root 'shirushi-canary-runner.exe') @arguments 2>&1
    return @{ Code = $LASTEXITCODE; Output = @($output) -join '\n' }
}

function Assert-Equivalent {
    param([string]$Name, [string]$ReferenceRoot, [string]$NativeRoot,
          [string]$PsAction, [string]$NativeAction, [string]$Candidate,
          [bool]$ShouldPass)
    $reference = Invoke-Reference -Root $ReferenceRoot -Action $PsAction -Candidate $Candidate
    $native = Invoke-Native -Root $NativeRoot -Action $NativeAction -Candidate $Candidate
    $referencePass = $reference.Code -eq 0
    $nativePass = $native.Code -eq 0
    if ($referencePass -ne $ShouldPass -or $nativePass -ne $ShouldPass) {
        throw "Equivalence mismatch: $Name (reference=$($reference.Code), native=$($native.Code))"
    }
    if ($ShouldPass -and $NativeAction -eq 'prepare' -and $native.Output -notmatch 'PREPARE_OK') {
        throw "Native successful Prepare lacked PREPARE_OK: $Name"
    }
    Write-Host "EQUIVALENCE PASS: $Name (reference=$($reference.Code), native=$($native.Code))"
}

$missing = New-CasePair -Name 'launch-without-prepare'
Assert-Equivalent -Name 'launch-without-prepare' -ReferenceRoot $missing[0] -NativeRoot $missing[1] -PsAction Launch -NativeAction launch -Candidate $null -ShouldPass $false
$relative = New-CasePair -Name 'relative-python'
Assert-Equivalent -Name 'relative-python' -ReferenceRoot $relative[0] -NativeRoot $relative[1] -PsAction Prepare -NativeAction prepare -Candidate 'python.exe' -ShouldPass $false
$wrongName = New-CasePair -Name 'wrong-python-name'
$badCandidate = (Get-Command pwsh).Source
Assert-Equivalent -Name 'wrong-python-name' -ReferenceRoot $wrongName[0] -NativeRoot $wrongName[1] -PsAction Prepare -NativeAction prepare -Candidate $badCandidate -ShouldPass $false
$wrongIdentity = New-CasePair -Name 'wrong-interpreter-identity'
$fakePythonDir = Join-Path $caseRoot 'wrong-interpreter'
New-Item -ItemType Directory -Path $fakePythonDir -ErrorAction Stop | Out-Null
$fakePython = Join-Path $fakePythonDir 'python.exe'
Copy-Item -LiteralPath $badCandidate -Destination $fakePython -ErrorAction Stop
Assert-Equivalent -Name 'wrong-interpreter-identity' -ReferenceRoot $wrongIdentity[0] -NativeRoot $wrongIdentity[1] -PsAction Prepare -NativeAction prepare -Candidate $fakePython -ShouldPass $false
$tampered = New-CasePair -Name 'tampered-payload'
Add-Content -LiteralPath (Join-Path $tampered[0] 'README.md') -Value 'tampered'
Add-Content -LiteralPath (Join-Path $tampered[1] 'README.md') -Value 'tampered'
Assert-Equivalent -Name 'tampered-payload' -ReferenceRoot $tampered[0] -NativeRoot $tampered[1] -PsAction Prepare -NativeAction prepare -Candidate $PythonPath -ShouldPass $false
$valid = New-CasePair -Name 'valid-prepare'
Assert-Equivalent -Name 'valid-prepare' -ReferenceRoot $valid[0] -NativeRoot $valid[1] -PsAction Prepare -NativeAction prepare -Candidate $PythonPath -ShouldPass $true
foreach ($root in $valid) {
    if (-not (Test-Path -LiteralPath (Join-Path $root '.venv-py312/Scripts/python.exe') -PathType Leaf)) {
        throw 'Successful Prepare did not create fixed venv Python'
    }
}
Assert-Equivalent -Name 'existing-venv-rejected' -ReferenceRoot $valid[0] -NativeRoot $valid[1] -PsAction Prepare -NativeAction prepare -Candidate $PythonPath -ShouldPass $false
Write-Host 'F2C.7 Prepare equivalence: PASS; no Desktop window was launched in CI.'

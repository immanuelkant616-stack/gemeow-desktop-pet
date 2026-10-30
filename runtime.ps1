# 이 파일은 실행기와 설치기가 함께 사용하는 Python 검색 함수입니다.
$script:GemeowRuntimeRoot = $PSScriptRoot

function Test-GemeowPython {
    param([string]$Executable, [switch]$RequirePackages)
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    $gemeowCheck = 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
    if ($RequirePackages) {
        $gemeowCheck = 'import sys; import PyQt5; from google import genai; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'
    }
    try {
        & $Executable -c $gemeowCheck *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-GemeowPython {
    param([switch]$SystemOnly, [switch]$RequirePackages)
    $gemeowCandidates = [System.Collections.Generic.List[string]]::new()
    if (-not $SystemOnly) {
        $gemeowCandidates.Add((Join-Path $script:GemeowRuntimeRoot '.venv\Scripts\python.exe'))
    }
    $gemeowBases = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:USERPROFILE 'AppData\Local\Programs\Python')
    ) | Select-Object -Unique
    foreach ($gemeowBase in $gemeowBases) {
        if (Test-Path -LiteralPath $gemeowBase) {
            Get-ChildItem -LiteralPath $gemeowBase -Directory -Filter 'Python3*' |
                Sort-Object Name -Descending |
                ForEach-Object { $gemeowCandidates.Add((Join-Path $_.FullName 'python.exe')) }
        }
    }
    $gemeowLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($gemeowLauncher) {
        try {
            $gemeowLocated = & $gemeowLauncher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $gemeowLocated) { $gemeowCandidates.Add([string]$gemeowLocated) }
        } catch { }
    }
    foreach ($gemeowName in @('python.exe', 'python3.exe')) {
        $gemeowCommand = Get-Command $gemeowName -ErrorAction SilentlyContinue
        if ($gemeowCommand -and $gemeowCommand.Source -notlike '*\WindowsApps\*') {
            $gemeowCandidates.Add($gemeowCommand.Source)
        }
    }
    foreach ($gemeowCandidate in ($gemeowCandidates | Select-Object -Unique)) {
        if (Test-GemeowPython -Executable $gemeowCandidate -RequirePackages:$RequirePackages) {
            return $gemeowCandidate
        }
    }
    return $null
}


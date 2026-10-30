$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
try {
    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'Gemeow.py'))) {
        throw '프로그램 파일을 찾지 못했어요. ZIP 파일을 모두 압축 해제한 폴더에서 start.cmd를 실행해 주세요.'
    }
    $gemeowPython = Get-GemeowPython -RequirePackages
    if (-not $gemeowPython) {
        if (Get-GemeowPython) {
            throw '필요한 패키지가 아직 없어요. 같은 폴더의 install.cmd를 한 번 실행한 뒤 start.cmd를 다시 실행해 주세요.'
        }
        throw 'Python 3.10 이상을 찾지 못했어요. 처음_실행하기.md의 Python 설치 단계를 따라 설치한 뒤 install.cmd를 실행해 주세요.'
    }
    $gemeowPythonw = Join-Path (Split-Path $gemeowPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $gemeowPythonw)) { $gemeowPythonw = $gemeowPython }
    $gemeowEntry = '"' + (Join-Path $PSScriptRoot 'Gemeow.py') + '"'
    # pythonw has no console. The companion is interactive and must not inherit
    # a hidden startup state for its regular chat/settings windows.
    Start-Process -FilePath $gemeowPythonw -ArgumentList $gemeowEntry -WorkingDirectory $PSScriptRoot -WindowStyle Normal
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        $_.Exception.Message, '제미곰 실행 안내',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
    exit 1
}


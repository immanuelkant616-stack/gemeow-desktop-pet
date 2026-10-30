$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime.ps1')
try {
    Write-Host ''
    Write-Host '제미곰에 필요한 파일을 준비합니다.' -ForegroundColor Cyan
    Write-Host '이 폴더 안의 .venv에만 설치하므로 기존 Python 패키지는 바꾸지 않습니다.'
    $gemeowPython = Get-GemeowPython -SystemOnly
    if (-not $gemeowPython) {
        throw 'Python 3.10 이상을 찾지 못했어요. 처음_실행하기.md의 Python 설치 단계를 완료한 뒤 다시 실행해 주세요.'
    }
    $gemeowVenv = Join-Path $PSScriptRoot '.venv'
    Write-Host ''
    Write-Host '[1/2] 프로그램 전용 Python 환경을 준비하고 있어요...'
    & $gemeowPython -m venv $gemeowVenv
    if ($LASTEXITCODE -ne 0) {
        throw '전용 환경을 만들지 못했어요. 압축을 모두 풀었는지, 이 폴더에 파일을 쓸 수 있는지 확인해 주세요.'
    }
    $gemeowVenvPython = Join-Path $gemeowVenv 'Scripts\python.exe'
    Write-Host '[2/2] 필요한 패키지를 내려받고 있어요. 인터넷 연결을 유지해 주세요...'
    & $gemeowVenvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) {
        throw '패키지 설치를 완료하지 못했어요. 인터넷 연결을 확인한 뒤 install.cmd를 다시 실행해 주세요. 처음_실행하기.md에도 해결 순서를 정리했습니다.'
    }
    if (-not (Test-GemeowPython -Executable $gemeowVenvPython -RequirePackages)) {
        throw '설치 후 실행 확인에 실패했어요. 처음_실행하기.md의 문제 해결 안내를 확인해 주세요.'
    }
    Write-Host ''
    Write-Host '준비가 끝났어요! 창을 닫고 start.cmd를 더블클릭해 주세요.' -ForegroundColor Green
} catch {
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Yellow
    exit 1
}


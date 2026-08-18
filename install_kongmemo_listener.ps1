$ErrorActionPreference = "Stop"

$apiKey = $env:KONGMEMO_API_KEY
$projectKey = $env:KONGMEMO_PROJECT_KEY
if ([string]::IsNullOrWhiteSpace($apiKey) -or [string]::IsNullOrWhiteSpace($projectKey)) {
    Write-Host "오류: 필요한 환경 변수가 설정되지 않았습니다." -ForegroundColor Red
    Write-Host "앱의 프로젝트 목록에서 명령어를 다시 복사하여 실행해주세요."
    exit 1
}

# 리스너가 작업할 폴더 = 지금 이 명령어를 붙여넣은 그 폴더(cd로 이동해둔 자리).
# 리스너 프로그램 파일(venv 포함)은 이 폴더를 더럽히지 않도록 별도의 고정 위치에
# 설치하지만, 실제로 실행할 때는 이 폴더로 되돌아와서 실행한다.
$projectDir = Get-Location
$installDir = Join-Path $env:USERPROFILE ".kongmemo_listener"

if (-not (Test-Path $installDir)) {
    Write-Host "[1/4] 리스너 프로그램 폴더 생성 중... ($installDir)" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
} else {
    Write-Host "[1/4] 리스너 프로그램 폴더 확인됨. ($installDir)" -ForegroundColor Cyan
}

Write-Host "[2/4] 리스너 스크립트 다운로드 중..." -ForegroundColor Cyan
$scriptUrl = "https://raw.githubusercontent.com/pilsunheo/KongMemo_Listener_Public/main/kongmemo_agent_listener.py"
Invoke-WebRequest -Uri $scriptUrl -OutFile (Join-Path $installDir "kongmemo_agent_listener.py")

Write-Host "[3/4] Python 가상환경 설정 및 패키지 설치 중..." -ForegroundColor Cyan
$venvDir = Join-Path $installDir "venv"
if (-not (Test-Path $venvDir)) {
    python -m venv $venvDir
}
$pythonExe = Join-Path $venvDir "Scripts\python.exe"

& $pythonExe -m pip install -q --upgrade pip
& $pythonExe -m pip install -q supabase python-dotenv claude-agent-sdk

Write-Host "[4/4] 환경 변수 설정 중..." -ForegroundColor Cyan
$envContent = @"
KONGMEMO_SUPABASE_URL=https://iiaqvhqnafqrrxizlofl.supabase.co
KONGMEMO_SUPABASE_ANON_KEY=sb_publishable_hh_54DP6aBLlXMVhA__Ulg_EEPrBWKu
KONGMEMO_API_KEY=$apiKey
KONGMEMO_PROJECT_KEY=$projectKey
"@
Set-Content -Path (Join-Path $installDir ".env") -Value $envContent -Encoding UTF8

Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host " KongMemo PC Listener가 성공적으로 설치되었습니다!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "작업 폴더: $projectDir" -ForegroundColor Yellow
Write-Host "지금부터 이 폴더를 기준으로 리스너를 실행합니다. 이 창을 닫으면 연결이 끊어집니다." -ForegroundColor Yellow

# venv/스크립트는 고정 위치에 있지만, 실제 실행은 항상 사용자가 명령어를 붙여넣은
# 그 폴더(프로젝트 폴더)에서 이뤄져야 한다 - 리스너는 자신의 cwd를 작업 폴더로 쓴다.
Set-Location $projectDir
& $pythonExe (Join-Path $installDir "kongmemo_agent_listener.py")

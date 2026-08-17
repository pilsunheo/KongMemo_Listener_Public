$ErrorActionPreference = "Stop"

$apiKey = $env:KONGMEMO_API_KEY
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Host "오류: KONGMEMO_API_KEY 환경 변수가 설정되지 않았습니다." -ForegroundColor Red
    Write-Host "명령어를 다시 복사하여 실행해주세요."
    exit 1
}

$installDir = Join-Path $env:USERPROFILE ".kongmemo_listener"

if (-not (Test-Path $installDir)) {
    Write-Host "[1/4] 리스너 폴더 생성 중... ($installDir)" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
} else {
    Write-Host "[1/4] 리스너 폴더 확인됨. ($installDir)" -ForegroundColor Cyan
}

Set-Location $installDir

Write-Host "[2/4] 리스너 스크립트 다운로드 중..." -ForegroundColor Cyan
$scriptUrl = "https://raw.githubusercontent.com/pilsunheo/KongMemo_Listener_Public/main/kongmemo_agent_listener.py"
Invoke-WebRequest -Uri $scriptUrl -OutFile "kongmemo_agent_listener.py"

Write-Host "[3/4] Python 가상환경 설정 및 패키지 설치 중..." -ForegroundColor Cyan
if (-not (Test-Path "venv")) {
    python -m venv venv
}

.\venv\Scripts\python.exe -m pip install -q --upgrade pip
.\venv\Scripts\python.exe -m pip install -q supabase python-dotenv claude-agent-sdk

Write-Host "[4/4] 환경 변수 설정 중..." -ForegroundColor Cyan
$envContent = @"
KONGMEMO_SUPABASE_URL=https://iiaqvhqnafqrrxizlofl.supabase.co
KONGMEMO_SUPABASE_ANON_KEY=sb_publishable_hh_54DP6aBLlXMVhA__Ulg_EEPrBWKu
KONGMEMO_API_KEY=$apiKey
"@
Set-Content -Path ".env" -Value $envContent -Encoding UTF8

Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host " KongMemo PC Listener가 성공적으로 설치되었습니다!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "지금부터 리스너를 실행합니다. 이 창을 닫으면 연결이 끊어집니다." -ForegroundColor Yellow

.\venv\Scripts\python.exe kongmemo_agent_listener.py

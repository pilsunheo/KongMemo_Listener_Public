$ErrorActionPreference = "Stop"

$apiKey = $env:KONGMEMO_API_KEY
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Host "오류: KONGMEMO_API_KEY가 설정되지 않았습니다." -ForegroundColor Red
    Write-Host "콩메모 앱의 'AI 커맨드 센터 > API & 확장'에서 명령어를 다시 복사해 실행해주세요."
    exit 1
}

# 리스너와 달리 MCP 서버는 사용자가 직접 실행하지 않는다. 클로드 코드가 필요할 때
# 알아서 켜고 끄므로, 설치 위치는 작업 폴더와 무관한 고정 위치면 된다.
$installDir = Join-Path $env:USERPROFILE ".kongmemo_mcp"

Write-Host "[1/4] 설치 폴더 준비 중... ($installDir)" -ForegroundColor Cyan
if (-not (Test-Path $installDir)) {
    New-Item -ItemType Directory -Force -Path $installDir | Out-Null
}

Write-Host "[2/4] MCP 서버 다운로드 중..." -ForegroundColor Cyan
$scriptUrl = "https://raw.githubusercontent.com/pilsunheo/KongMemo_Listener_Public/main/kongmemo_mcp_server.py"
$serverPath = Join-Path $installDir "kongmemo_mcp_server.py"
Invoke-WebRequest -Uri $scriptUrl -OutFile $serverPath

Write-Host "[3/4] Python 가상환경 설정 및 패키지 설치 중..." -ForegroundColor Cyan
$venvDir = Join-Path $installDir "venv"
if (-not (Test-Path $venvDir)) {
    python -m venv $venvDir
}
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
& $pythonExe -m pip install -q --upgrade pip
& $pythonExe -m pip install -q mcp supabase python-dotenv

Write-Host "[4/4] 클로드 코드에 등록 중..." -ForegroundColor Cyan
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    Write-Host ""
    Write-Host "클로드 코드(claude 명령어)를 찾을 수 없습니다." -ForegroundColor Yellow
    Write-Host "설치 자체는 끝났으니, 클로드 코드를 설치한 뒤 아래 한 줄만 실행해주세요:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "claude mcp add kongmemo -s user -e KONGMEMO_API_KEY=$apiKey -- `"$pythonExe`" `"$serverPath`"" -ForegroundColor White
    exit 0
}

# 이미 등록돼 있으면 add가 실패하므로 먼저 지운다(없으면 조용히 넘어감).
try { & claude mcp remove kongmemo -s user 2>$null | Out-Null } catch {}
& claude mcp add kongmemo -s user -e "KONGMEMO_API_KEY=$apiKey" -- "$pythonExe" "$serverPath"

Write-Host ""
Write-Host "===============================================" -ForegroundColor Green
Write-Host " 콩메모 MCP가 연결되었습니다!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""
Write-Host "사용법: 클로드 코드를 '새로 켠 뒤' 그냥 한국어로 말하면 됩니다." -ForegroundColor Yellow
Write-Host "  예) 콩메모에서 유튜브 링크 다 찾아서 정리해줘" -ForegroundColor White
Write-Host "  예) 콩메모 '중요 메모' 방에 내일 회의 준비라고 추가해줘" -ForegroundColor White
Write-Host ""
Write-Host "이미 켜져 있던 창에서는 보이지 않습니다 - 클로드 코드는 시작할 때 도구를 읽어들입니다." -ForegroundColor Yellow

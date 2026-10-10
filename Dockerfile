# 콩메모 MCP 서버(kongmemo_mcp_server.py)용 Dockerfile.
#
# KONGMEMO_API_KEY 없이도 서버 프로세스 자체는 정상적으로 뜨고 MCP 초기화/
# introspection(tools/list 등)에 응답한다 - 실제 데이터 조회/쓰기 도구만 그때
# "API 키가 없다"는 메시지를 돌려줄 뿐이다(kongmemo_mcp_server.py의 _require_key
# 참고). 그래서 이 이미지는 빌드 인자나 런타임 환경변수 없이 그대로 띄우기만 해도
# 정상 동작 확인(헬스체크)이 가능하다.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kongmemo_mcp_server.py .

# 콩메모 MCP 서버는 표준입출력(stdio)으로 통신한다(server.run(transport="stdio")).
ENTRYPOINT ["python", "kongmemo_mcp_server.py"]

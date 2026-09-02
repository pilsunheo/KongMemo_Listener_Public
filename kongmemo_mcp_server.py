"""콩메모 MCP 서버.

클로드 코드(또는 다른 MCP 클라이언트)가 콩메모에 쌓인 메모를 직접 읽고 쓰게 해준다.
PC 리스너(kongmemo_agent_listener.py)가 "폰에서 PC를 시킨다"면, 이쪽은 반대로
"PC에서 콩메모 데이터를 본다".

인증은 PC 리스너와 완전히 같은 방식이다. 앱의 AI 커맨드 센터에서 발급한 API 키를
SHA-256으로 해시해서 넘기면, Supabase의 SECURITY DEFINER 함수(010_mcp_server_rpcs.sql)가
그 키의 주인을 찾아 그 사람 데이터만 돌려준다. 즉 이 스크립트 자체는 아무 권한도
갖고 있지 않고, 남의 데이터는 구조적으로 볼 수 없다.

설치:
    pip install mcp supabase python-dotenv

실행에 필요한 환경변수는 KONGMEMO_API_KEY 하나뿐이다(앱에서 발급한 키).
Supabase 주소와 익명 키는 앱에 이미 공개되어 있는 값이라 기본값으로 넣어두었다.

클로드 코드에 등록하는 법:
    claude mcp add kongmemo -e KONGMEMO_API_KEY=<발급받은 키> -- python <이 파일의 경로>
"""

import hashlib
import json
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from supabase import AsyncClient, create_async_client

load_dotenv()

# 앱(lib/main.dart)에 그대로 들어있는 공개 값. 비밀이 아니므로 기본값으로 둔다 -
# 사용자가 직접 채워야 하는 것은 자기 API 키 하나뿐이어야 설치가 쉬워진다.
SUPABASE_URL = os.environ.get(
    "KONGMEMO_SUPABASE_URL", "https://iiaqvhqnafqrrxizlofl.supabase.co"
)
SUPABASE_ANON_KEY = os.environ.get(
    "KONGMEMO_SUPABASE_ANON_KEY", "sb_publishable_hh_54DP6aBLlXMVhA__Ulg_EEPrBWKu"
)

API_KEY = os.environ.get("KONGMEMO_API_KEY", "")
API_KEY_HASH = hashlib.sha256(API_KEY.encode()).hexdigest() if API_KEY else ""

server = MCPServer(
    name="kongmemo",
    version="1.0.0",
    instructions=(
        "콩메모(Kong Memo)에 저장된 사용자의 메모/링크/파일 기록을 읽고 씁니다. "
        "방(room)은 카카오톡 채팅방처럼 주제별로 메모가 모여 있는 단위입니다. "
        "먼저 list_rooms로 방을 확인한 뒤, 특정 방을 읽으려면 get_room_items를, "
        "전체에서 찾으려면 search_memos를 사용하세요."
    ),
)

_client: AsyncClient | None = None


async def _supabase() -> AsyncClient:
    """이벤트 루프가 살아난 뒤에 만들어야 해서 최초 호출 시점에 생성한다."""
    global _client
    if _client is None:
        _client = await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _client


def _require_key() -> str | None:
    """키가 없으면 도구가 조용히 빈 결과를 주는 대신 이유를 알려주게 한다."""
    if not API_KEY:
        return (
            "KONGMEMO_API_KEY 환경변수가 설정되지 않았습니다. "
            "콩메모 앱의 'AI 커맨드 센터 > API & 확장'에서 키를 발급받아 설정해주세요."
        )
    return None


async def _rpc(name: str, params: dict[str, Any]) -> Any:
    client = await _supabase()
    result = await client.rpc(name, params).execute()
    return result.data


def _as_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


@server.tool()
async def list_rooms() -> str:
    """콩메모의 방 목록을 최근 활동순으로 돌려줍니다.

    각 방의 room_id, 제목, 메모 개수, 마지막 활동 시각을 포함합니다.
    다른 도구에 넘길 room_id를 여기서 얻으세요.
    system_key가 있는 방은 앱이 자동으로 만든 기본 방입니다
    (SYS_IMPORTANT=중요 메모, SYS_YOUTUBE=유튜브 보관함, SYS_TODO=할일 목록 등).
    """
    if (error := _require_key()) is not None:
        return error
    rooms = await _rpc("mcp_list_rooms", {"p_key_hash": API_KEY_HASH})
    if not rooms:
        return "방이 없거나 API 키가 유효하지 않습니다."
    return _as_json(rooms)


@server.tool()
async def get_room_items(room_id: str, limit: int = 50) -> str:
    """특정 방에 있는 메모를 최신순으로 읽습니다.

    Args:
        room_id: list_rooms가 돌려준 room_id.
        limit: 가져올 개수(기본 50, 최대 200).

    type이 'link'면 content가 URL이고, 'file'/'image'면 파일 경로나 주소입니다.
    """
    if (error := _require_key()) is not None:
        return error
    items = await _rpc(
        "mcp_get_room_items",
        {"p_key_hash": API_KEY_HASH, "p_room_id": room_id, "p_limit": limit},
    )
    if not items:
        return "해당 방에 메모가 없거나, 그 방에 접근할 수 없습니다."
    return _as_json(items)


@server.tool()
async def search_memos(query: str, limit: int = 50, item_type: str | None = None) -> str:
    """모든 방을 통틀어 메모 내용을 검색합니다.

    Args:
        query: 찾을 문자열(부분 일치, 대소문자 무시).
        limit: 가져올 개수(기본 50, 최대 200).
        item_type: 특정 종류만 보고 싶을 때. 'text', 'link', 'image', 'file' 중 하나.
                   예를 들어 저장해둔 유튜브 링크만 모으려면 'link'를 쓰세요.

    결과에는 어느 방의 메모인지 알 수 있도록 room_title이 함께 들어갑니다.
    """
    if (error := _require_key()) is not None:
        return error
    items = await _rpc(
        "mcp_search_items",
        {
            "p_key_hash": API_KEY_HASH,
            "p_query": query,
            "p_limit": limit,
            "p_type": item_type,
        },
    )
    if not items:
        return f"'{query}'에 해당하는 메모를 찾지 못했습니다."
    return _as_json(items)


@server.tool()
async def add_memo(room_id: str, content: str, item_type: str = "text") -> str:
    """콩메모의 특정 방에 새 메모를 추가합니다.

    추가된 메모는 사용자의 폰과 웹에 자동으로 동기화되고, 앱에서는 'mcp'가 보낸
    메모로 표시됩니다.

    Args:
        room_id: list_rooms가 돌려준 room_id.
        content: 저장할 내용. item_type이 'link'면 URL을 넣으세요.
        item_type: 'text'(기본) 또는 'link'.
    """
    if (error := _require_key()) is not None:
        return error
    item_id = await _rpc(
        "mcp_add_item",
        {
            "p_key_hash": API_KEY_HASH,
            "p_room_id": room_id,
            "p_content": content,
            "p_type": item_type,
        },
    )
    if not item_id:
        return "메모를 추가하지 못했습니다. room_id가 맞는지, API 키가 유효한지 확인해주세요."
    return f"추가했습니다. item_id={item_id}"


if __name__ == "__main__":
    server.run(transport="stdio")

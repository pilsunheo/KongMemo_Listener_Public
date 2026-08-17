import asyncio
import hashlib
import json
import logging
import os
import subprocess
import time
import uuid

from dotenv import load_dotenv
from supabase import create_async_client
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

load_dotenv()

SUPABASE_URL = os.environ["KONGMEMO_SUPABASE_URL"]
SUPABASE_ANON_KEY = os.environ["KONGMEMO_SUPABASE_ANON_KEY"]
API_KEY = os.environ["KONGMEMO_API_KEY"]
API_KEY_HASH = hashlib.sha256(API_KEY.encode()).hexdigest()

PROJECTS_FILE = os.path.join(os.path.dirname(__file__), "projects.json")

def load_project_registry() -> dict[str, str]:
    if not os.path.exists(PROJECTS_FILE):
        default_registry = {
            "PROJ_VAULT": r"c:\Users\pilsu\Vault",
            "PROJ_KONG_CRM": r"c:\Antigravity\Kong_CRM",
            "PROJ_KONGMEMO": r"c:\Antigravity\Kong_memo",
        }
        with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_registry, f, indent=4, ensure_ascii=False)
        print(f"[kongmemo_agent_listener] {PROJECTS_FILE} 생성됨. 필요시 로컬 경로를 수정한 후 다시 시작해주세요.")
        return default_registry
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[kongmemo_agent_listener] {PROJECTS_FILE} 로드 실패: {e}")
        return {}

PROJECT_REGISTRY: dict[str, str] = load_project_registry()

ENGINE_BIN: dict[str, list[str]] = {
    "antigravity": ["agy", "-p", "--dangerously-skip-permissions"],
}

TEXT_DECODE_KWARGS = {"text": True, "encoding": "utf-8", "errors": "replace"}

ENGINE_TIMEOUT_SECONDS = 1800
RAW_COMMAND_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 4000
PING_INTERVAL_SECONDS = 15
PROGRESS_INTERVAL_SECONDS = 15

def _run_raw_command_blocking(command: str, args: list[str], cwd: str) -> dict:
    try:
        result = subprocess.run(
            [command, *args], shell=False, cwd=cwd, capture_output=True,
            **TEXT_DECODE_KWARGS,
            timeout=RAW_COMMAND_TIMEOUT_SECONDS,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "exitCode": result.returncode,
            "stdout": (result.stdout or "")[-MAX_OUTPUT_CHARS:],
            "stderr": (result.stderr or "")[-MAX_OUTPUT_CHARS:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"{RAW_COMMAND_TIMEOUT_SECONDS}초 시간 초과"}
    except FileNotFoundError:
        return {"status": "error", "message": f"'{command}' 실행 파일을 찾을 수 없습니다"}
    except Exception as e:
        return {"status": "error", "message": f"명령 실행 중 예외: {e!r}"}


def _run_legacy_engine_blocking(engine: str, prompt: str, cwd: str) -> dict:
    args = ENGINE_BIN[engine] + [prompt]
    try:
        result = subprocess.run(
            args, shell=False, cwd=cwd, capture_output=True,
            **TEXT_DECODE_KWARGS,
            timeout=ENGINE_TIMEOUT_SECONDS,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "exitCode": result.returncode,
            "stdout": (result.stdout or "")[-MAX_OUTPUT_CHARS:],
            "stderr": (result.stderr or "")[-MAX_OUTPUT_CHARS:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"{ENGINE_TIMEOUT_SECONDS // 60}분 시간 초과"}
    except FileNotFoundError:
        return {"status": "error", "message": f"'{args[0]}' 실행 파일을 찾을 수 없습니다 (PATH 확인 필요)"}
    except Exception as e:
        return {"status": "error", "message": f"{engine} 실행 중 예외: {e!r}"}


# room_id 단위로 ClaudeSDKClient를 계속 물고 있어야 대화 맥락이 이어진다. query()는
# SDK 자체 docstring에 "Stateless: Each query is independent, no conversation state"라고
# 명시되어 있어 여기서 쓰면 안 되고, ClaudeAgentOptions(session_id=...)만 넘기는 것도
# resume이 아니라서 이전 대화를 불러오지 않는다(둘은 별개 필드).
_active_clients: dict[str, ClaudeSDKClient] = {}
_active_clients_dangerous: dict[str, bool] = {}
_client_locks: dict[str, asyncio.Lock] = {}


def _lock_for_room(room_id: str) -> asyncio.Lock:
    if room_id not in _client_locks:
        _client_locks[room_id] = asyncio.Lock()
    return _client_locks[room_id]


async def _disconnect_and_forget(room_id: str) -> None:
    existing = _active_clients.pop(room_id, None)
    _active_clients_dangerous.pop(room_id, None)
    if existing is not None:
        try:
            await existing.disconnect()
        except Exception:
            pass


async def _run_claude_sdk(prompt: str, cwd: str, room_id: str, is_dangerous: bool) -> dict:
    started = time.monotonic()
    lock = _lock_for_room(room_id)

    async with lock:
        if prompt == "/clear":
            await _disconnect_and_forget(room_id)
            return {"status": "success", "stdout": "세션이 초기화되었습니다."}

        allowed = ["Read", "Grep", "Glob"]
        if is_dangerous:
            allowed.extend(["Bash", "Edit", "Write"])

        client = _active_clients.get(room_id)
        if client is not None and _active_clients_dangerous.get(room_id) != is_dangerous:
            # 위험 도구 토글이 바뀌면 안전을 위해 세션을 새로 연다(그 순간 대화 맥락은 끊김).
            await _disconnect_and_forget(room_id)
            client = None

        if client is None:
            options = ClaudeAgentOptions(allowed_tools=allowed, cwd=cwd)
            client = ClaudeSDKClient(options)
            await client.connect()
            _active_clients[room_id] = client
            _active_clients_dangerous[room_id] = is_dangerous

    async def run_query():
        try:
            await client.query(prompt)
            full_output = []
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            full_output.append(block.text)
            return {"status": "success", "stdout": "".join(full_output)}
        except Exception as e:
            await _disconnect_and_forget(room_id)
            return {"status": "error", "message": f"Claude SDK 오류: {e!r}"}

    task = asyncio.create_task(run_query())

    while True:
        done, _ = await asyncio.wait({task}, timeout=PROGRESS_INTERVAL_SECONDS)
        if done:
            break
        print(f"[kongmemo_agent_listener] Claude SDK still running... {int(time.monotonic() - started)}s elapsed")

    return await task


async def main() -> None:
    client = await create_async_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    channel_name = f"agent_hub_{API_KEY[3:10]}"
    channel = client.channel(channel_name)

    async def key_is_valid() -> bool:
        try:
            result = await client.rpc("is_agent_key_valid", {"p_key_hash": API_KEY_HASH}).execute()
            return bool(result.data)
        except Exception as e:
            print(f"[kongmemo_agent_listener] key validity check failed: {e}")
            return False

    async def fetch_dangerous_tools_enabled(project_slug: str) -> bool:
        try:
            resp = await client.from_("user_projects").select("dangerous_tools_enabled").eq("slug", project_slug.lower()).maybe_single().execute()
            if resp and resp.data:
                return bool(resp.data.get("dangerous_tools_enabled"))
            return False
        except Exception as e:
            print(f"[kongmemo_agent_listener] db query failed for tools: {e}")
            return False

    async def send_result(request_id: str | None, result: dict) -> None:
        await channel.send_broadcast("command_result", {"requestId": request_id, **result})

    async def send_agent_message(room_id: str | None, request_id: str | None, engine: str, result: dict) -> None:
        if room_id is None:
            return
        text = result.get("stdout") or result.get("message") or "(응답 없음)"
        if result.get("stderr"):
            text += f"\n\n--- stderr ---\n{result['stderr']}"
            
        if request_id is not None:
            message_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"kongmemo-agent-message-{request_id}"))
        else:
            message_id = str(uuid.uuid4())
            
        await channel.send_broadcast(
            "agent_message",
            {
                "type": "chat",
                "roomId": room_id,
                "agent_id": f"{engine}_pc_listener",
                "agent_name": engine,
                "content": {"text": text},
                "messageId": message_id,
            },
        )

    async def process_command(data: dict) -> None:
        request_id = data.get("requestId")
        room_id = data.get("roomId")
        project_key = data.get("projectKey")
        engine = data.get("engine")
        command = data.get("command")
        args = data.get("args") or []
        print(f"[kongmemo_agent_listener] received client_command: engine={engine} projectKey={project_key} roomId={room_id}")

        if not await key_is_valid():
            await send_result(request_id, {"status": "error", "message": "API 키가 폐기되었거나 유효하지 않습니다."})
            return

        if engine:
            cwd = PROJECT_REGISTRY.get(project_key)
            if cwd is None:
                await send_result(request_id, {
                    "status": "error",
                    "message": f"등록 안 된 프로젝트: {project_key}\nPC의 projects.json 파일에 \"{project_key}\": \"폴더경로\" 줄을 추가한 뒤 리스너를 재시작해주세요."
                })
                return
                
            prompt = args[0] if args else ""
            await send_result(request_id, {"status": "success", "message": f"{engine} 실행 시작됨 (cwd={cwd})"})
            
            if engine == "claude":
                is_dangerous = False
                if project_key and project_key.startswith("PROJ_"):
                    slug = project_key.replace("PROJ_", "")
                    is_dangerous = await fetch_dangerous_tools_enabled(slug)
                result = await _run_claude_sdk(prompt, cwd, room_id, is_dangerous)
            elif engine in ENGINE_BIN:
                task = asyncio.create_task(asyncio.to_thread(_run_legacy_engine_blocking, engine, prompt, cwd))
                started = time.monotonic()
                while True:
                    done, _ = await asyncio.wait({task}, timeout=PROGRESS_INTERVAL_SECONDS)
                    if done:
                        break
                    print(f"[kongmemo_agent_listener] {engine} still running... {int(time.monotonic() - started)}s elapsed")
                result = await task
            else:
                await send_result(request_id, {"status": "error", "message": f"알 수 없는 엔진: {engine}"})
                return

            print(f"[kongmemo_agent_listener] {engine} finished")
            await send_agent_message(room_id, request_id, engine, result)
        else:
            result = await asyncio.to_thread(_run_raw_command_blocking, command, args, os.getcwd())
            await send_result(request_id, result)

    def on_client_command(broadcast_payload: dict) -> None:
        async def _wrapped():
            try:
                data = broadcast_payload.get("payload", {})
                await process_command(data)
            except Exception as e:
                print(f"[kongmemo_agent_listener] process_command crashed: {e!r}")
        asyncio.create_task(_wrapped())

    channel.on_broadcast(event="client_command", callback=on_client_command)
    await channel.subscribe()
    print(f"[kongmemo_agent_listener] listening on channel: {channel_name}")

    try:
        while True:
            await channel.send_broadcast("agent_ping", {})
            await asyncio.sleep(PING_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        await channel.unsubscribe()
        print("[kongmemo_agent_listener] stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[kongmemo_agent_listener] stopped.")

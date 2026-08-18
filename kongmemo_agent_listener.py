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

# 예전엔 개발자 본인 경로(Vault/Kong_CRM/Kong_memo)를 기본값으로 깔아뒀는데, 이건
# 일반 사용자에게는 아무 의미 없는(오히려 낯선) 값이라 그냥 빈 registry로 시작한다.
# 등록은 이제 앱의 "PC 폴더 연결" 버튼(client_command의 configAction)으로 한다.
def load_project_registry() -> dict[str, str]:
    if not os.path.exists(PROJECTS_FILE):
        return {}
    try:
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[kongmemo_agent_listener] {PROJECTS_FILE} 로드 실패: {e}")
        return {}

def save_project_registry() -> None:
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(PROJECT_REGISTRY, f, indent=4, ensure_ascii=False)

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
_client_locks: dict[str, asyncio.Lock] = {}

# Bash/Edit/Write까지 항상 허용한다 - 폰에서 파일을 실제로 만들고 고치는 게 이 기능의
# 핵심 목적이라, 안전 vs 편의 토글을 두는 대신 처음부터 다 열어둔다(Antigravity 엔진도
# 이미 --dangerously-skip-permissions로 항상 전부 허용되고 있어서 통일된 동작).
ALWAYS_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash", "Edit", "Write"]


def _lock_for_room(room_id: str) -> asyncio.Lock:
    if room_id not in _client_locks:
        _client_locks[room_id] = asyncio.Lock()
    return _client_locks[room_id]


async def _disconnect_and_forget(room_id: str) -> None:
    existing = _active_clients.pop(room_id, None)
    if existing is not None:
        try:
            await existing.disconnect()
        except Exception:
            pass


async def _run_claude_sdk(prompt: str, cwd: str, room_id: str) -> dict:
    started = time.monotonic()
    lock = _lock_for_room(room_id)

    # 락을 클라이언트 생성 구간에만 걸면, 첫 메시지가 아직 응답을 받는 중일 때 같은 방으로
    # 두 번째 메시지가 오면 같은 ClaudeSDKClient에 동시에 query()/receive_response()를
    # 걸게 되어 응답이 서로 섞일 수 있다. 그래서 한 번에 한 메시지만 처리되도록 질문-응답
    # 전체 구간을 락으로 감싼다 - 다른 방은 서로 다른 락이라 영향 없음.
    async with lock:
        if prompt == "/clear":
            await _disconnect_and_forget(room_id)
            return {"status": "success", "stdout": "세션이 초기화되었습니다."}

        client = _active_clients.get(room_id)
        if client is None:
            options = ClaudeAgentOptions(allowed_tools=ALWAYS_ALLOWED_TOOLS, cwd=cwd)
            client = ClaudeSDKClient(options)
            await client.connect()
            _active_clients[room_id] = client

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

        agent_id = f"{engine}_pc_listener"
        await channel.send_broadcast(
            "agent_message",
            {
                "type": "chat",
                "roomId": room_id,
                "agent_id": agent_id,
                "agent_name": engine,
                "content": {"text": text},
                "messageId": message_id,
            },
        )

        # Broadcast는 그 순간 폰이 연결되어 있어야만 받을 수 있고, 놓치면 영구히
        # 사라진다(비영속). memo_items에도 같은 메시지를 직접 저장해두면, 이미 있는
        # postgres_changes 실시간 구독과 다음 pull sync 양쪽으로 자동으로 도착해서
        # 응답 자체를 놓치는 문제가 없어진다. 같은 message_id를 쓰므로 브로드캐스트로
        # 이미 로컬에 들어온 것과 합쳐질 뿐, 중복 메시지가 생기지 않는다.
        content_json = json.dumps({
            "agent_id": agent_id,
            "agent_name": engine,
            "content": {"text": text},
            "actions": [],
        }, ensure_ascii=False)
        try:
            await client.rpc("insert_agent_message", {
                "p_key_hash": API_KEY_HASH,
                "p_room_id": room_id,
                "p_message_id": message_id,
                "p_content": content_json,
                "p_sender_id": agent_id,
            }).execute()
        except Exception as e:
            print(f"[kongmemo_agent_listener] failed to persist agent message to memo_items: {e}")

    async def handle_config_action(action: str, project_key: str | None, path: str | None, request_id: str | None) -> None:
        if action == "list":
            if not PROJECT_REGISTRY:
                await send_result(request_id, {"status": "success", "message": "등록된 프로젝트가 없습니다."})
            else:
                lines = "\n".join(f"{k} → {v}" for k, v in PROJECT_REGISTRY.items())
                await send_result(request_id, {"status": "success", "message": lines})
            return

        if not project_key:
            await send_result(request_id, {"status": "error", "message": "프로젝트 키가 없습니다."})
            return

        if action == "register":
            if not path:
                await send_result(request_id, {"status": "error", "message": "등록할 폴더 경로가 없습니다."})
                return
            if not os.path.isdir(path):
                await send_result(request_id, {"status": "error", "message": f"PC에서 이 경로를 찾을 수 없습니다: {path}"})
                return
            PROJECT_REGISTRY[project_key] = path
            save_project_registry()
            await send_result(request_id, {"status": "success", "message": f"등록 완료: {project_key} → {path}"})
        elif action == "remove":
            if project_key in PROJECT_REGISTRY:
                del PROJECT_REGISTRY[project_key]
                save_project_registry()
                await send_result(request_id, {"status": "success", "message": f"삭제 완료: {project_key}"})
            else:
                await send_result(request_id, {"status": "error", "message": f"등록되어 있지 않습니다: {project_key}"})
        else:
            await send_result(request_id, {"status": "error", "message": f"알 수 없는 설정 명령: {action}"})

    async def process_command(data: dict) -> None:
        request_id = data.get("requestId")
        room_id = data.get("roomId")
        project_key = data.get("projectKey")
        engine = data.get("engine")
        command = data.get("command")
        args = data.get("args") or []
        config_action = data.get("configAction")
        print(f"[kongmemo_agent_listener] received client_command: engine={engine} projectKey={project_key} configAction={config_action} roomId={room_id}")

        if not await key_is_valid():
            await send_result(request_id, {"status": "error", "message": "API 키가 폐기되었거나 유효하지 않습니다."})
            return

        if config_action:
            await handle_config_action(config_action, project_key, data.get("path"), request_id)
            return

        if engine:
            cwd = PROJECT_REGISTRY.get(project_key)
            if cwd is None:
                await send_result(request_id, {
                    "status": "error",
                    "message": f"등록 안 된 프로젝트: {project_key}\n앱의 프로젝트 목록에서 'PC 폴더 연결' 버튼으로 이 프로젝트의 로컬 폴더를 등록해주세요."
                })
                return
                
            prompt = args[0] if args else ""
            await send_result(request_id, {"status": "success", "message": f"{engine} 실행 시작됨 (cwd={cwd})"})
            
            if engine == "claude":
                result = await _run_claude_sdk(prompt, cwd, room_id)
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

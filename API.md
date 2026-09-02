# 콩메모 API

콩메모에 쌓인 내 메모를 **코드로 직접 읽고 쓸 수 있습니다.** 파이썬, 자바스크립트,
n8n 같은 자동화 도구, 심지어 `curl` 한 줄로도 됩니다.

앱에서 발급한 API 키 하나만 있으면 되고, 별도 서버나 봇을 만들 필요가 없습니다.

> 문의와 제안은 [Issues](https://github.com/pilsunheo/KongMemo_Listener_Public/issues)로 남겨주세요.

---

## 시작하기

### 1. 키 발급

콩메모 앱 → **AI 커맨드 센터 → API & 확장 → 새로운 API Key 발행**

발급된 키는 `kh_`로 시작합니다. **이 키는 다시 볼 수 없으니 안전한 곳에 보관하세요.**
잃어버리면 재발급하면 됩니다(이전 키는 즉시 무효화됩니다).

### 2. 키 해시 만들기

서버에는 키 원문이 저장되지 않습니다. 요청할 때도 **키의 SHA-256 해시**를 보냅니다.

```python
import hashlib
key_hash = hashlib.sha256("kh_여기에_발급받은_키".encode()).hexdigest()
```

```javascript
const enc = new TextEncoder().encode("kh_여기에_발급받은_키");
const buf = await crypto.subtle.digest("SHA-256", enc);
const keyHash = [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
```

### 3. 호출

```
POST https://iiaqvhqnafqrrxizlofl.supabase.co/rest/v1/rpc/<함수이름>
Content-Type: application/json
apikey: sb_publishable_hh_54DP6aBLlXMVhA__Ulg_EEPrBWKu
```

`apikey` 헤더 값은 **모든 사용자가 공유하는 공개 값**입니다(앱에도 들어 있습니다).
누가 내 데이터를 보느냐는 본문에 담는 **키 해시**로만 결정됩니다.

---

## 엔드포인트

### `mcp_list_rooms` — 방 목록

```json
{ "p_key_hash": "<키 해시>" }
```

```json
[
  {
    "room_id": "0f4c...", "title": "유튜브 보관함 📺", "system_key": "SYS_YOUTUBE",
    "is_favorite": false, "item_count": 74, "last_activity": "2026-09-02T13:29:10Z"
  }
]
```

최근 활동순으로 정렬됩니다. `system_key`가 있으면 앱이 자동 생성한 기본 방입니다
(`SYS_IMPORTANT` 중요 메모, `SYS_YOUTUBE` 유튜브 보관함, `SYS_TODO` 할일 목록 등).
`PROJ_`로 시작하면 PC 리스너와 연결된 프로젝트 방입니다.

### `mcp_get_room_items` — 방의 메모 읽기

```json
{ "p_key_hash": "<키 해시>", "p_room_id": "<room_id>", "p_limit": 50 }
```

```json
[
  { "item_id": "9a1e...", "content": "https://youtu.be/...", "type": "link",
    "sender_id": null, "created_at": "2026-09-02T10:31:00Z" }
]
```

최신순. `p_limit`은 기본 50, 최대 200입니다.

### `mcp_search_items` — 전체 검색

```json
{ "p_key_hash": "<키 해시>", "p_query": "유튜브", "p_limit": 50, "p_type": null }
```

`p_type`에 `"link"`를 주면 링크만, `"file"`이면 파일만 골라냅니다(`null`이면 전체).
결과에는 어느 방의 메모인지 알 수 있도록 `room_title`이 함께 들어갑니다.

### `mcp_add_item` — 메모 추가

```json
{ "p_key_hash": "<키 해시>", "p_room_id": "<room_id>",
  "p_content": "내일 3시 회의 준비", "p_type": "text" }
```

추가된 메모의 `item_id`(UUID)를 돌려줍니다. 실패하면 `null`입니다.
`p_type`은 `text` 또는 `link`만 가능합니다. 추가한 메모는 **폰과 웹에 즉시 동기화**되고,
앱에서는 `mcp`가 보낸 것으로 표시됩니다.

### `is_agent_key_valid` — 키 유효성 확인

```json
{ "p_key_hash": "<키 해시>" }
```

`true` / `false`를 돌려줍니다. 장시간 도는 프로그램이라면 주기적으로 확인해서,
앱에서 키를 폐기했을 때 즉시 멈추도록 만드는 것을 권합니다.

---

## 예제

### curl — 저장해둔 링크 전부 뽑기

```bash
KEY_HASH=$(printf '%s' "kh_발급받은_키" | sha256sum | cut -d' ' -f1)

curl -s -X POST "https://iiaqvhqnafqrrxizlofl.supabase.co/rest/v1/rpc/mcp_search_items" \
  -H "apikey: sb_publishable_hh_54DP6aBLlXMVhA__Ulg_EEPrBWKu" \
  -H "Content-Type: application/json" \
  -d "{\"p_key_hash\":\"$KEY_HASH\",\"p_query\":\"http\",\"p_type\":\"link\",\"p_limit\":200}"
```

### 파이썬 — 유튜브 링크만 모아 파일로

```python
import hashlib, requests

BASE = "https://iiaqvhqnafqrrxizlofl.supabase.co/rest/v1/rpc"
ANON = "sb_publishable_hh_54DP6aBLlXMVhA__Ulg_EEPrBWKu"
KEY_HASH = hashlib.sha256(b"kh_발급받은_키").hexdigest()

def call(fn, **params):
    r = requests.post(f"{BASE}/{fn}",
                      headers={"apikey": ANON, "Content-Type": "application/json"},
                      json={"p_key_hash": KEY_HASH, **params})
    r.raise_for_status()
    return r.json()

links = call("mcp_search_items", p_query="youtu", p_type="link", p_limit=200)
with open("youtube_links.md", "w", encoding="utf-8") as f:
    for item in links:
        f.write(f"- [{item['room_title']}] {item['content']}\n")
print(f"{len(links)}개 저장 완료")
```

---

## 보안

- 서버는 **키 원문을 저장하지 않습니다.** SHA-256 해시만 보관합니다.
- 모든 함수는 키 해시로 주인을 찾아 **그 사람의 데이터만** 다룹니다. 다른 사용자의 방은
  조회도 기록도 되지 않습니다(Postgres `SECURITY DEFINER` 함수로 강제).
- 잘못된 키는 빈 결과를 돌려줍니다. 오류 메시지로 정보를 흘리지 않습니다.
- 앱에서 키를 폐기하면 **서버에서 즉시 차단**됩니다. PC에 키가 남아 있어도 소용없습니다.
- 키는 계정 전체 권한을 가지므로 **공개 저장소에 커밋하지 마세요.** 환경변수를 쓰세요.

## 호출 제한

**키당 분당 60회**입니다. 사람이 쓰는 속도로는 걸릴 일이 없고, 반복문이 폭주할 때만
걸립니다. 초과하면 HTTP 400과 함께 이런 메시지가 돌아옵니다.

```json
{ "message": "rate limit exceeded: 분당 60회를 초과했습니다. 잠시 후 다시 시도해주세요." }
```

1분 단위로 초기화되므로 잠시 기다렸다 다시 호출하면 됩니다. 많은 데이터를 가져와야 한다면
호출을 여러 번 나누기보다 **`p_limit`을 크게(최대 200) 잡아 한 번에** 받아오세요.

PC 리스너는 이 제한과 별개로 동작합니다 — API 쪽에서 한도를 다 써도 폰↔PC 연결은
멈추지 않습니다.

## 알아두실 것

- 삭제와 수정은 아직 제공하지 않습니다(읽기·검색·추가만). 실수로 메모가 사라지는 일을
  막기 위해 의도적으로 뺐습니다.
- 파일과 이미지는 `content`에 주소나 경로가 담깁니다. 폰에만 있는 원본은 앱이 켜져 있을 때만
  받아올 수 있습니다.

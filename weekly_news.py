import openai
import re
import requests
import xml.etree.ElementTree as ET
import os
import json
from datetime import datetime, timezone, timedelta


# 讀取執行程式時需要的環境變數。
# OPENAI_API_KEY：用來呼叫 OpenAI API。
# DISCORD_WEBHOOK_URL：用來把完成的週報發送到 Discord。
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

# 定義台灣時區，之後所有日期與週數都用台灣時間計算。
TAIWAN_TZ = timezone(timedelta(hours=8))

# 定義記憶檔案名稱，用來存放每週已報導的標題。
MEMORY_FILE = "newsletter_memory.json"


# 定義可抓取的 RSS 來源。
# key 是給 LLM 使用的來源名稱，value 是實際的 RSS URL。
RSS_FEEDS = {
    "TechCrunch": "https://techcrunch.com/feed/",
    "The Verge": "https://www.theverge.com/rss/index.xml",
    "Wired": "https://www.wired.com/feed/rss",
    "Ars Technica": "https://feeds.arstechnica.com/arstechnica/index",
    "iThome": "https://www.ithome.com.tw/rss",
}


# ════════════════════════════════════════════════════════════
# Tool 實作
# 這一區是真正會做事的函式。
# LLM 不會直接抓網頁或發 Discord，而是透過這些工具函式完成。
# ════════════════════════════════════════════════════════════


def tool_fetch_rss(source: str) -> str:
    """
    根據來源名稱抓取 RSS。

    參數：
    - source：來源名稱，例如 "TechCrunch"、"Wired"

    回傳：
    - 成功時：多行文字，每行格式為「標題 | 網址」
    - 失敗時：錯誤訊息字串
    """
    # 根據來源名稱查出對應的 RSS URL。
    url = RSS_FEEDS.get(source)

    # 如果輸入的來源名稱不存在，直接回傳可用來源清單。
    if not url:
        return f"找不到來源：{source}。可用來源：{', '.join(RSS_FEEDS)}"

    try:
        # 發送 HTTP GET 請求抓 RSS 內容。
        # timeout=8 代表最久等 8 秒，超過就放棄。
        # headers 中放 User-Agent，可降低部分網站拒絕請求的機率。
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})

        # 將 RSS / Atom XML 內容解析成 ElementTree 物件，方便後續用路徑查找元素。
        root = ET.fromstring(resp.content)

        # 兼容兩種常見格式：
        # 1. RSS 2.0：文章放在 <item> 標籤
        # 2. Atom：文章放在 <entry> 標籤
        items = (
            root.findall(".//item")
            or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        )

        # 用來收集最後要回傳的文章列表。
        results = []

        # 只取前 5 篇，避免一次餵給模型太多內容。
        for item in items[:5]:
            # 嘗試抓文章標題。
            # 先抓一般 RSS 的 title，再抓 Atom 命名空間的 title。
            title = (
                item.findtext("title")
                or item.findtext("{http://www.w3.org/2005/Atom}title")
                or ""
            ).strip()

            # 嘗試抓文章連結。
            # 先抓一般 RSS 的 link，再抓 Atom 命名空間的 link。
            link = (
                item.findtext("link")
                or item.findtext("{http://www.w3.org/2005/Atom}link")
                or ""
            ).strip()

            # 有些 Atom feed 的連結不在文字內容，而是放在 link 標籤的 href 屬性裡。
            # 如果前面都沒抓到，就改從 href 屬性取值。
            if not link:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href", "").strip()

            # 只保留：
            # 1. 有標題
            # 2. 連結是 https:// 開頭（過濾掉相對路徑或空連結）
            if title and link.startswith("https://"):
                results.append(f"{title} | {link}")

        # 如果有抓到文章，就用換行串起來回傳。
        # 如果一篇都沒有，就回傳該來源無法取得文章的訊息。
        return "\n".join(results) if results else f"{source}：無法取得文章"

    except Exception as e:
        # 任一錯誤都包成字串回傳，避免工具直接讓整個程式中斷。
        return f"{source} 失敗：{e}"


def _split_chunks(content: str, limit: int = 3800) -> list[str]:
    """
    將長文字切成多段，讓每段都在 Discord embed description 的長度限制內。

    Discord 的 embed description 上限約為 4096 字。
    這裡用 3800 是刻意留了安全空間，避免邊界誤差。

    參數：
    - content：要切割的完整內容
    - limit：每段的最大字元數，預設 3800

    回傳：
    - 字串列表，每個元素都是可以安全發送的一段
    """
    # 用來存放切割後的段落。
    chunks = []

    # 只要剩餘內容還超過限制，就繼續切割。
    while len(content) > limit:
        # 在限制範圍內，從後面往前找最後一個換行符號。
        # 這樣可以讓切點落在段落邊界，保持可讀性。
        cut = content.rfind("\n", 0, limit)

        # 如果整段內容都沒有換行符號，rfind 會回傳 -1。
        # 這時就直接在 limit 位置強制硬切，避免無限迴圈。
        if cut == -1:
            cut = limit

        # 把前半段加入結果列表。
        chunks.append(content[:cut])

        # 剩下的內容繼續進入下一輪迴圈。
        # strip() 用來去掉切段後開頭可能殘留的空白或換行符號。
        content = content[cut:].strip()

    # 迴圈結束代表剩餘內容長度已在限制內。
    # 如果還有剩，就加進最後一段。
    if content:
        chunks.append(content)

    return chunks


def tool_post_discord(content: str) -> str:
    """
    將完成的週報發送到 Discord Webhook。

    若週報太長，會自動切成多則訊息依序發送。

    參數：
    - content：完整週報內容

    回傳：
    - 成功時：成功訊息
    - 失敗時：HTTP 錯誤資訊，並停止後續發送
    """
    # 產生今天的台灣日期，顯示在 Discord embed 標題。
    today = datetime.now(TAIWAN_TZ).strftime("%Y/%m/%d")

    # 取得 ISO week number（例如第 14 週），用於週報標題。
    week_num = datetime.now(TAIWAN_TZ).isocalendar()[1]

    # 先把內容切成多段，確保每段都在 Discord 限制內。
    chunks = _split_chunks(content)

    # 逐段發送到 Discord。
    for i, chunk in enumerate(chunks):
        # 如果超過一段，就在標題後面標示目前是第幾段，例如 "(1/2)"。
        tag = f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""

        # 組成 Discord Webhook 的 payload（JSON 格式）。
        payload = {
            "username": "黛安娜的科技蟹蟹水果報",
            "embeds": [{
                "title": f"黛安娜的科技蟹蟹水果報  Week {week_num} | {today}{tag}",
                "description": chunk,
                "color": 0xC0392B,
                "footer": {"text": "Powered by GPT | 每週五 18:00 發布"},
                # timestamp 要用 UTC 格式，Discord 會自動轉換顯示時區。
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        }

        # 將 payload 送到 Discord Webhook URL。
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)

        # Discord Webhook 成功時回傳 200（附有內容）或 204（無內容）。
        # 只要不是這兩個，就視為發送失敗，停止後續段落的發送。
        if resp.status_code not in (200, 204):
            return f"Discord 發送失敗：HTTP {resp.status_code} — {resp.text}"

    # 全部段落都成功發出後，回傳完成訊息。
    return f"成功發送（共 {len(chunks)} 則訊息）"


# ── Memory ────────────────────────────────────────────────
# 這一區處理長期記憶，用一個本機的 JSON 檔案儲存每週的報導標題。
#
# 為什麼需要記憶？
# 如果每週都從零開始選文章，很容易重複選到類似主題。
# 透過記憶，可以讓 Collector 知道「這個主題上週已經報過」，進而跳過它。
# ──────────────────────────────────────────────────────────


def tool_load_memory() -> str:
    """
    讀取最近幾週已報導的新聞標題，提供給 Collector 做去重參考。

    回傳：
    - 若沒有記憶檔：回傳首次執行提示訊息
    - 若檔案損壞：回傳重置提示訊息
    - 若成功：回傳整理好的多行文字
    """
    # 如果記憶檔完全不存在，代表這是第一次執行。
    if not os.path.exists(MEMORY_FILE):
        return "尚無歷史記錄，這是第一期。"

    # 嘗試讀取 JSON 記憶檔。
    # 如果檔案損壞（例如寫到一半程式中斷）或無法讀取，就視為重新開始。
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "記憶檔案損壞，視為第一期重新開始。"

    # 只取最近 4 週。
    # 太舊的標題對去重已經沒有意義，且會讓 prompt 不必要地變長。
    recent = data[-4:]

    # 把記憶整理成純文字，方便模型理解。
    lines = []
    for week in recent:
        lines.append(f"Week {week['week']} ({week['date']})：")
        for title in week["titles"]:
            lines.append(f"  - {title}")

    # 若有資料就回傳整理後的文字，否則回傳空記憶提示。
    return "\n".join(lines) if lines else "尚無歷史記錄。"


def tool_save_memory(titles: list[str]) -> str:
    """
    將本週實際採用的新聞標題寫入記憶檔。

    這個函式應在週報成功發送後呼叫，確保只有真正送出的內容才會被記憶。

    參數：
    - titles：本週週報中最終採用的標題列表

    回傳：
    - 成功寫入後的確認訊息
    """
    # 預設先建立空清單，作為讀取失敗時的預備值。
    data = []

    # 如果記憶檔存在，先把舊資料讀進來，才能在後面追加本週資料。
    # 如果讀不到或檔案損壞，就用空清單重新開始，不讓這個錯誤影響整個流程。
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = []

    # 取得現在的台灣時間。
    now = datetime.now(TAIWAN_TZ)

    # 把本週資料追加到清單中。
    data.append({
        "week": now.isocalendar()[1],
        "date": now.strftime("%Y/%m/%d"),
        "titles": titles,
    })

    # 最多只保留最近 12 週，相當於 3 個月的歷史。
    # 超過的就從前面丟掉，保持檔案大小合理。
    data = data[-12:]

    # 將更新後的記憶寫回 JSON 檔。
    # ensure_ascii=False 確保中文字不會被轉成 \uXXXX 跳脫序列。
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return f"已儲存 {len(titles)} 筆標題到記憶"


# ════════════════════════════════════════════════════════════
# Tool Schema
# 這一區不是工具本身，而是描述工具的「說明書」。
#
# OpenAI 的 tool schema 格式與 Anthropic 略有不同：
# - Anthropic：直接放 name / description / input_schema
# - OpenAI：外層需要包一層 {"type": "function", "function": {...}}
#            輸入參數用 "parameters" 而不是 "input_schema"
# ════════════════════════════════════════════════════════════


# fetch_rss 工具的 schema（OpenAI 格式）。
FETCH_RSS_TOOL = {
    "type": "function",         # OpenAI 固定需要這個欄位，目前只支援 "function"
    "function": {
        "name": "fetch_rss",
        # description 是給 LLM 看的說明，直接影響模型是否會在正確時機呼叫這個工具。
        "description": (
            "抓取指定科技媒體的最新文章清單，回傳標題和完整網址。"
            "可用來源：TechCrunch, The Verge, Wired, Ars Technica, iThome。"
            "每個來源需要分別呼叫一次。"
        ),
        # parameters 定義這個工具接受什麼輸入，格式遵循 JSON Schema。
        # 注意：OpenAI 用 "parameters"，Anthropic 用 "input_schema"，意思相同。
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "媒體名稱，必須完全符合可用來源之一",
                }
            },
            "required": ["source"],
        },
    },
}

# post_discord 工具的 schema（OpenAI 格式）。
POST_DISCORD_TOOL = {
    "type": "function",
    "function": {
        "name": "post_discord",
        "description": (
            "把完成的週報內容發送到 Discord。"
            "只在週報完整寫好後才呼叫，蒐集資料期間不要呼叫。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "完整的週報文字，包含開場、5 則新聞分析、本週觀察",
                }
            },
            "required": ["content"],
        },
    },
}


# ════════════════════════════════════════════════════════════
# Tool 執行器
# 這一區負責把 LLM 的工具呼叫請求，轉換成真正的 Python 函式執行。
#
# OpenAI 的工具呼叫流程：
# 1. LLM 在回覆的 message.tool_calls 中說明它要呼叫哪個工具和傳入什麼參數
# 2. 程式讀取 tool_calls，呼叫這裡的 execute_tool
# 3. execute_tool 根據工具名稱，去呼叫對應的 Python 函式
# 4. 結果包裝成 role="tool" 的 message，再傳回給 LLM，讓 LLM 繼續決策
# ════════════════════════════════════════════════════════════


def execute_tool(name: str, inputs: dict) -> str:
    """
    根據工具名稱執行對應的工具函式。

    參數：
    - name：工具名稱（由 LLM 在 tool_calls 中指定）
    - inputs：工具輸入參數（由 LLM 提供，需先用 json.loads 解析）

    回傳：
    - 工具執行後的字串結果
    """
    # 如果 LLM 指定執行 fetch_rss，就呼叫 tool_fetch_rss 並印出摘要。
    if name == "fetch_rss":
        result = tool_fetch_rss(inputs["source"])
        print(f"    [tool] fetch_rss({inputs['source']}): {result.count(chr(10)) + 1} 篇")
        return result

    # 如果 LLM 指定執行 post_discord，就呼叫 tool_post_discord 並印出結果。
    if name == "post_discord":
        result = tool_post_discord(inputs["content"])
        print(f"    [tool] post_discord: {result}")
        return result

    # 如果工具名稱不在預期範圍內，回傳提示訊息。
    return f"未知工具：{name}"


# ════════════════════════════════════════════════════════════
# Reflection
# 這一區負責「第二次品質確認」，讓另一個模型來審查草稿。
# ════════════════════════════════════════════════════════════


# Critic 的系統提示詞。
CRITIC_SYSTEM = """你是資深科技媒體主編，負責審查週報草稿。
審查標準（全部符合才算通過）：
1. 禁用詞不得出現：值得關注、深刻影響、不容忽視、劃時代、引領未來
2. 不得有任何 emoji 或表情符號
3. 網址必須裸露貼出，格式為「來源：媒體名稱 https://...」
   不可使用 Markdown [文字](連結) 格式，也不可加任何括號包住網址
4. 每則新聞必須同時包含「商業視角」和「科技視角」兩個段落
5. 開場和本週觀察需有具體觀點，不能只是陳述事實的摘要

回覆規則：
- 若草稿完全符合，僅回覆一行：APPROVED
- 若有問題，依以下格式回覆：
ISSUES
（條列具體問題，每條一行）
REVISED
（修改後的完整稿件）"""


def reflect_on_draft(client: openai.OpenAI, draft: str, max_retries: int = 2) -> str:
    """
    讓另一個模型扮演主編，審查週報草稿並在必要時修正。

    參數：
    - client：OpenAI client
    - draft：Writer 生成的原始草稿
    - max_retries：最多允許修改幾次，預設 2 次

    回傳：
    - 通過審查（或達到上限）後的最終稿件
    """
    # current 代表目前正在被審查的版本。
    current = draft

    for attempt in range(max_retries + 1):
        # 呼叫 OpenAI 模型進行審稿。
        # OpenAI 沒有獨立的 system= 參數，system prompt 要放進 messages 的第一則，
        # role 設為 "system"。
        resp = client.chat.completions.create(
            # 審稿任務是規則比對，gpt-4o 已足夠，不需要用最貴的模型。
            model="gpt-4o",
            max_tokens=4096,
            messages=[
                {"role": "system", "content": CRITIC_SYSTEM},
                {"role": "user", "content": f"請審查以下草稿：\n\n{current}"},
            ],
        )

        # OpenAI 的回覆結構：
        # resp.choices[0].message.content 是模型回傳的文字。
        text = resp.choices[0].message.content.strip()

        # 情況一：Critic 認為草稿完全通過。
        if text.startswith("APPROVED"):
            print(f"  [reflection] 第 {attempt + 1} 次審查通過")
            return current

        # 情況二：Critic 找到問題並提供修正版稿件。
        if "REVISED" in text:
            idx = text.index("REVISED") + len("REVISED")
            current = text[idx:].strip()
            print(f"  [reflection] 第 {attempt + 1} 次：發現問題，已修改")

        else:
            # 情況三：模型輸出格式不符合預期，直接停止審稿。
            print(f"  [reflection] 第 {attempt + 1} 次：格式異常，保留現版本")
            break

    print("  [reflection] 已達最大審查次數，使用最後版本")
    return current


# ════════════════════════════════════════════════════════════
# Multi-Agent
# ════════════════════════════════════════════════════════════


class CollectorAgent:
    """
    蒐集型 Agent。

    職責：
    - 呼叫 fetch_rss 逐一抓取各媒體的 RSS
    - 參考記憶，排除最近幾週已報過的相似主題
    - 整理出 7 篇候選文章，交給 Writer 使用
    """

    SYSTEM = """你是資料蒐集員，負責抓取 RSS 並精選文章。

任務：
1. 逐一呼叫 fetch_rss 抓取全部 5 個來源
2. 對照已報導標題，排除高度重疊的主題
3. 挑出 7 篇候選（多給 2 篇緩衝供後續撰稿員選擇）

輸出格式（純文字，每篇一行，不要分析）：
來源名稱 | 文章標題 | 網址

只輸出這 7 行，不加任何其他文字。"""

    # 最多允許迭代 15 次，防止模型卡住時進入無限迴圈。
    MAX_ITER = 15

    def run(self, client: openai.OpenAI, today: str, memory_context: str) -> str:
        """
        執行 Collector Agent 的完整蒐集流程。

        OpenAI 與 Anthropic 在訊息格式上的主要差異：
        - OpenAI 的 system prompt 放在 messages[0]，role="system"
        - 工具結果用 role="tool"，不是 role="user" 包 tool_result
        - tool_call_id 對應 message.tool_calls[i].id

        參數：
        - client：OpenAI client
        - today：今天日期字串
        - memory_context：最近幾週已報導標題的純文字內容

        回傳：
        - 成功：候選文章清單字串
        - 失敗：空字串
        """
        print("\n[Collector Agent] 開始蒐集文章...")

        # 初始化對話歷史。
        # OpenAI 的 system prompt 放在 messages 的第一則，role="system"。
        messages = [
            {"role": "system", "content": self.SYSTEM},
            {
                "role": "user",
                "content": (
                    f"今天是 {today}。\n\n"
                    f"以下是過去已報導的標題，請避開相似主題：\n{memory_context}\n\n"
                    "請逐一呼叫 fetch_rss 抓取全部 5 個來源，挑出 7 篇候選文章。"
                ),
            },
        ]

        # 進入 ReAct 迴圈：模型思考 -> 呼叫工具 -> 接收結果 -> 繼續思考。
        for iteration in range(self.MAX_ITER):

            resp = client.chat.completions.create(
                # gpt-4o-mini 對應原本的 claude-haiku，便宜且速度快，適合資料蒐集任務。
                model="gpt-4o-mini",
                max_tokens=2048,
                tools=[FETCH_RSS_TOOL],
                messages=messages,
            )

            choice = resp.choices[0]

            # 把模型這一輪的回應加入對話歷史。
            # OpenAI 需要把整個 message 物件轉成 dict 格式才能放進 messages。
            # 包含 content（可能是 None）和 tool_calls（若有呼叫工具）。
            assistant_msg = {
                "role": "assistant",
                "content": choice.message.content,  # 若有工具呼叫，這裡可能是 None
            }
            # 如果有工具呼叫，把 tool_calls 也一起放進去。
            # OpenAI 要求回傳工具結果時，前面必須附上對應的 assistant 訊息（含 tool_calls）。
            if choice.message.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]
            messages.append(assistant_msg)

            # OpenAI 用 finish_reason 表示這一輪結束的原因。
            # "stop" 對應 Anthropic 的 "end_turn"，代表模型認為任務已完成。
            if choice.finish_reason == "stop":
                text = choice.message.content or ""
                if text.strip():
                    print(f"  [Collector] 完成（{text.count(chr(10)) + 1} 篇候選）")
                    return text
                return ""

            # "tool_calls" 對應 Anthropic 的 "tool_use"，代表模型想呼叫工具。
            if choice.finish_reason == "tool_calls":
                for tc in choice.message.tool_calls:
                    # OpenAI 的工具參數是 JSON 字串，需要先用 json.loads 解析成 dict。
                    # Anthropic 則是直接給 dict，不需要額外解析。
                    args = json.loads(tc.function.arguments)
                    result = execute_tool(tc.function.name, args)

                    # 工具結果用 role="tool" 放回 messages。
                    # tool_call_id 必須對應這個請求的 id，讓模型知道這是哪個工具的回應。
                    # 這個設計與 Anthropic 的 tool_use_id 目的相同，但格式不同。
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

        print("  ! [Collector] 已達最大迭代次數，強制終止")
        return ""


class WriterAgent:
    """
    撰稿型 Agent。

    職責：
    - 接收 Collector 整理好的候選文章清單
    - 從中選出 5 篇最適合的文章
    - 依固定格式撰寫完整週報
    """

    SYSTEM = """你是「黛安娜」，每週為商學院學生與工程師撰寫科技週報的編輯。

語氣要求：
- 繁體中文，輕鬆自然但不輕浮，像懂產業的朋友在解說
- 幽默感來自觀察和措辭，不靠賣萌，不加感嘆號
- 不使用任何 emoji 或表情符號
- 禁用詞：值得關注、深刻影響、不容忽視、劃時代、引領未來

網址格式規則（嚴格遵守）：
- 網址直接貼出，不加任何括號或 Markdown 格式
- 正確：來源：TechCrunch https://techcrunch.com/2026/04/04/example
- 錯誤：來源：TechCrunch [https://techcrunch.com/...](https://techcrunch.com/...)"""

    NEWSLETTER_FORMAT = """黛安娜的科技蟹蟹水果報｜本週科技趨勢整理

開場：
（2-3 句，點出本週主旋律，有觀察感）

本週 5 大趨勢：

1. 新聞標題
發生什麼：（一句話說清楚核心）
商業視角：（對市場、商業模式、投資邏輯的意義，1-2 句）
科技視角：（對開發者、技術選型、工作流程的意義，1-2 句）
來源：媒體名稱 https://完整網址

（以此類推到第 5 則）

本週觀察：
（2-3 句，有自己的觀點）"""

    def run(self, client: openai.OpenAI, today: str, articles: str) -> str:
        """
        執行 Writer Agent，生成一份完整的週報草稿。

        參數：
        - client：OpenAI client
        - today：今天日期字串
        - articles：Collector 回傳的候選文章清單

        回傳：
        - Writer 生成的完整週報草稿（純文字）
        """
        print("\n[Writer Agent] 開始撰寫週報...")

        # Writer 是單輪生成，不需要迴圈。
        # system prompt 放在 messages[0]，user message 放在 messages[1]。
        resp = client.chat.completions.create(
            # gpt-4o 對應原本的 claude-opus，是 OpenAI 目前品質最高的標準模型。
            model="gpt-4o",
            max_tokens=4096,
            messages=[
                {"role": "system", "content": self.SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"今天是 {today}。\n\n"
                        f"以下是精選候選文章：\n{articles}\n\n"
                        f"請從中挑 5 篇，依以下格式撰寫週報。只輸出週報內文，不要任何說明。\n\n"
                        f"{self.NEWSLETTER_FORMAT}"
                    ),
                },
            ],
        )

        # 取出模型生成的週報草稿。
        draft = resp.choices[0].message.content

        print(f"  [Writer] 草稿完成（{len(draft)} 字）")
        return draft


# ════════════════════════════════════════════════════════════
# Orchestrator
# ════════════════════════════════════════════════════════════


class OrchestratorAgent:
    """
    協調整個多代理流程的主控 Agent。
    """

    def run(self):
        """
        按順序執行 pipeline 的每個步驟。
        每個步驟都有獨立的 try/except，失敗時會印出明確的錯誤位置。
        """
        # 建立 OpenAI client。
        # 原本 anthropic.Anthropic() 現在換成 openai.OpenAI()，介面類似。
        client = openai.OpenAI(api_key=OPENAI_API_KEY)

        today = datetime.now(TAIWAN_TZ).strftime("%Y年%m月%d日")

        print("=" * 55)
        print("  黛安娜的科技蟹蟹水果報  ·  Agent Pipeline 啟動")
        print("=" * 55)

        # ── Step 1：載入記憶 ──────────────────────────────────
        print("\n[Step 1] 載入歷史記憶")
        memory_context = tool_load_memory()
        preview = memory_context[:120] + "..." if len(memory_context) > 120 else memory_context
        print(f"  {preview}")

        # ── Step 2：Collector ─────────────────────────────────
        print("\n[Step 2] Collector Agent 蒐集候選文章")
        try:
            articles = CollectorAgent().run(client, today, memory_context)
        except Exception as e:
            print(f"  ! [Step 2] Collector 失敗：{e}")
            return

        if not articles or articles.count("\n") < 3:
            print("  ! [Step 2] Collector 回傳文章不足（少於 4 篇），終止 Pipeline。")
            return

        # ── Step 3：Writer ────────────────────────────────────
        print("\n[Step 3] Writer Agent 撰寫週報")
        try:
            draft = WriterAgent().run(client, today, articles)
        except Exception as e:
            print(f"  ! [Step 3] Writer 失敗：{e}")
            return

        # ── Step 4：Reflection ────────────────────────────────
        print("\n[Step 4] Reflection 審稿")
        try:
            final_draft = reflect_on_draft(client, draft)
        except Exception as e:
            print(f"  ! [Step 4] Reflection 失敗，使用未審稿的草稿：{e}")
            final_draft = draft

        # ── Step 5：發送 Discord ──────────────────────────────
        print("\n[Step 5] 發送到 Discord")
        try:
            result = tool_post_discord(final_draft)
            print(f"  {result}")
        except Exception as e:
            print(f"  ! [Step 5] Discord 發送失敗：{e}")
            return

        # ── Step 6：儲存記憶 ──────────────────────────────────
        print("\n[Step 6] 儲存本週標題到記憶")
        try:
            titles = _extract_titles(final_draft)
            save_result = tool_save_memory(titles)
            print(f"  {save_result}")
        except Exception as e:
            print(f"  ! [Step 6] 記憶儲存失敗（不影響已發送的週報）：{e}")

        print("\n" + "=" * 55)
        print("  Pipeline 完成")
        print("=" * 55)


def _extract_titles(newsletter: str) -> list[str]:
    """
    從週報內文中抽出 5 則新聞的標題。
    """
    titles = []
    for line in newsletter.splitlines():
        m = re.match(r"^\d+[.\uff0e]\s+(.+)", line.strip())
        if m:
            titles.append(m.group(1).strip())
        if len(titles) >= 5:
            break
    return titles


if __name__ == "__main__":
    OrchestratorAgent().run()

import anthropic
import re
import requests
import xml.etree.ElementTree as ET
import os
import json
from datetime import datetime, timezone, timedelta

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TAIWAN_TZ   = timezone(timedelta(hours=8))
MEMORY_FILE = "newsletter_memory.json"

RSS_FEEDS = {
    "TechCrunch":  "https://techcrunch.com/feed/",
    "The Verge":   "https://www.theverge.com/rss/index.xml",
    "Wired":       "https://www.wired.com/feed/rss",
    "Ars Technica":"https://feeds.arstechnica.com/arstechnica/index",
    "iThome":      "https://www.ithome.com.tw/rss",
}

# ════════════════════════════════════════════════════════════
#  Tool 實作
# ════════════════════════════════════════════════════════════

def tool_fetch_rss(source: str) -> str:
    url = RSS_FEEDS.get(source)
    if not url:
        return f"找不到來源：{source}。可用來源：{', '.join(RSS_FEEDS)}"
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(resp.content)
        items = (root.findall(".//item") or
                 root.findall(".//{http://www.w3.org/2005/Atom}entry"))
        results = []
        for item in items[:5]:
            title = (item.findtext("title") or
                     item.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
            link  = (item.findtext("link") or
                     item.findtext("{http://www.w3.org/2005/Atom}link") or "").strip()
            if not link:
                link_el = item.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href", "").strip()
            if title and link.startswith("https://"):
                results.append(f"{title} | {link}")
        return "\n".join(results) if results else f"{source}：無法取得文章"
    except Exception as e:
        return f"{source} 失敗：{e}"


# FIX #2：改為迴圈切割，解決 rfind=-1 及只能切兩段的問題
def _split_chunks(content: str, limit: int = 3800) -> list[str]:
    chunks = []
    while len(content) > limit:
        cut = content.rfind("\n", 0, limit)
        if cut == -1:       # 整段沒有換行，強制硬切
            cut = limit
        chunks.append(content[:cut])
        content = content[cut:].strip()
    if content:
        chunks.append(content)
    return chunks


def tool_post_discord(content: str) -> str:
    today    = datetime.now(TAIWAN_TZ).strftime("%Y/%m/%d")
    week_num = datetime.now(TAIWAN_TZ).isocalendar()[1]
    chunks   = _split_chunks(content)

    for i, chunk in enumerate(chunks):
        tag = f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
        payload = {
            "username": "黛安娜的科技蟹蟹水果報",
            "embeds": [{
                "title":       f"黛安娜的科技蟹蟹水果報  Week {week_num} | {today}{tag}",
                "description": chunk,
                "color":       0xC0392B,
                "footer":      {"text": "Powered by Claude AI | 每週五 18:00 發布"},
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            }],
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if resp.status_code not in (200, 204):
            return f"Discord 發送失敗：HTTP {resp.status_code} — {resp.text}"
    return f"成功發送（共 {len(chunks)} 則訊息）"


# ── Memory ────────────────────────────────────────────────

def tool_load_memory() -> str:
    if not os.path.exists(MEMORY_FILE):
        return "尚無歷史記錄，這是第一期。"
    # FIX #7：捕捉 JSON 損壞，避免整個 pipeline crash
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "記憶檔案損壞，視為第一期重新開始。"
    recent = data[-4:]
    lines = []
    for week in recent:
        lines.append(f"Week {week['week']} ({week['date']})：")
        for title in week["titles"]:
            lines.append(f"  - {title}")
    return "\n".join(lines) if lines else "尚無歷史記錄。"


def tool_save_memory(titles: list[str]) -> str:
    # FIX #7：同樣保護讀取端
    data = []
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = []
    now = datetime.now(TAIWAN_TZ)
    data.append({
        "week":   now.isocalendar()[1],
        "date":   now.strftime("%Y/%m/%d"),
        "titles": titles,
    })
    data = data[-12:]
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return f"已儲存 {len(titles)} 筆標題到記憶"


# ════════════════════════════════════════════════════════════
#  Tool Schema
# ════════════════════════════════════════════════════════════

FETCH_RSS_TOOL = {
    "name": "fetch_rss",
    "description": (
        "抓取指定科技媒體的最新文章清單，回傳標題和完整網址。"
        "可用來源：TechCrunch, The Verge, Wired, Ars Technica, iThome。"
        "每個來源需要分別呼叫一次。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "媒體名稱，必須完全符合可用來源之一",
            }
        },
        "required": ["source"],
    },
}

POST_DISCORD_TOOL = {
    "name": "post_discord",
    "description": (
        "把完成的週報內容發送到 Discord。"
        "只在週報完整寫好後才呼叫，蒐集資料期間不要呼叫。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "完整的週報文字，包含開場、5 則新聞分析、本週觀察",
            }
        },
        "required": ["content"],
    },
}

# ════════════════════════════════════════════════════════════
#  Tool 執行器
# ════════════════════════════════════════════════════════════

def execute_tool(name: str, inputs: dict) -> str:
    if name == "fetch_rss":
        result = tool_fetch_rss(inputs["source"])
        print(f"    [tool] fetch_rss({inputs['source']}): {result.count(chr(10))+1} 篇")
        return result
    if name == "post_discord":
        result = tool_post_discord(inputs["content"])
        print(f"    [tool] post_discord: {result}")
        return result
    return f"未知工具：{name}"


# ════════════════════════════════════════════════════════════
#  Reflection
# ════════════════════════════════════════════════════════════

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


def reflect_on_draft(client: anthropic.Anthropic, draft: str, max_retries: int = 2) -> str:
    current = draft
    for attempt in range(max_retries + 1):
        resp = client.messages.create(
            # FIX #6：審稿是規則比對，sonnet 足夠，不需要 opus
            model="claude-sonnet-4-5",
            max_tokens=4096,
            system=CRITIC_SYSTEM,
            messages=[{"role": "user", "content": f"請審查以下草稿：\n\n{current}"}],
        )
        text = resp.content[0].text.strip()

        if text.startswith("APPROVED"):
            print(f"  [reflection] 第 {attempt + 1} 次審查通過")
            return current

        if "REVISED" in text:
            idx     = text.index("REVISED") + len("REVISED")
            current = text[idx:].strip()
            print(f"  [reflection] 第 {attempt + 1} 次：發現問題，已修改")
        else:
            print(f"  [reflection] 第 {attempt + 1} 次：格式異常，保留現版本")
            break

    print("  [reflection] 已達最大審查次數，使用最後版本")
    return current


# ════════════════════════════════════════════════════════════
#  Multi-Agent
# ════════════════════════════════════════════════════════════

class CollectorAgent:

    SYSTEM = """你是資料蒐集員，負責抓取 RSS 並精選文章。

任務：
1. 逐一呼叫 fetch_rss 抓取全部 5 個來源
2. 對照已報導標題，排除高度重疊的主題
3. 挑出 7 篇候選（多給 2 篇緩衝供後續撰稿員選擇）

輸出格式（純文字，每篇一行，不要分析）：
來源名稱 | 文章標題 | 網址

只輸出這 7 行，不加任何其他文字。"""

    # FIX #4：加入 MAX_ITER 防止無限迴圈
    MAX_ITER = 15

    def run(self, client: anthropic.Anthropic, today: str, memory_context: str) -> str:
        print("\n[Collector Agent] 開始蒐集文章...")
        messages = [{
            "role": "user",
            "content": (
                f"今天是 {today}。\n\n"
                f"以下是過去已報導的標題，請避開相似主題：\n{memory_context}\n\n"
                "請逐一呼叫 fetch_rss 抓取全部 5 個來源，挑出 7 篇候選文章。"
            ),
        }]

        for iteration in range(self.MAX_ITER):
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=2048,
                system=self.SYSTEM,
                tools=[FETCH_RSS_TOOL],
                messages=messages,
            )
            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "end_turn":
                for block in resp.content:
                    if hasattr(block, "text") and block.text.strip():
                        print(f"  [Collector] 完成（{block.text.count(chr(10))+1} 篇候選）")
                        return block.text
                return ""

            if resp.stop_reason == "tool_use":
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        result = execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})

        print("  ! [Collector] 已達最大迭代次數，強制終止")
        return ""


class WriterAgent:

    # FIX #1：修正 system prompt 中自相矛盾的網址範例
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

    def run(self, client: anthropic.Anthropic, today: str, articles: str) -> str:
        print("\n[Writer Agent] 開始撰寫週報...")
        resp = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=self.SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    f"今天是 {today}。\n\n"
                    f"以下是精選候選文章：\n{articles}\n\n"
                    f"請從中挑 5 篇，依以下格式撰寫週報。只輸出週報內文，不要任何說明。\n\n"
                    f"{self.NEWSLETTER_FORMAT}"
                ),
            }],
        )
        draft = resp.content[0].text
        print(f"  [Writer] 草稿完成（{len(draft)} 字）")
        return draft


# ════════════════════════════════════════════════════════════
#  Orchestrator
# ════════════════════════════════════════════════════════════

class OrchestratorAgent:

    def run(self):
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        today  = datetime.now(TAIWAN_TZ).strftime("%Y年%m月%d日")

        print("=" * 55)
        print("  黛安娜的科技蟹蟹水果報  ·  Agent Pipeline 啟動")
        print("=" * 55)

        # ── Step 1：載入記憶 ──────────────────────────────────
        print("\n[Step 1] 載入歷史記憶")
        memory_context = tool_load_memory()
        preview = memory_context[:120] + "..." if len(memory_context) > 120 else memory_context
        print(f"  {preview}")

        # ── Step 2：Collector ─────────────────────────────────
        # FIX #8：每個 Step 獨立 try/except，失敗時明確說明死在哪
        print("\n[Step 2] Collector Agent 蒐集候選文章")
        try:
            articles = CollectorAgent().run(client, today, memory_context)
        except Exception as e:
            print(f"  ! [Step 2] Collector 失敗：{e}")
            return

        # FIX #5：驗證 Collector 回傳是否足夠，不夠就提早終止
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
            final_draft = draft   # 降級：直接用原稿

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
            titles     = _extract_titles(final_draft)
            save_result = tool_save_memory(titles)
            print(f"  {save_result}")
        except Exception as e:
            print(f"  ! [Step 6] 記憶儲存失敗（不影響已發送的週報）：{e}")

        print("\n" + "=" * 55)
        print("  Pipeline 完成")
        print("=" * 55)


# FIX #3：用正則表達式，正確處理一位數和兩位數編號
def _extract_titles(newsletter: str) -> list[str]:
    titles = []
    for line in newsletter.splitlines():
        m = re.match(r"^\d+[.．]\s+(.+)", line.strip())
        if m:
            titles.append(m.group(1).strip())
        if len(titles) >= 5:
            break
    return titles


# ════════════════════════════════════════════════════════════
#  Entry Point
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    OrchestratorAgent().run()

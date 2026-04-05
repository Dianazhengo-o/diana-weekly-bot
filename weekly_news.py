import anthropic
import requests
import xml.etree.ElementTree as ET
import os
from datetime import datetime, timezone, timedelta

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TAIWAN_TZ = timezone(timedelta(hours=8))

RSS_FEEDS = {
    "TechCrunch":  "https://techcrunch.com/feed/",
    "The Verge":   "https://www.theverge.com/rss/index.xml",
    "Wired":       "https://www.wired.com/feed/rss",
    "Ars Technica":"https://feeds.arstechnica.com/arstechnica/index",
    "iThome":      "https://www.ithome.com.tw/rss",
}

# ── Tool 實作（Claude 可以呼叫的函式）────────────────────

def tool_fetch_rss(source: str) -> str:
    """抓取單一來源的 RSS 文章"""
    url = RSS_FEEDS.get(source)
    if not url:
        available = ", ".join(RSS_FEEDS.keys())
        return f"找不到來源：{source}。可用來源：{available}"
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


def tool_post_discord(content: str) -> str:
    """把完成的週報發送到 Discord"""
    today    = datetime.now(TAIWAN_TZ).strftime("%Y/%m/%d")
    week_num = datetime.now(TAIWAN_TZ).isocalendar()[1]
    LIMIT    = 3800

    chunks = [content] if len(content) <= LIMIT else [
        content[:content.rfind("\n", 0, LIMIT)],
        content[content.rfind("\n", 0, LIMIT):].strip()
    ]

    for i, chunk in enumerate(chunks):
        tag = f" ({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
        payload = {
            "username": "黛安娜的科技蟹蟹水果報",
            "embeds": [{
                "title":       f"黛安娜的科技蟹蟹水果報  Week {week_num} | {today}{tag}",
                "description": chunk,
                "color":       0xC0392B,
                "footer":      {"text": "Powered by Claude AI | 每週五 18:00 發布"},
                "timestamp":   datetime.now(timezone.utc).isoformat()
            }]
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        if resp.status_code not in (200, 204):
            return f"Discord 發送失敗：HTTP {resp.status_code} — {resp.text}"

    return f"成功發送（共 {len(chunks)} 則訊息）"


# ── Tool Schema（告訴 Claude 有哪些工具可以用）────────────

TOOLS = [
    {
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
                    "description": "媒體名稱，必須完全符合可用來源之一"
                }
            },
            "required": ["source"]
        }
    },
    {
        "name": "post_discord",
        "description": (
            "把完成的週報內容發送到 Discord。"
            "只在週報完整寫好之後才呼叫，不要在還在蒐集資料時呼叫。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "完整的週報文字，包含開場、5 則新聞分析、本週觀察"
                }
            },
            "required": ["content"]
        }
    }
]


# ── Tool 執行器（把 Claude 的呼叫對應到實際函式）──────────

def execute_tool(name: str, inputs: dict) -> str:
    if name == "fetch_rss":
        result = tool_fetch_rss(inputs["source"])
        print(f"  ✓ fetch_rss({inputs['source']}): 取得 {result.count(chr(10))+1} 篇")
        return result
    elif name == "post_discord":
        result = tool_post_discord(inputs["content"])
        print(f"  ✓ post_discord: {result}")
        return result
    return f"未知工具：{name}"


# ── Agent 主迴圈────────────────────────────────────────────

def run_agent():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now(TAIWAN_TZ).strftime("%Y年%m月%d日")

    system_prompt = """你是「黛安娜」，每週為商學院學生與工程師撰寫科技週報的編輯。

語氣要求：
- 繁體中文，輕鬆自然但不輕浮，像懂產業的朋友在解說
- 幽默感來自觀察和措辭，不靠賣萌，不加感嘆號
- 不使用任何 emoji 或表情符號
- 禁用詞：值得關注、深刻影響、不容忽視、劃時代、引領未來

來源規則：
- 每個 fetch_rss 只能抓一個來源，需要逐一呼叫
- 網址直接貼，不加任何括號、Markdown 格式或標點符號
  正確：來源：TechCrunch https://techcrunch.com/2026/04/04/example
  錯誤：來源：TechCrunch [https://...](https://...)"""

    user_prompt = f"""今天是 {today}。

請完成以下任務：
1. 用 fetch_rss 逐一抓取所有 5 個來源的文章（TechCrunch, The Verge, Wired, Ars Technica, iThome）
2. 從中挑出最重要、最值得科技人和商學院學生關注的 5 篇
3. 撰寫本週的《黛安娜的科技蟹蟹水果報》，格式如下：

黛安娜的科技蟹蟹水果報｜本週科技趨勢整理

開場：
（2-3 句，點出本週主旋律，有觀察感）

本週 5 大趨勢：

1. 新聞標題
發生什麼：（一句話說清楚核心）
商業視角：（對市場、商業模式、投資邏輯的意義，1-2 句）
科技視角：（對開發者、技術選型、工作流程的意義，1-2 句）
來源：媒體名稱 網址

（以此類推到第 5 則）

本週觀察：
（2-3 句，有自己的觀點）

4. 週報寫完後，用 post_discord 發送。"""

    messages = [{"role": "user", "content": user_prompt}]

    print("Agent 啟動，開始執行任務...")
    print("=" * 50)

    iteration = 0
    max_iterations = 20  # 防止無限迴圈

    while iteration < max_iterations:
        iteration += 1

        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages
        )

        # 把 Claude 的回應加進對話歷史
        messages.append({"role": "assistant", "content": response.content})

        # 任務完成，結束迴圈
        if response.stop_reason == "end_turn":
            print("\n✓ Agent 完成所有任務")
            break

        # Claude 要呼叫 tool
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"\nStep {iteration}：呼叫 {block.name}")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            # 把 tool 結果還給 Claude，讓它決定下一步
            messages.append({"role": "user", "content": tool_results})

        else:
            print(f"意外的 stop_reason：{response.stop_reason}")
            break

    if iteration >= max_iterations:
        print("已達最大迭代次數，強制停止")


if __name__ == "__main__":
    run_agent()

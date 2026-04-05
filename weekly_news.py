import anthropic
import requests
import os
from datetime import datetime, timezone, timedelta

# GitHub Actions 版：Key 從環境變數讀取
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
TAIWAN_TZ = timezone(timedelta(hours=8))

# ── Step 1：專門搜尋，只輸出標題 + 真實 URL ──────────────────────────────
def search_real_urls():
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now(TAIWAN_TZ).strftime('%Y年%m月%d日')

    prompt = f"""
今天是 {today}。請搜尋「最近 7 天內最重要的 5 則科技新聞」。

搜尋時只能使用以下媒體的文章，不可使用其他來源：
英文媒體：TechCrunch、The Verge、Wired、Bloomberg、Reuters、Ars Technica
中文媒體：iThome、數位時代、科技新報、Tech Orange、聯合新聞網科技版

搜尋完畢後，只輸出以下格式，不要寫任何其他文字：

1. 新聞標題 | 完整網址
2. 新聞標題 | 完整網址
3. 新聞標題 | 完整網址
4. 新聞標題 | 完整網址
5. 新聞標題 | 完整網址

規則：
- 網址必須是你搜尋時真實看到的完整 URL，以 https:// 開頭
- 禁止使用新聞聚合站（如 techstartups.com、coaio.com 這類站）
- 禁止自己推測或補全任何網址
- 只輸出這 5 行，不要有任何其他文字
"""

    response = client.messages.create(
        model='claude-opus-4-5',
        max_tokens=1024,
        tools=[{
            'type': 'web_search_20260209',
            'name': 'web_search',
            'max_uses': 8,
        }],
        messages=[{'role': 'user', 'content': prompt}]
    )

    for block in response.content:
        if block.type == 'text':
            return block.text.strip()
    return ''


# ── Step 2：拿到真實 URL 後，再讓 Claude 寫完整報告 ─────────────────────
def write_report_with_urls(url_list_text):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now(TAIWAN_TZ).strftime('%Y年%m月%d日')

    prompt = f"""
你是「黛安娜」，固定在每週五晚上為一群商學院學生與工程師撰寫科技週報的編輯。
今天是 {today}。

以下是本週 5 則科技新聞的標題和真實網址：

{url_list_text}

請根據這些新聞，撰寫本週的《黛安娜的科技蟹蟹水果報》。

【語氣要求】
- 繁體中文。語氣輕鬆自然但不輕浮，像懂產業的朋友在解說。
- 幽默感來自觀察和措辭，不靠賣萌，不加感嘆號。
- 不使用任何 emoji 或表情符號。
- 禁用詞：值得關注、深刻影響、不容忽視、劃時代、引領未來。

【讀者】
- 一半商學院：關心產業趨勢、商業模式、競爭格局、投資方向。
- 一半工程師：關心工具變化、技術方向、開發工作流、基礎設施。

【輸出格式，嚴格照做】

黛安娜的科技蟹蟹水果報｜本週科技趨勢整理

開場：
2-3 句，點出這週科技圈的主旋律。要有觀察感，不要是流水帳。

本週 5 大趨勢：

1. 新聞標題
發生什麼：一句話說清楚核心。
商業視角：對市場、商業模式、投資邏輯的意義，1-2 句。
科技視角：對開發者、技術選型、工作流程的意義，1-2 句。
來源：媒體名稱（從上方清單直接複製對應的完整網址）

2. 新聞標題
發生什麼：
商業視角：
科技視角：
來源：

3. 新聞標題
發生什麼：
商業視角：
科技視角：
來源：

4. 新聞標題
發生什麼：
商業視角：
科技視角：
來源：

5. 新聞標題
發生什麼：
商業視角：
科技視角：
來源：

本週觀察：
2-3 句總結，這週主旋律是什麼、接下來最值得追什麼。要有自己的觀點。
"""

    response = client.messages.create(
        model='claude-opus-4-5',
        max_tokens=4096,
        messages=[{'role': 'user', 'content': prompt}]   # 這步不需要 web_search
    )

    all_text = []
    for block in response.content:
        if block.type == 'text':
            all_text.append(block.text)
    return '\n'.join(all_text) if all_text else '無法取得內容'


# ── 發送到 Discord（超長自動拆兩則）────────────────────────────────────────
def post_to_discord(news_text):
    today    = datetime.now(TAIWAN_TZ).strftime('%Y/%m/%d')
    week_num = datetime.now(TAIWAN_TZ).isocalendar()[1]
    LIMIT    = 3800

    chunks = [news_text] if len(news_text) <= LIMIT else [
        news_text[:news_text.rfind('\n', 0, LIMIT)],
        news_text[news_text.rfind('\n', 0, LIMIT):].strip()
    ]

    for i, chunk in enumerate(chunks):
        tag = f' ({i+1}/{len(chunks)})' if len(chunks) > 1 else ''
        payload = {
            'username': '黛安娜的科技蟹蟹水果報',
            'embeds': [{
                'title':       f'黛安娜的科技蟹蟹水果報  Week {week_num} | {today}{tag}',
                'description': chunk,
                'color':       0xC0392B,
                'footer':      {'text': 'Powered by Claude AI | 每週五 18:00 發布'},
                'timestamp':   datetime.now(timezone.utc).isoformat()
            }]
        }
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        print(f'發送成功（第 {i+1} 則）' if resp.status_code in (200, 204)
              else f'失敗：{resp.status_code} — {resp.text}')




if __name__ == "__main__":
    print("Step 1：搜尋真實 URL...")
    url_list = search_real_urls()
    print(url_list)

    print("\nStep 2：撰寫週報...")
    news = write_report_with_urls(url_list)
    print(news)
    print(f"\n全文字數：{len(news)} 字")

    print("\nStep 3：發送到 Discord...")
    post_to_discord(news)

        
    


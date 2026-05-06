import asyncio
from telethon import TelegramClient, events
import json
import urllib.request
import urllib.parse

# ==========================================
# ★★★ 設定區 (保留您的 ID) ★★★
# ==========================================

# 1. 來源頻道列表 (共 4 個)
source_chat_ids = [
    -1003543044025, 
    -1001674914929, 
    -1002721984707, 
    -1002874705988,
]

# 2. Discord 目標頻道設定
#    注意：請填入你的 Discord Bot Token 與頻道 ID
discord_bot_token = ""
discord_channel_id = 1493256513756598272

# ==========================================
# 系統金鑰 (TG 專用)
# ==========================================
api_id = 29076154
api_hash = '3be7ce3d6ab963e4996bb816bc54c6ea'

print("--------------------------------------------------")
print(f"模式：【極速直接轉發 (無 AI)】")
print(f"正在監聽 {len(source_chat_ids)} 個來源頻道...")
print(f"Discord 頻道 ID: {discord_channel_id}")
print("機器人啟動中... (按 Ctrl+C 可停止)")
print("--------------------------------------------------")

client = TelegramClient('mysession', api_id, api_hash)

async def translate_text_to_zh_tw(text: str):
    if not text or not text.strip():
        return text

    def _translate():
        query = urllib.parse.quote(text)
        url = (
            "https://translate.googleapis.com/translate_a/single"
            f"?client=gtx&sl=auto&tl=zh-TW&dt=t&q={query}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = []
        if isinstance(data, list) and data and isinstance(data[0], list):
            for item in data[0]:
                if isinstance(item, list) and item:
                    parts.append(str(item[0]))
        translated = "".join(parts).strip()
        return translated if translated else text

    try:
        return await asyncio.to_thread(_translate)
    except Exception:
        return text

async def send_to_discord(content: str):
    if not discord_bot_token:
        raise Exception("尚未設定 discord_bot_token")

    url = f"https://discord.com/api/v10/channels/{discord_channel_id}/messages"
    headers = {
        "Authorization": f"Bot {discord_bot_token}",
        "Content-Type": "application/json",
        "User-Agent": "TelegramForwardBot/1.0"
    }
    payload = {"content": f"BOT: @everyone {(content.strip() or ' ')}".strip()}

    def _post():
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.getcode()

    status = await asyncio.to_thread(_post)
    if not (200 <= status < 300):
        raise Exception(f"Discord HTTP 狀態碼: {status}")

@client.on(events.NewMessage(chats=source_chat_ids))
async def handler(event):
    # 取得原始訊息內容
    original_text = event.message.message
    
    # 取得來源名稱 (方便在黑視窗看)
    chat = await event.get_chat()
    chat_title = chat.title if chat.title else str(chat.id)

    # 如果是完全空白的訊息(極少見)則跳過
    if not original_text and not event.message.media:
        return

    print(f"\n[新訊來自: {chat_title}]")
    print(f"內容摘要: {original_text[:15]}...") 

    try:
        content = await translate_text_to_zh_tw(original_text or "")
        if event.message.media:
            content = f"{content}\n\n[附註] 原訊息包含媒體，當前版本僅轉發文字。"

        await send_to_discord(content)
            
        print(f"✅ [秒轉發到 Discord] 成功！")

    except Exception as e:
        print(f"❌ [錯誤] {e}")

# 啟動程式
client.start()
client.run_until_disconnected()
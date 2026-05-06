from telethon import TelegramClient
import asyncio
import sys

# ================= 您的資料 =================
api_id = 29076154
api_hash = '3be7ce3d6ab963e4996bb816bc54c6ea'
# ==========================================

client = TelegramClient('mysession', api_id, api_hash)

async def main():
    print("\n===========================================")
    print("登入成功！正在讀取您的群組清單...")
    print("===========================================\n")
    
    async for dialog in client.iter_dialogs():
        # 只列出群組和頻道
        if dialog.is_group or dialog.is_channel:
            # 為了避免特殊符號導致顯示錯誤，我們強制轉換編碼
            safe_name = dialog.name.encode('utf-8', 'ignore').decode('utf-8')
            print(f"名稱: {safe_name}")
            print(f"ID:   {dialog.id}")
            print("-------------------------------------------")
    
    print("\n程式執行完畢。請複製您需要的 ID。")
    input("按 Enter 鍵關閉視窗...")

if __name__ == '__main__':
    # 1. 強制先問手機號碼，確保您看得到提示
    print("程式啟動中...", flush=True)
    phone = input("請輸入您的手機號碼 (例如 +886912345678) 並按 Enter: ")
    
    # 2. 帶入號碼啟動
    print("正在連線到 Telegram...", flush=True)
    
    # 這一步會自動檢查驗證碼，如果需要輸入，它會跳出來問
    client.start(phone=phone)
    
    # 3. 執行主程式
    client.loop.run_until_complete(main())
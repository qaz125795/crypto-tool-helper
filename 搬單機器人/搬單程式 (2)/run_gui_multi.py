import asyncio
import threading
import re
import json
import os
import urllib.request
import urllib.parse
import queue
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityUrl, MessageEntityTextUrl

class BotInstance:
    """單個機器人實例"""
    def __init__(self, config):
        self.config = config
        self.client = None
        self.is_running = False
        self.loop = None
        self.thread = None
        # 用來優雅地停止機器人的事件（在事件迴圈內等待）
        self.stop_event = None
        self.name = config.get('name', f"帳號{config.get('instance_id', 1)}")
        self.session_file = config.get('session_file', f"mysession_{config.get('instance_id', 1)}")
    
    def to_dict(self):
        """轉換為字典以便儲存"""
        return {
            'name': self.name,
            'api_id': self.config.get('api_id'),
            'api_hash': self.config.get('api_hash'),
            'source_chat_ids': self.config.get('source_chat_ids', []),
            'source_topic_ids': self.config.get('source_topic_ids', []),
            'target_chat_id': self.config.get('target_chat_id'),
            'target_topic_id': self.config.get('target_topic_id'),
            'session_file': self.session_file,
            'instance_id': self.config.get('instance_id'),
            'filter_media': self.config.get('filter_media', True),
            'filter_url': self.config.get('filter_url', True),
            'discord_bot_token': self.config.get('discord_bot_token', ''),
            'discord_channel_id': self.config.get('discord_channel_id', ''),
        }

    def set_stop(self):
        """從主執行緒安全地請求停止（觸發 stop_event）"""
        if self.loop and self.stop_event:
            try:
                self.loop.call_soon_threadsafe(self.stop_event.set)
            except RuntimeError:
                # 事件迴圈已關閉或無效，忽略
                pass

class TelegramMultiBotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram 搬單機器人 - 多開版")
        self.root.geometry("1000x800")
        self.root.resizable(True, True)
        
        # 機器人實例列表
        self.bot_instances = {}
        self.instance_counter = 0
        self.config_file = "bot_instances_config.json"
        
        # 先建立介面，再載入配置（因為 log 需要 log_text）
        self.create_widgets()
        
        # 載入已儲存的配置
        self.load_config()
    
    def create_widgets(self):
        # 標題
        title_label = tk.Label(self.root, text="Telegram 搬單機器人 - 多帳號管理", 
                              font=("Microsoft JhengHei", 16, "bold"))
        title_label.pack(pady=10)
        
        # 建立筆記本（分頁）
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 機器人管理頁
        manage_frame = ttk.Frame(notebook)
        notebook.add(manage_frame, text="🤖 機器人管理")
        
        # 廣播功能頁
        broadcast_frame = ttk.Frame(notebook)
        notebook.add(broadcast_frame, text="📢 廣播功能")
        
        # 日誌頁
        log_frame = ttk.Frame(notebook)
        notebook.add(log_frame, text="📊 運行日誌")
        
        self.create_manage_tab(manage_frame)
        self.create_broadcast_tab(broadcast_frame)
        self.create_log_tab(log_frame)
    
    def create_manage_tab(self, parent):
        # 左側：機器人列表
        left_frame = ttk.Frame(parent)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 機器人列表標題
        list_label = tk.Label(left_frame, text="已配置的機器人", 
                             font=("Microsoft JhengHei", 12, "bold"))
        list_label.pack(pady=5)
        
        # 機器人列表（使用 Treeview）
        list_container = ttk.Frame(left_frame)
        list_container.pack(fill=tk.BOTH, expand=True)
        
        # 建立 Treeview
        columns = ('name', 'status', 'sources', 'target')
        self.instance_tree = ttk.Treeview(list_container, columns=columns, show='tree headings', height=15)
        self.instance_tree.heading('#0', text='ID')
        self.instance_tree.heading('name', text='名稱')
        self.instance_tree.heading('status', text='狀態')
        self.instance_tree.heading('sources', text='來源數')
        self.instance_tree.heading('target', text='目標')
        
        self.instance_tree.column('#0', width=50)
        self.instance_tree.column('name', width=150)
        self.instance_tree.column('status', width=80)
        self.instance_tree.column('sources', width=80)
        self.instance_tree.column('target', width=150)
        
        # 滾動條
        scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.instance_tree.yview)
        self.instance_tree.configure(yscrollcommand=scrollbar.set)
        
        self.instance_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 綁定選擇事件
        self.instance_tree.bind('<<TreeviewSelect>>', self.on_instance_select)
        
        # 列表按鈕
        list_button_frame = ttk.Frame(left_frame)
        list_button_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(list_button_frame, text="➕ 新增機器人", 
                  command=self.show_add_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(list_button_frame, text="✏️ 編輯", 
                  command=self.edit_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(list_button_frame, text="🗑️ 刪除", 
                  command=self.delete_selected).pack(side=tk.LEFT, padx=2)
        
        # 右側：機器人控制
        right_frame = ttk.LabelFrame(parent, text="機器人控制", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, padx=5, pady=5, ipadx=10, ipady=10)
        
        # 選中的機器人資訊
        self.selected_info_text = scrolledtext.ScrolledText(right_frame, height=15, width=40,
                                                            state=tk.DISABLED, font=("Consolas", 9))
        self.selected_info_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 控制按鈕
        control_frame = ttk.Frame(right_frame)
        control_frame.pack(fill=tk.X, pady=5)
        
        self.start_button = ttk.Button(control_frame, text="▶️ 啟動", 
                                       command=self.start_selected, state=tk.DISABLED)
        self.start_button.pack(side=tk.LEFT, padx=2)
        
        self.stop_button = ttk.Button(control_frame, text="⏹️ 停止", 
                                      command=self.stop_selected, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=2)
        
        # 批量控制
        batch_frame = ttk.LabelFrame(right_frame, text="批量操作", padding=5)
        batch_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(batch_frame, text="▶️ 全部啟動", 
                  command=self.start_all).pack(pady=2, fill=tk.X)
        ttk.Button(batch_frame, text="⏹️ 全部停止", 
                  command=self.stop_all).pack(pady=2, fill=tk.X)
        
        # 更新列表顯示
        self.refresh_instance_list()
    
    def create_broadcast_tab(self, parent):
        """建立廣播功能分頁"""
        # 左側：選擇機器人和目標設定
        left_frame = ttk.Frame(parent)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)
        
        # 選擇機器人
        bot_frame = ttk.LabelFrame(left_frame, text="選擇發送帳號", padding=10)
        bot_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(bot_frame, text="選擇機器人:").pack(anchor=tk.W, pady=5)
        self.broadcast_bot_var = tk.StringVar()
        self.broadcast_bot_combo = ttk.Combobox(bot_frame, textvariable=self.broadcast_bot_var,
                                               state="readonly", width=30)
        self.broadcast_bot_combo.pack(fill=tk.X, pady=5)
        self.broadcast_bot_combo.bind('<<ComboboxSelected>>', self.on_broadcast_bot_select)
        
        # 目標設定
        target_frame = ttk.LabelFrame(left_frame, text="發送目標設定", padding=10)
        target_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(target_frame, text="目標群組 ID:").pack(anchor=tk.W, pady=5)
        self.broadcast_target_id_var = tk.StringVar()
        ttk.Entry(target_frame, textvariable=self.broadcast_target_id_var, width=30).pack(fill=tk.X, pady=5)
        
        tk.Label(target_frame, text="話題 ID (可選，留空則不使用):").pack(anchor=tk.W, pady=5)
        self.broadcast_topic_id_var = tk.StringVar()
        ttk.Entry(target_frame, textvariable=self.broadcast_topic_id_var, width=30).pack(fill=tk.X, pady=5)
        
        # 使用機器人的預設目標
        ttk.Button(target_frame, text="使用選中機器人的預設目標", 
                  command=self.use_default_target).pack(pady=5)

        # 廣播通道開關（可獨立控制）
        channel_toggle_frame = ttk.LabelFrame(left_frame, text="廣播通道", padding=10)
        channel_toggle_frame.pack(fill=tk.X, pady=5)
        self.broadcast_to_tg_var = tk.BooleanVar(value=True)
        self.broadcast_to_dc_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(channel_toggle_frame, text="TG 廣播", variable=self.broadcast_to_tg_var).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(channel_toggle_frame, text="DC 廣播", variable=self.broadcast_to_dc_var).pack(anchor=tk.W, pady=2)
        
        # 快速選擇
        quick_frame = ttk.LabelFrame(left_frame, text="快速選擇", padding=10)
        quick_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(quick_frame, text="使用機器人1的目標", 
                  command=lambda: self.use_bot_target(1)).pack(pady=2, fill=tk.X)
        ttk.Button(quick_frame, text="使用機器人2的目標", 
                  command=lambda: self.use_bot_target(2)).pack(pady=2, fill=tk.X)
        
        # 右側：文字輸入和發送
        right_frame = ttk.Frame(parent)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 文字輸入區
        input_frame = ttk.LabelFrame(right_frame, text="輸入要發送的文字", padding=10)
        input_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.broadcast_text = scrolledtext.ScrolledText(input_frame, height=20, width=50,
                                                       font=("Microsoft JhengHei", 11))
        self.broadcast_text.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 提示
        tk.Label(input_frame, text="提示：可輸入多行文字，支援換行", 
                foreground="gray", font=("Microsoft JhengHei", 8)).pack(pady=5)
        
        # 發送按鈕
        button_frame = ttk.Frame(right_frame)
        button_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(button_frame, text="📢 發送訊息", 
                  command=self.send_broadcast_message, style="Accent.TButton").pack(side=tk.LEFT, padx=5)
        
        ttk.Button(button_frame, text="清空文字", 
                  command=lambda: self.broadcast_text.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=5)
        
        # 發送歷史/狀態
        status_frame = ttk.LabelFrame(right_frame, text="發送狀態", padding=10)
        status_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.broadcast_status_text = scrolledtext.ScrolledText(status_frame, height=8, width=50,
                                                              font=("Consolas", 9), state=tk.DISABLED)
        self.broadcast_status_text.pack(fill=tk.BOTH, expand=True)
        
        # 更新機器人選單
        self.update_broadcast_bot_list()
    
    def update_broadcast_bot_list(self):
        """更新廣播機器人選單"""
        # 檢查廣播介面是否已經建立
        if not hasattr(self, 'broadcast_bot_combo'):
            return
        
        bot_names = []
        bot_ids = []
        for instance_id, bot in self.bot_instances.items():
            bot_names.append(f"{bot.name} (ID: {instance_id})")
            bot_ids.append(instance_id)
        
        if bot_names:
            self.broadcast_bot_combo['values'] = bot_names
            self.broadcast_bot_ids = bot_ids
        else:
            self.broadcast_bot_combo['values'] = []
            self.broadcast_bot_ids = []
    
    def on_broadcast_bot_select(self, event=None):
        """當選擇機器人時，自動填入目標"""
        self.use_default_target()
    
    def use_default_target(self):
        """使用選中機器人的預設目標"""
        selection = self.broadcast_bot_var.get()
        if not selection:
            return
        
        try:
            index = self.broadcast_bot_combo['values'].index(selection)
            instance_id = self.broadcast_bot_ids[index]
            bot = self.bot_instances.get(instance_id)
            
            if bot:
                self.broadcast_target_id_var.set(str(bot.config.get('target_chat_id', '')))
                topic_id = bot.config.get('target_topic_id')
                self.broadcast_topic_id_var.set(str(topic_id) if topic_id else '')
        except:
            pass
    
    def use_bot_target(self, bot_id):
        """使用指定機器人的目標"""
        bot = self.bot_instances.get(bot_id)
        if bot:
            self.broadcast_target_id_var.set(str(bot.config.get('target_chat_id', '')))
            topic_id = bot.config.get('target_topic_id')
            self.broadcast_topic_id_var.set(str(topic_id) if topic_id else '')
            
            # 同時選擇該機器人
            for i, instance_id in enumerate(self.broadcast_bot_ids):
                if instance_id == bot_id:
                    if i < len(self.broadcast_bot_combo['values']):
                        self.broadcast_bot_var.set(self.broadcast_bot_combo['values'][i])
                    break
    
    def send_broadcast_message(self):
        """發送廣播訊息"""
        # 取得選擇的機器人
        selection = self.broadcast_bot_var.get()
        if not selection:
            messagebox.showerror("錯誤", "請先選擇要使用的機器人帳號")
            return
        
        try:
            index = self.broadcast_bot_combo['values'].index(selection)
            instance_id = self.broadcast_bot_ids[index]
            bot = self.bot_instances.get(instance_id)
        except:
            messagebox.showerror("錯誤", "無法取得機器人資訊")
            return
        
        # 檢查機器人是否已啟動
        if not bot.is_running:
            messagebox.showwarning("警告", 
                                 f"機器人「{bot.name}」未啟動！\n"
                                 "是否需要先啟動機器人才能發送訊息？")
            # 嘗試發送（因為機器人即使未在監聽狀態，客戶端可能仍可用）
        
        # 取得目標設定
        target_id_str = self.broadcast_target_id_var.get().strip()
        if not target_id_str:
            messagebox.showerror("錯誤", "請輸入目標群組 ID")
            return
        
        try:
            target_chat_id = int(target_id_str)
        except ValueError:
            messagebox.showerror("錯誤", "目標群組 ID 必須是數字")
            return
        
        topic_id_str = self.broadcast_topic_id_var.get().strip()
        target_topic_id = int(topic_id_str) if topic_id_str else None
        
        # 取得要發送的文字
        message_text = self.broadcast_text.get("1.0", tk.END).strip()
        if not message_text:
            messagebox.showerror("錯誤", "請輸入要發送的文字")
            return
        
        # 顯示狀態
        self.broadcast_log(f"正在發送訊息...")
        self.broadcast_log(f"機器人: {bot.name}")
        self.broadcast_log(f"目標: {target_chat_id}" + (f" (話題: {target_topic_id})" if target_topic_id else ""))
        self.broadcast_log(f"內容: {message_text[:50]}...")
        send_tg = bool(self.broadcast_to_tg_var.get())
        send_dc = bool(self.broadcast_to_dc_var.get())
        if not send_tg and not send_dc:
            messagebox.showerror("錯誤", "請至少勾選一個廣播通道（TG 或 DC）")
            return
        self.broadcast_log(f"通道: {'TG ' if send_tg else ''}{'DC' if send_dc else ''}".strip())
        
        # 在新執行緒中發送（避免阻塞 UI）
        thread = threading.Thread(target=self.send_message_thread, 
                                 args=(bot, target_chat_id, target_topic_id, message_text, send_tg, send_dc),
                                 daemon=True)
        thread.start()
    
    def send_message_thread(self, bot, target_chat_id, target_topic_id, message_text, send_tg=True, send_dc=True):
        """在新執行緒中發送訊息"""
        loop = None
        client_to_use = None
        need_disconnect = False
        tg_ok = False
        dc_ok = False
        dc_msg = ""
        translated_message_text = message_text
        
        try:
            # 如果客戶端已連接（機器人正在運行），使用現有的客戶端
            if bot.client and bot.client.is_connected():
                client_to_use = bot.client
                loop = bot.loop
                # 使用現有 loop 的 run_coroutine_threadsafe
                if loop:
                    try:
                        tf = asyncio.run_coroutine_threadsafe(
                            self.translate_text_to_zh_tw(message_text),
                            loop
                        )
                        translated_message_text = tf.result(timeout=20)
                    except Exception:
                        translated_message_text = message_text

                    # Telegram 廣播
                    if send_tg:
                        try:
                            future = asyncio.run_coroutine_threadsafe(
                                self._send_message_async(client_to_use, target_chat_id, target_topic_id, translated_message_text),
                                loop
                            )
                            future.result(timeout=30)  # 等待最多30秒
                            tg_ok = True
                        except Exception as e:
                            self.root.after(0, lambda err=str(e): self.broadcast_log(f"❌ Telegram 發送失敗: {err}"))

                    # Discord 廣播（有設定才送）
                    dc_token = str(bot.config.get('discord_bot_token', '')).strip()
                    dc_channel_id = str(bot.config.get('discord_channel_id', '')).strip()
                    if send_dc and dc_token and dc_channel_id:
                        try:
                            dc_future = asyncio.run_coroutine_threadsafe(
                                self.send_to_discord(bot, translated_message_text),
                                loop
                            )
                            dc_ok, dc_msg = dc_future.result(timeout=30)
                        except Exception as e:
                            dc_ok = False
                            dc_msg = str(e)
                else:
                    raise Exception("無法取得事件循環")
            else:
                # 客戶端未連接，創建臨時的客戶端和循環
                temp_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(temp_loop)
                loop = temp_loop
                need_disconnect = True
                
                temp_client = TelegramClient(bot.session_file, 
                                           bot.config['api_id'], 
                                           bot.config['api_hash'])
                client_to_use = temp_client
                
                # 連接並發送
                async def send_with_temp_client():
                    await temp_client.connect()
                    if not temp_client.is_user_authorized():
                        raise Exception("需要先登入此帳號")
                    await self._send_message_async(temp_client, target_chat_id, target_topic_id, translated_message_text)
                    await temp_client.disconnect()

                try:
                    translated_message_text = temp_loop.run_until_complete(self.translate_text_to_zh_tw(message_text))
                except Exception:
                    translated_message_text = message_text

                # Telegram 廣播
                if send_tg:
                    try:
                        temp_loop.run_until_complete(send_with_temp_client())
                        tg_ok = True
                    except Exception as e:
                        self.root.after(0, lambda err=str(e): self.broadcast_log(f"❌ Telegram 發送失敗: {err}"))

                # Discord 廣播（有設定才送）
                dc_token = str(bot.config.get('discord_bot_token', '')).strip()
                dc_channel_id = str(bot.config.get('discord_channel_id', '')).strip()
                if send_dc and dc_token and dc_channel_id:
                    try:
                        dc_ok, dc_msg = temp_loop.run_until_complete(self.send_to_discord(bot, translated_message_text))
                    except Exception as e:
                        dc_ok = False
                        dc_msg = str(e)

            if send_tg and tg_ok:
                self.root.after(0, lambda: self.broadcast_log("✅ Telegram 廣播成功！"))
            has_dc_config = bool(str(bot.config.get('discord_bot_token', '')).strip() and str(bot.config.get('discord_channel_id', '')).strip())
            if send_dc and has_dc_config:
                if dc_ok:
                    self.root.after(0, lambda: self.broadcast_log("✅ Discord 廣播成功！（含 @everyone）"))
                else:
                    self.root.after(0, lambda m=dc_msg: self.broadcast_log(f"❌ Discord 廣播失敗: {m}"))
            elif send_dc and not has_dc_config:
                self.root.after(0, lambda: self.broadcast_log("⚠️ Discord 廣播已勾選，但未設定 Token/Channel，已略過。"))

            if tg_ok or dc_ok:
                self.root.after(0, lambda: self.log(f"📢 廣播訊息已發送(TG:{'成功' if tg_ok else '失敗'} / DC:{'成功' if dc_ok else '未送或失敗'}): {translated_message_text[:30]}...", bot.name))
            else:
                self.root.after(0, lambda: self.log("❌ 廣播訊息兩邊都未成功", bot.name))
            
        except Exception as e:
            error_msg = str(e)
            self.root.after(0, lambda: self.broadcast_log(f"❌ 發送失敗: {error_msg}"))
            self.root.after(0, lambda: self.log(f"❌ 廣播訊息發送失敗: {error_msg}", bot.name))
        finally:
            if need_disconnect and client_to_use and client_to_use.is_connected():
                try:
                    loop.run_until_complete(client_to_use.disconnect())
                except:
                    pass
    
    async def _send_message_async(self, client, target_chat_id, target_topic_id, message_text):
        """異步發送訊息的輔助函數"""
        send_kwargs = {}
        if target_topic_id:
            send_kwargs['reply_to'] = target_topic_id
        
        await client.send_message(target_chat_id, message_text, **send_kwargs)

    def translate_text_to_zh_tw_sync(self, text):
        """免費翻譯：轉成繁體中文；失敗回傳原文。"""
        if not text or not str(text).strip():
            return text
        try:
            query = urllib.parse.quote(str(text))
            url = (
                "https://translate.googleapis.com/translate_a/single"
                f"?client=gtx&sl=auto&tl=zh-TW&dt=t&q={query}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            chunks = []
            if isinstance(data, list) and data and isinstance(data[0], list):
                for part in data[0]:
                    if isinstance(part, list) and part:
                        chunks.append(str(part[0]))
            translated = "".join(chunks).strip()
            return translated if translated else text
        except Exception:
            return text

    async def translate_text_to_zh_tw(self, text):
        return await asyncio.to_thread(self.translate_text_to_zh_tw_sync, text)

    async def send_to_discord(self, bot, text, media=None):
        """發送訊息到 Discord 頻道（有設定才送）"""
        token = str(bot.config.get('discord_bot_token', '')).strip()
        channel_id = str(bot.config.get('discord_channel_id', '')).strip()
        if not token or not channel_id:
            return False, "未設定 Discord Token 或 Channel ID"

        content = (text or "").strip() or " "
        sender_name = str(bot.name or "Bot").strip()
        content = f"{sender_name}: @everyone {content}".strip()
        if media:
            content = f"{content}\n\n[附註] 原訊息包含媒體，當前版本僅轉發文字。"

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "TelegramForwardBot/1.0"
        }
        payload = {"content": content}

        def _post():
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.getcode()

        try:
            status = await asyncio.to_thread(_post)
            return (True, "Discord 發送成功") if 200 <= status < 300 else (False, f"Discord HTTP 狀態碼: {status}")
        except Exception as e:
            return False, str(e)
    
    def broadcast_log(self, message):
        """在廣播狀態區域顯示訊息"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}\n"
        
        self.broadcast_status_text.config(state=tk.NORMAL)
        self.broadcast_status_text.insert(tk.END, log_message)
        self.broadcast_status_text.see(tk.END)
        self.broadcast_status_text.config(state=tk.DISABLED)
        self.root.update()
    
    def create_log_tab(self, parent):
        # 日誌區域
        log_frame = ttk.Frame(parent)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 日誌顯示
        self.log_text = scrolledtext.ScrolledText(log_frame, height=35, width=100,
                                                  font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 日誌控制
        log_control = ttk.Frame(log_frame)
        log_control.pack(fill=tk.X, pady=5)
        
        ttk.Button(log_control, text="清空日誌", 
                  command=lambda: self.log_text.delete("1.0", tk.END)).pack(side=tk.LEFT, padx=5)
    
    def log(self, message, instance_name=""):
        """在日誌區域顯示訊息"""
        # 檢查 log_text 是否存在（可能在介面建立前被調用）
        if not hasattr(self, 'log_text'):
            return
        
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = f"[{instance_name}] " if instance_name else ""
        log_message = f"[{timestamp}] {prefix}{message}\n"
        
        self.log_text.insert(tk.END, log_message)
        self.log_text.see(tk.END)
        self.root.update()
    
    def load_config(self):
        """載入已儲存的配置"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    configs = json.load(f)
                    for cfg in configs:
                        instance_id = cfg.get('instance_id', self.instance_counter + 1)
                        self.instance_counter = max(self.instance_counter, instance_id)
                        bot = BotInstance(cfg)
                        self.bot_instances[instance_id] = bot
                        self.log(f"已載入配置: {bot.name}")
            except Exception as e:
                self.log(f"載入配置失敗: {e}", "ERROR")
    
    def save_config(self):
        """儲存配置到檔案"""
        try:
            configs = [bot.to_dict() for bot in self.bot_instances.values()]
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(configs, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("錯誤", f"儲存配置失敗: {e}")
    
    def refresh_instance_list(self):
        """刷新機器人列表顯示"""
        # 清空列表
        for item in self.instance_tree.get_children():
            self.instance_tree.delete(item)
        
        # 重新載入
        for instance_id, bot in self.bot_instances.items():
            status = "運行中 ✅" if bot.is_running else "已停止"
            source_count = len(bot.config.get('source_chat_ids', []))
            target = str(bot.config.get('target_chat_id', ''))
            if bot.config.get('target_topic_id'):
                target += f" (話題:{bot.config['target_topic_id']})"
            
            self.instance_tree.insert('', tk.END, 
                                     text=str(instance_id),
                                     values=(bot.name, status, source_count, target),
                                     tags=(instance_id,))
        
        # 更新廣播機器人列表
        self.update_broadcast_bot_list()
    
    def on_instance_select(self, event):
        """當選擇機器人時"""
        selection = self.instance_tree.selection()
        if not selection:
            self.start_button.config(state=tk.DISABLED)
            self.stop_button.config(state=tk.DISABLED)
            self.selected_info_text.config(state=tk.NORMAL)
            self.selected_info_text.delete("1.0", tk.END)
            self.selected_info_text.config(state=tk.DISABLED)
            return
        
        item = selection[0]
        instance_id = int(self.instance_tree.item(item)['text'])
        bot = self.bot_instances.get(instance_id)
        
        if bot:
            # 顯示資訊
            info = f"""機器人名稱: {bot.name}
Session 檔案: {bot.session_file}
運行狀態: {"運行中 ✅" if bot.is_running else "已停止"}

API 設定:
  API ID: {bot.config.get('api_id', '')}
  API Hash: {bot.config.get('api_hash', '')[:20]}...

來源頻道 ({len(bot.config.get('source_chat_ids', []))} 個):
"""
            for sid in bot.config.get('source_chat_ids', [])[:5]:
                info += f"  • {sid}\n"
            if len(bot.config.get('source_chat_ids', [])) > 5:
                info += f"  ... 還有 {len(bot.config.get('source_chat_ids', [])) - 5} 個\n"
            source_topics = bot.config.get('source_topic_ids', []) or []
            if source_topics:
                info += f"\n來源話題 ID ({len(source_topics)} 個):\n"
                for tid in source_topics[:5]:
                    info += f"  • {tid}\n"
                if len(source_topics) > 5:
                    info += f"  ... 還有 {len(source_topics) - 5} 個\n"
            else:
                info += f"\n來源話題 ID: (全部話題)\n"
            
            info += f"\n目標設定:\n"
            info += f"  群組 ID: {bot.config.get('target_chat_id', '')}\n"
            if bot.config.get('target_topic_id'):
                info += f"  話題 ID: {bot.config.get('target_topic_id')}\n"
            
            info += f"\n過濾設定:\n"
            info += f"  過濾圖片: {'是' if bot.config.get('filter_media', True) else '否'}\n"
            info += f"  過濾連結: {'是' if bot.config.get('filter_url', True) else '否'}\n"
            info += f"\nDiscord 設定:\n"
            info += f"  Channel ID: {bot.config.get('discord_channel_id', '') or '(未設定)'}\n"
            info += f"  Bot Token: {'已設定' if str(bot.config.get('discord_bot_token', '')).strip() else '未設定'}\n"
            
            self.selected_info_text.config(state=tk.NORMAL)
            self.selected_info_text.delete("1.0", tk.END)
            self.selected_info_text.insert("1.0", info)
            self.selected_info_text.config(state=tk.DISABLED)
            
            # 更新按鈕狀態
            self.start_button.config(state=tk.NORMAL if not bot.is_running else tk.DISABLED)
            self.stop_button.config(state=tk.NORMAL if bot.is_running else tk.DISABLED)
    
    def show_add_dialog(self):
        """顯示新增機器人對話框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("新增機器人")
        dialog.geometry("600x700")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # 表單變數
        name_var = tk.StringVar(value=f"機器人{self.instance_counter + 1}")
        # 預設使用用戶提供的 API（可以修改）
        api_id_var = tk.StringVar(value="34609376")
        api_hash_var = tk.StringVar(value="7cecfae75441ef7bb34bc471edc23e6c")
        source_ids_var = tk.StringVar()
        target_chat_id_var = tk.StringVar(value="-1003611242392")
        target_topic_id_var = tk.StringVar(value="9")
        filter_media_var = tk.BooleanVar(value=True)
        filter_url_var = tk.BooleanVar(value=True)
        discord_channel_id_var = tk.StringVar()
        discord_bot_token_var = tk.StringVar()
        
        # 表單內容
        ttk.Label(dialog, text="機器人名稱:").pack(pady=5)
        ttk.Entry(dialog, textvariable=name_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="API ID:").pack(pady=5)
        ttk.Entry(dialog, textvariable=api_id_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="API Hash:").pack(pady=5)
        ttk.Entry(dialog, textvariable=api_hash_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="來源頻道 ID (一行一個):").pack(pady=5)
        source_text = scrolledtext.ScrolledText(dialog, height=6, width=50)
        source_text.pack(pady=5)
        ttk.Label(dialog, text="來源話題 ID (可選，一行一個；留空=全部話題):").pack(pady=5)
        source_topic_text = scrolledtext.ScrolledText(dialog, height=4, width=50)
        source_topic_text.pack(pady=5)
        
        ttk.Label(dialog, text="目標群組 ID:").pack(pady=5)
        ttk.Entry(dialog, textvariable=target_chat_id_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="話題 ID (可選，留空則不使用):").pack(pady=5)
        ttk.Entry(dialog, textvariable=target_topic_id_var, width=50).pack(pady=5)
        
        ttk.Checkbutton(dialog, text="過濾圖片/媒體", variable=filter_media_var).pack(pady=5)
        ttk.Checkbutton(dialog, text="過濾連結", variable=filter_url_var).pack(pady=5)
        ttk.Label(dialog, text="Discord Channel ID (可選):").pack(pady=5)
        ttk.Entry(dialog, textvariable=discord_channel_id_var, width=50).pack(pady=5)
        ttk.Label(dialog, text="Discord Bot Token (可選):").pack(pady=5)
        ttk.Entry(dialog, textvariable=discord_bot_token_var, width=50, show="*").pack(pady=5)
        
        def save_new_bot():
            try:
                api_id = int(api_id_var.get())
                api_hash = api_hash_var.get()
                
                source_ids_text = source_text.get("1.0", tk.END).strip()
                source_ids = []
                for line in source_ids_text.split('\n'):
                    line = line.strip()
                    if line and line.startswith('-'):
                        try:
                            source_ids.append(int(line))
                        except ValueError:
                            pass
                
                if not source_ids:
                    messagebox.showerror("錯誤", "請至少輸入一個來源頻道 ID")
                    return

                source_topic_ids_text = source_topic_text.get("1.0", tk.END).strip()
                source_topic_ids = []
                for line in source_topic_ids_text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        source_topic_ids.append(int(line))
                    except ValueError:
                        pass
                
                target_chat_id = int(target_chat_id_var.get())
                topic_id_str = target_topic_id_var.get().strip()
                target_topic_id = int(topic_id_str) if topic_id_str else None
                
                # 建立新實例
                self.instance_counter += 1
                config = {
                    'instance_id': self.instance_counter,
                    'name': name_var.get(),
                    'api_id': api_id,
                    'api_hash': api_hash,
                    'source_chat_ids': source_ids,
                    'source_topic_ids': source_topic_ids,
                    'target_chat_id': target_chat_id,
                    'target_topic_id': target_topic_id,
                    'session_file': f"mysession_{self.instance_counter}",
                    'filter_media': filter_media_var.get(),
                    'filter_url': filter_url_var.get(),
                    'discord_channel_id': discord_channel_id_var.get().strip(),
                    'discord_bot_token': discord_bot_token_var.get().strip(),
                }
                
                bot = BotInstance(config)
                self.bot_instances[self.instance_counter] = bot
                self.save_config()
                self.refresh_instance_list()
                self.log(f"已新增機器人: {bot.name}")
                dialog.destroy()
                
            except ValueError as e:
                messagebox.showerror("錯誤", f"請檢查輸入的數字格式: {e}")
        
        ttk.Button(dialog, text="確定", command=save_new_bot).pack(pady=10)
        ttk.Button(dialog, text="取消", command=dialog.destroy).pack()
    
    def edit_selected(self):
        """編輯選中的機器人"""
        selection = self.instance_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "請先選擇要編輯的機器人")
            return
        
        item = selection[0]
        instance_id = int(self.instance_tree.item(item)['text'])
        bot = self.bot_instances.get(instance_id)
        
        if not bot:
            messagebox.showerror("錯誤", "找不到選中的機器人")
            return
        
        if bot.is_running:
            messagebox.showwarning("警告", "請先停止機器人再進行編輯")
            return
        
        # 顯示編輯對話框（類似新增對話框，但載入現有值）
        dialog = tk.Toplevel(self.root)
        dialog.title(f"編輯機器人 - {bot.name}")
        dialog.geometry("600x700")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # 表單變數（載入現有值）
        name_var = tk.StringVar(value=bot.name)
        api_id_var = tk.StringVar(value=str(bot.config.get('api_id', '')))
        api_hash_var = tk.StringVar(value=str(bot.config.get('api_hash', '')))
        target_chat_id_var = tk.StringVar(value=str(bot.config.get('target_chat_id', '')))
        topic_id = bot.config.get('target_topic_id')
        target_topic_id_var = tk.StringVar(value=str(topic_id) if topic_id else '')
        filter_media_var = tk.BooleanVar(value=bot.config.get('filter_media', True))
        filter_url_var = tk.BooleanVar(value=bot.config.get('filter_url', True))
        discord_channel_id_var = tk.StringVar(value=str(bot.config.get('discord_channel_id', '')))
        discord_bot_token_var = tk.StringVar(value=str(bot.config.get('discord_bot_token', '')))
        
        # 表單內容
        ttk.Label(dialog, text="機器人名稱:").pack(pady=5)
        ttk.Entry(dialog, textvariable=name_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="API ID:").pack(pady=5)
        ttk.Entry(dialog, textvariable=api_id_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="API Hash:").pack(pady=5)
        ttk.Entry(dialog, textvariable=api_hash_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="來源頻道 ID (一行一個):").pack(pady=5)
        source_text = scrolledtext.ScrolledText(dialog, height=6, width=50)
        source_text.pack(pady=5)
        # 載入現有的來源頻道
        source_ids = bot.config.get('source_chat_ids', [])
        source_text.insert("1.0", '\n'.join(str(sid) for sid in source_ids))
        ttk.Label(dialog, text="來源話題 ID (可選，一行一個；留空=全部話題):").pack(pady=5)
        source_topic_text = scrolledtext.ScrolledText(dialog, height=4, width=50)
        source_topic_text.pack(pady=5)
        source_topic_ids = bot.config.get('source_topic_ids', [])
        source_topic_text.insert("1.0", '\n'.join(str(tid) for tid in source_topic_ids))
        
        ttk.Label(dialog, text="目標群組 ID:").pack(pady=5)
        ttk.Entry(dialog, textvariable=target_chat_id_var, width=50).pack(pady=5)
        
        ttk.Label(dialog, text="話題 ID (可選，留空則不使用):").pack(pady=5)
        ttk.Entry(dialog, textvariable=target_topic_id_var, width=50).pack(pady=5)
        
        ttk.Checkbutton(dialog, text="過濾圖片/媒體", variable=filter_media_var).pack(pady=5)
        ttk.Checkbutton(dialog, text="過濾連結", variable=filter_url_var).pack(pady=5)
        ttk.Label(dialog, text="Discord Channel ID (可選):").pack(pady=5)
        ttk.Entry(dialog, textvariable=discord_channel_id_var, width=50).pack(pady=5)
        ttk.Label(dialog, text="Discord Bot Token (可選):").pack(pady=5)
        ttk.Entry(dialog, textvariable=discord_bot_token_var, width=50, show="*").pack(pady=5)
        
        def save_edited_bot():
            try:
                api_id = int(api_id_var.get())
                api_hash = api_hash_var.get().strip()
                
                source_ids_text = source_text.get("1.0", tk.END).strip()
                source_ids = []
                for line in source_ids_text.split('\n'):
                    line = line.strip()
                    if line and line.startswith('-'):
                        try:
                            source_ids.append(int(line))
                        except ValueError:
                            pass
                
                if not source_ids:
                    messagebox.showerror("錯誤", "請至少輸入一個來源頻道 ID")
                    return

                source_topic_ids_text = source_topic_text.get("1.0", tk.END).strip()
                source_topic_ids = []
                for line in source_topic_ids_text.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        source_topic_ids.append(int(line))
                    except ValueError:
                        pass
                
                target_chat_id = int(target_chat_id_var.get())
                topic_id_str = target_topic_id_var.get().strip()
                target_topic_id = int(topic_id_str) if topic_id_str else None
                
                # 更新配置（保留 instance_id 和 session_file）
                bot.name = name_var.get()
                bot.config.update({
                    'api_id': api_id,
                    'api_hash': api_hash,
                    'source_chat_ids': source_ids,
                    'source_topic_ids': source_topic_ids,
                    'target_chat_id': target_chat_id,
                    'target_topic_id': target_topic_id,
                    'filter_media': filter_media_var.get(),
                    'filter_url': filter_url_var.get(),
                    'discord_channel_id': discord_channel_id_var.get().strip(),
                    'discord_bot_token': discord_bot_token_var.get().strip(),
                })
                
                self.save_config()
                self.refresh_instance_list()
                self.log(f"已更新機器人: {bot.name}")
                dialog.destroy()
                
            except ValueError as e:
                messagebox.showerror("錯誤", f"請檢查輸入的數字格式: {e}")
        
        ttk.Button(dialog, text="確定", command=save_edited_bot).pack(pady=10)
        ttk.Button(dialog, text="取消", command=dialog.destroy).pack()
    
    def delete_selected(self):
        """刪除選中的機器人"""
        selection = self.instance_tree.selection()
        if not selection:
            messagebox.showwarning("警告", "請先選擇要刪除的機器人")
            return
        
        item = selection[0]
        instance_id = int(self.instance_tree.item(item)['text'])
        bot = self.bot_instances.get(instance_id)
        
        if bot and bot.is_running:
            messagebox.showwarning("警告", "請先停止機器人再進行刪除")
            return
        
        if messagebox.askyesno("確認", f"確定要刪除機器人「{bot.name}」嗎？"):
            del self.bot_instances[instance_id]
            self.save_config()
            self.refresh_instance_list()
            self.log(f"已刪除機器人: {bot.name}")
    
    def get_selected_bot(self):
        """取得選中的機器人"""
        selection = self.instance_tree.selection()
        if not selection:
            return None
        item = selection[0]
        instance_id = int(self.instance_tree.item(item)['text'])
        return self.bot_instances.get(instance_id)
    
    def start_selected(self):
        """啟動選中的機器人"""
        bot = self.get_selected_bot()
        if not bot:
            return
        
        if bot.is_running:
            messagebox.showwarning("警告", "機器人已在運行中")
            return
        
        self.start_bot_instance(bot)
    
    def stop_selected(self):
        """停止選中的機器人"""
        bot = self.get_selected_bot()
        if not bot:
            return
        
        if not bot.is_running:
            return
        
        self.stop_bot_instance(bot)
    
    def start_all(self):
        """啟動所有機器人"""
        for bot in self.bot_instances.values():
            if not bot.is_running:
                self.start_bot_instance(bot)
    
    def stop_all(self):
        """停止所有機器人"""
        for bot in self.bot_instances.values():
            if bot.is_running:
                self.stop_bot_instance(bot)
    
    def start_bot_instance(self, bot):
        """啟動單個機器人實例"""
        if bot.is_running:
            return
        
        self.log(f"正在啟動: {bot.name}", bot.name)
        bot.thread = threading.Thread(target=self.run_bot_thread, args=(bot,), daemon=True)
        bot.thread.start()
    
    def stop_bot_instance(self, bot):
        """停止單個機器人實例"""
        if not bot.is_running:
            return
        
        try:
            # 記錄停止請求
            self.log(f"正在停止: {bot.name}", bot.name)
            # 第三步：改為透過 stop_event 通知異步迴圈優雅停止
            bot.set_stop()
            # 狀態更新由 start_bot_async 的 finally 統一處理
        except Exception as e:
            # 即使發生錯誤，也要確保至少有日誌可看
            self.log(f"停止時發生錯誤: {e}", bot.name)
    
    def show_input_dialog_sync(self, title, prompt_text, password=False):
        """同步顯示輸入對話框（阻塞式，用於 code_callback）"""
        result_queue = queue.Queue()
        
        def show_dialog():
            dialog = tk.Toplevel(self.root)
            dialog.title(title)
            dialog.geometry("450x220")
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            
            # 置中顯示
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() // 2) - (450 // 2)
            y = (dialog.winfo_screenheight() // 2) - (220 // 2)
            dialog.geometry(f"450x220+{x}+{y}")
            
            tk.Label(dialog, text=prompt_text, 
                    font=("Microsoft JhengHei", 11), wraplength=400, justify=tk.LEFT).pack(pady=20)
            
            entry_var = tk.StringVar()
            show_char = "*" if password else ""
            entry = ttk.Entry(dialog, textvariable=entry_var, width=45, 
                             font=("Microsoft JhengHei", 10), show=show_char)
            entry.pack(pady=10)
            entry.focus()
            
            def confirm():
                result_queue.put(entry_var.get().strip())
                dialog.destroy()
            
            def cancel():
                result_queue.put(None)
                dialog.destroy()
            
            entry.bind('<Return>', lambda e: confirm())
            dialog.bind('<Escape>', lambda e: cancel())
            dialog.protocol("WM_DELETE_WINDOW", cancel)
            
            button_frame = ttk.Frame(dialog)
            button_frame.pack(pady=10)
            
            ttk.Button(button_frame, text="確定", command=confirm).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="取消", command=cancel).pack(side=tk.LEFT, padx=5)
        
        # 在主執行緒中執行對話框
        self.root.after(0, show_dialog)
        
        # 等待對話框結果（阻塞式輪詢，因為這是在同步函數中調用）
        while True:
            self.root.update()  # 更新 GUI，讓對話框可以顯示
            try:
                result = result_queue.get_nowait()
                return result
            except queue.Empty:
                import time
                time.sleep(0.1)  # 短暫等待
    
    async def show_input_dialog_async(self, title, prompt_text, password=False):
        """異步顯示輸入對話框"""
        result_queue = queue.Queue()
        
        def show_dialog():
            dialog = tk.Toplevel(self.root)
            dialog.title(title)
            dialog.geometry("450x200")
            dialog.transient(self.root)
            dialog.grab_set()
            dialog.resizable(False, False)
            
            # 置中顯示
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() // 2) - (450 // 2)
            y = (dialog.winfo_screenheight() // 2) - (200 // 2)
            dialog.geometry(f"450x200+{x}+{y}")
            
            tk.Label(dialog, text=prompt_text, 
                    font=("Microsoft JhengHei", 11), wraplength=400, justify=tk.LEFT).pack(pady=20)
            
            entry_var = tk.StringVar()
            show_char = "*" if password else ""
            entry = ttk.Entry(dialog, textvariable=entry_var, width=45, 
                             font=("Microsoft JhengHei", 10), show=show_char)
            entry.pack(pady=10)
            entry.focus()
            
            def confirm():
                result_queue.put(entry_var.get().strip())
                dialog.destroy()
            
            def cancel():
                result_queue.put(None)
                dialog.destroy()
            
            entry.bind('<Return>', lambda e: confirm())
            dialog.bind('<Escape>', lambda e: cancel())
            dialog.protocol("WM_DELETE_WINDOW", cancel)
            
            button_frame = ttk.Frame(dialog)
            button_frame.pack(pady=10)
            
            ttk.Button(button_frame, text="確定", command=confirm).pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="取消", command=cancel).pack(side=tk.LEFT, padx=5)
        
        # 在主執行緒中執行對話框
        self.root.after(0, show_dialog)
        
        # 等待對話框結果（使用異步輪詢）
        while True:
            try:
                result = result_queue.get_nowait()
                return result
            except queue.Empty:
                await asyncio.sleep(0.1)  # 等待對話框完成
    
    def run_bot_thread(self, bot):
        """在新執行緒中運行機器人"""
        bot.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot.loop)
        
        try:
            bot.loop.run_until_complete(self.start_bot_async(bot))
        except Exception as e:
            self.log(f"❌ 錯誤: {e}", bot.name)
            self.root.after(0, lambda: self.stop_bot_instance(bot))
    
    async def start_bot_async(self, bot):
        """異步啟動機器人（使用 asyncio.Event 控制生命週期）"""
        # 第一步：每次啟動時建立新的 stop_event，供 UI 觸發停止
        bot.stop_event = asyncio.Event()
        try:
            config = bot.config
            source_ids = config.get('source_chat_ids', [])
            source_topic_ids = config.get('source_topic_ids', []) or []
            source_topic_id_set = set()
            for _tid in source_topic_ids:
                try:
                    source_topic_id_set.add(int(_tid))
                except Exception:
                    pass
            target_chat_id = config.get('target_chat_id')
            target_topic_id = config.get('target_topic_id')
            
            self.root.after(0, lambda: self.log(f"📡 連接 Telegram API...", bot.name))
            
            # 驗證 API ID 和 API Hash
            try:
                api_id = int(config['api_id'])
                api_hash = str(config['api_hash']).strip()
                if not api_hash:
                    raise ValueError("API Hash 不能為空")
            except (ValueError, KeyError) as e:
                raise Exception(f"API 設定錯誤: {e}\n請檢查 API ID 和 API Hash 是否正確")
            
            # 檢查是否有其他機器人使用相同的 session 檔案（避免衝突）
            for other_id, other_bot in self.bot_instances.items():
                if other_id != bot.config.get('instance_id') and other_bot.is_running:
                    if other_bot.session_file == bot.session_file:
                        raise Exception(f"錯誤：機器人「{other_bot.name}」正在使用相同的 session 檔案\n每個機器人必須使用獨立的 session 檔案")
            
            # 建立客戶端（每個機器人使用獨立的 session 檔案）
            # Session 檔案格式：mysession_1, mysession_2 等，確保每個機器人獨立
            bot.client = TelegramClient(bot.session_file, api_id, api_hash)
            
            # 先連接客戶端
            try:
                if not bot.client.is_connected():
                    await bot.client.connect()
            except Exception as e:
                error_msg = str(e)
                if 'api_id' in error_msg.lower() or 'api_hash' in error_msg.lower() or 'invalid' in error_msg.lower():
                    # 提供更清楚的錯誤訊息
                    detailed_msg = (
                        f"API 驗證失敗: {error_msg}\n\n"
                        f"請檢查：\n"
                        f"1. API ID ({api_id}) 和 API Hash 是否正確\n"
                        f"2. 是否從 https://my.telegram.org/apps 取得正確的 API 金鑰\n"
                        f"3. 如果使用相同的 API ID/Hash 登入同一個帳號，每個機器人會使用獨立的 session 檔案\n"
                        f"4. 當前 Session 檔案: {bot.session_file}.session"
                    )
                    raise Exception(detailed_msg)
                raise
            
            # 檢查是否已授權（每個帳號都需要獨立授權）
            is_authorized = await bot.client.is_user_authorized()
            
            if not is_authorized:
                # 未授權，需要登入（每個機器人都是不同的帳號）
                self.root.after(0, lambda: self.log(f"需要登入 {bot.name}，請輸入電話號碼...", bot.name))
                
                # 取得電話號碼（使用對話框）
                phone = await self.show_input_dialog_async(
                    f"登入 {bot.name}",
                    f"請輸入 {bot.name} 的電話號碼\n(例如: +886912345678)\n\n注意：每個機器人使用不同的 Telegram 帳號"
                )
                
                if not phone:
                    raise Exception("取消登入")
                
                # 定義驗證碼回調函數（使用 GUI 對話框，同步函數）
                def code_callback():
                    self.root.after(0, lambda: self.log(f"請輸入 {bot.name} 的驗證碼...", bot.name))
                    return self.show_input_dialog_sync(
                        f"驗證碼 - {bot.name}",
                        f"請輸入 Telegram 傳送給 {bot.name} 的驗證碼\n(檢查您的手機簡訊或 Telegram App)"
                    )
                
                # 定義二階段驗證密碼回調函數（同步函數）
                def password_callback():
                    self.root.after(0, lambda: self.log(f"請輸入 {bot.name} 的二階段驗證密碼...", bot.name))
                    return self.show_input_dialog_sync(
                        f"二階段驗證 - {bot.name}",
                        f"請輸入 {bot.name} 的二階段驗證密碼\n(這是您在 Telegram 設定的帳號密碼)",
                        password=True
                    )
                
                # 使用 client.start() 自動處理登入流程
                # 這會自動發送驗證碼請求到指定的電話號碼
                await bot.client.start(
                    phone=phone,
                    code_callback=code_callback,
                    password=password_callback
                )
            else:
                # 已授權，直接啟動（使用已保存的 session）
                await bot.client.start()
            
            self.root.after(0, lambda: self.log(f"✅ 已連接到 Telegram", bot.name))
            self.root.after(0, lambda: self.log(f"監聽 {len(source_ids)} 個來源頻道", bot.name))
            if source_topic_ids:
                self.root.after(0, lambda: self.log(f"來源話題白名單: {len(source_topic_ids)} 個", bot.name))
            else:
                self.root.after(0, lambda: self.log("來源話題白名單: 未設定（接收全部話題）", bot.name))
            
            # 註冊訊息處理器
            @bot.client.on(events.NewMessage(chats=source_ids))
            async def handler(event):
                original_text = event.message.message or ""
                chat = await event.get_chat()
                chat_title = chat.title if chat.title else str(chat.id)
                msg = event.message

                # 來源話題白名單過濾：留空代表收全部話題
                if source_topic_id_set:
                    topic_id = getattr(msg, "reply_to_top_id", None)
                    if topic_id is None and getattr(msg, "reply_to", None):
                        topic_id = getattr(msg.reply_to, "reply_to_top_id", None)
                    if topic_id is None and getattr(msg, "reply_to", None):
                        topic_id = getattr(msg.reply_to, "reply_to_msg_id", None)

                    try:
                        tid = int(topic_id) if topic_id is not None else None
                    except Exception:
                        tid = None

                    if tid is None or tid not in source_topic_id_set:
                        self.root.after(0, lambda: self.log(f"🚫 [已過濾] {chat_title} - 非指定來源話題", bot.name))
                        return
                
                # 過濾媒體
                if config.get('filter_media', True) and event.message.media:
                    self.root.after(0, lambda: self.log(f"🚫 [已過濾] {chat_title} - 包含圖片/媒體", bot.name))
                    return
                
                # 過濾連結
                if config.get('filter_url', True):
                    has_url = False
                    if event.message.entities:
                        for entity in event.message.entities:
                            if isinstance(entity, (MessageEntityUrl, MessageEntityTextUrl)):
                                has_url = True
                                break
                    
                    if not has_url:
                        url_pattern = r'(https?://[^\s]+|www\.[^\s]+|[a-zA-Z0-9-]+\.[a-zA-Z]{2,}[^\s]*)'
                        if re.search(url_pattern, original_text, re.IGNORECASE):
                            has_url = True
                    
                    if has_url:
                        self.root.after(0, lambda: self.log(f"🚫 [已過濾] {chat_title} - 包含連結", bot.name))
                        return
                
                # 檢查是否有內容（文字或媒體）
                has_content = bool(original_text.strip()) or bool(event.message.media)
                if not has_content:
                    return
                
                self.root.after(0, lambda: self.log(f"📨 [新訊來自: {chat_title}]", bot.name))
                
                try:
                    translated_text = await self.translate_text_to_zh_tw(original_text or "")

                    # 1) 先送 Telegram（維持原本功能）
                    send_kwargs = {}
                    if target_topic_id:
                        send_kwargs['reply_to'] = target_topic_id

                    if event.message.media:
                        await bot.client.send_message(
                            target_chat_id,
                            translated_text,
                            file=event.message.media,
                            **send_kwargs
                        )
                    else:
                        await bot.client.send_message(target_chat_id, translated_text, **send_kwargs)

                    self.root.after(0, lambda: self.log("✅ Telegram 轉發成功！", bot.name))

                    # 2) 若有設定 Discord，再額外鏡像到 Discord
                    discord_token = str(config.get('discord_bot_token', '')).strip()
                    discord_channel_id = str(config.get('discord_channel_id', '')).strip()
                    if discord_token and discord_channel_id:
                        ok, msg = await self.send_to_discord(bot, translated_text, media=event.message.media)
                        if ok:
                            self.root.after(0, lambda: self.log("✅ Discord 轉發成功！", bot.name))
                        else:
                            self.root.after(0, lambda m=msg: self.log(f"❌ Discord 轉發失敗: {m}", bot.name))
                    
                except Exception as e:
                    self.root.after(0, lambda err=str(e): self.log(f"❌ 錯誤: {err}", bot.name))
            
            # 標記為運行中並更新 UI
            bot.is_running = True
            self.root.after(0, self.refresh_instance_list)
            self.root.after(0, lambda: self.log(f"🎉 雙向機器人已啟動，正在監聽訊息...", bot.name))

            # 第二步：在這裡懸掛，直到 stop_event 被觸發
            await bot.stop_event.wait()

        except Exception as e:
            # 第三步：任何錯誤都記錄下來，狀態留給 finally 統一處理
            self.root.after(0, lambda: self.log(f"❌ 嚴重錯誤: {e}", bot.name))
        finally:
            # 第四步：統一在這裡收尾（關閉連線、更新狀態與 UI）
            try:
                if bot.client and bot.client.is_connected():
                    await bot.client.disconnect()
            except Exception:
                pass
            bot.is_running = False
            self.root.after(0, self.refresh_instance_list)

def main():
    root = tk.Tk()
    app = TelegramMultiBotGUI(root)
    root.mainloop()

if __name__ == '__main__':
    main()


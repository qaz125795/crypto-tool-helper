def fetch_position_change():
    """主流程：持倉變化篩選（原本的邏輯，只是改成只偵測 BingX 的 554 個交易對）"""
    global _coinglass_oi_first_failure_logged
    _coinglass_oi_first_failure_logged = False  # 本輪只記錄第一次 OI 失敗，方便診斷
    logger.info("開始執行持倉變化篩選，只偵測 BingX 合約幣種...")

    # 步驟1：先抓 BingX 有支援的合約交易對；失敗則用 CoinGlass 名單
    allowed_bases, base_to_symbol, bases_for_price = fetch_bingx_contracts()
    if allowed_bases:
        bingx_symbols_upper = allowed_bases
        logger.info(f"從 BingX contracts 取得 {len(allowed_bases)} 個合約交易對")
    else:
        bingx_symbols = fetch_supported_futures_coins()
        if not bingx_symbols:
            send_telegram_message("⚠️ 無法取得合約幣種名單，請稍後再試。", TG_THREAD_IDS['position_change'])
            return
        bingx_symbols_upper = {s.upper() for s in bingx_symbols}
        base_to_symbol = {}
        bases_for_price = []
        logger.info(f"獲取到 {len(bingx_symbols)} 個 BingX 合約幣種（CoinGlass 名單）")

    # 步驟2：依「BingX 交易對」取得 30m 價格（有 contracts 則直接用 BingX 取價，不再依賴 CoinGlass 漲跌幅）
    if bases_for_price:
        all_symbols_data = _fetch_coins_price_change_fallback(bases_for_price)
        logger.info(f"依 BingX 交易對取得 {len(all_symbols_data)} 個幣種的 30m 價格數據")
    else:
        all_symbols_data = fetch_coins_price_change()
        logger.info(f"從 Coinglass API 取得 {len(all_symbols_data)} 個幣種的價格數據")
    if not all_symbols_data:
        send_telegram_message("⚠️ 無法取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
        return

    # 步驟3：只保留 BingX 名單中的幣種
    target_symbols_data = []
    for coin in all_symbols_data:
        symbol = normalize_symbol(coin)
        if symbol and symbol.upper() in bingx_symbols_upper:
            target_symbols_data.append(coin)
    
    logger.info(f"過濾後剩餘 {len(target_symbols_data)} 個合約幣種")

    # 24h 漲跌幅：先從 CoinGlass 現成資料取得，抓不到再用 BingX 計算
    coinglass_24h_map = {}
    for coin in all_symbols_data:
        pct = extract_price_change_24h(coin)
        if pct is not None:
            s = normalize_symbol(coin) or ""
            clean = s.replace("USDT", "").replace("-", "").replace("_", "").upper()
            if clean:
                coinglass_24h_map[clean] = pct
    if not coinglass_24h_map:
        coinglass_24h_map = _fetch_coinglass_24h_map()
    
    # 【智慧過濾 Smart Filter - 30m 版】山寨為主，主流幣用 OI_MAIN_COIN_MIN 排除
    PRICE_GATEKEEPER = 1.0  # 30m 價格波動門檻 %（>=1% 即進入 OI 檢查）
    active_symbols = []
    for coin in target_symbols_data:
        p_change = extract_price_change_30m(coin)
        if abs(p_change) >= PRICE_GATEKEEPER:
            active_symbols.append(coin)
    logger.info(
        f"🔍 智慧過濾: 從 {len(target_symbols_data)} 個幣種中篩選出 {len(active_symbols)} 個活躍標的 "
        f"(價格 30m >= {PRICE_GATEKEEPER}%) 進行 30m OI 檢查..."
    )
    # 成交量預篩：3M 以下完全不列入，只對剩餘標的跑 OI（省時且避免低流動性雜訊）
    VOLUME_PREFILTER_MIN_USD = 3_000_000
    active_above_volume = []
    vol_check_no_snap = 0
    vol_check_no_vol = 0
    vol_check_below = 0
    for coin in active_symbols:
        sym = normalize_symbol(coin) or ""
        if not sym:
            continue
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        preferred = base_to_symbol.get(clean_base) if base_to_symbol else None
        if not preferred:
            preferred = base_to_symbol.get(sym.upper()) if base_to_symbol else None
        time.sleep(0.06)
        snap = _fetch_bingx_ticker_snapshot(sym, preferred_symbol=preferred)
        if not snap:
            vol_check_no_snap += 1
            continue
        vol = snap.get("volume_usd")
        if vol is None:
            vol_check_no_vol += 1
            continue
        if vol < VOLUME_PREFILTER_MIN_USD:
            vol_check_below += 1
            continue
        active_above_volume.append(coin)
    logger.info(
        f"📊 成交量預篩: 門檻 24h 成交額 ≥ {VOLUME_PREFILTER_MIN_USD/1e6:.0f}M USD，"
        f"通過 {len(active_above_volume)} 個、刷掉 {vol_check_no_snap + vol_check_no_vol + vol_check_below} 個 (無ticker:{vol_check_no_snap} 無成交額:{vol_check_no_vol} <3M:{vol_check_below})，"
        f"剩餘 {len(active_above_volume)} 個進入 OI 檢查"
    )
    # 為在 16 分鐘內完成，OI 階段僅處理前 320 個（約 8 分鐘內跑完）
    MAX_OI_SYMBOLS = 320
    target_symbols = active_above_volume[:MAX_OI_SYMBOLS]
    if len(active_above_volume) > MAX_OI_SYMBOLS:
        logger.info(f"成交量過篩後共 {len(active_above_volume)} 個，本輪僅處理前 {MAX_OI_SYMBOLS} 個以確保準時推播")
    
    long_open = []
    long_close = []
    short_open = []
    short_close = []
    
    processed_count = 0
    oi_success_count = 0
    oi_fail_count = 0
    
    # 並行處理配置：CoinGlass 專用慢速模式，避免瞬間請求過多
    MAX_WORKERS = 4
    start_time = time.time()
    MAX_EXECUTION_TIME = 16 * 60  # 強制結束上限 16 分鐘（雙重保護用）
    
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    broke_early = False
    try:
        future_to_coin = {executor.submit(process_single_symbol, coin): coin for coin in target_symbols}
        completed = 0
        for future in as_completed(future_to_coin):
            elapsed_time = time.time() - start_time
            if elapsed_time > MAX_EXECUTION_TIME:
                logger.warning(f"已達 {MAX_EXECUTION_TIME/60:.0f} 分鐘上限，提前結束並推播（已處理 {processed_count} 個）")
                for f in future_to_coin:
                    f.cancel()
                broke_early = True
                break
            
            completed += 1
            result = future.result()
            if result is None:
                continue
            processed_count += 1
            if completed % 100 == 0:
                logger.info(f"處理進度: {completed}/{len(target_symbols)} | 已用時: {elapsed_time/60:.1f} 分鐘")
            status = result.get('status')
            if status == 'oi_failed':
                oi_fail_count += 1
            elif status == 'success':
                oi_success_count += 1
                category = result.get('category')
                symbol = result.get('symbol')
                price_change = result.get('priceChange30m')
                oi_change = result.get('oiChange30m')
                price_change_24h = result.get('priceChange24h')
                item = {'symbol': symbol, 'priceChange30m': price_change, 'oiChange30m': oi_change, 'priceChange24h': price_change_24h}
                base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
                oi_min = OI_MAIN_COIN_MIN if base in MAIN_COINS else OI_ALTCOIN_MIN
                if abs(oi_change) >= oi_min:
                    if category == 'long_open':
                        long_open.append(item)
                    elif category == 'long_close':
                        long_close.append(item)
                    elif category == 'short_open':
                        short_open.append(item)
                    elif category == 'short_close':
                        short_close.append(item)
    finally:
        executor.shutdown(wait=not broke_early)  # 提前結束時不等待未完成任務，以利準時推播
    
    total_time = time.time() - start_time
    logger.info(f"處理統計: 總共 {processed_count} 個幣種, OI 成功 {oi_success_count} 個, OI 失敗 {oi_fail_count} 個 | 總用時: {total_time/60:.1f} 分鐘")
    logger.info(f"分類結果: 多方開倉 {len(long_open)}, 多方平倉 {len(long_close)}, 空方開倉 {len(short_open)}, 空方平倉 {len(short_close)}")
    
    # 只統計與計算 4 星以上：|OI| < OI_FOR_4_STAR 的不進 top、不跑後續運算
    long_open = [x for x in long_open if abs(x.get('oiChange30m') or 0) >= OI_FOR_4_STAR]
    long_close = [x for x in long_close if abs(x.get('oiChange30m') or 0) >= OI_FOR_4_STAR]
    short_open = [x for x in short_open if abs(x.get('oiChange30m') or 0) >= OI_FOR_4_STAR]
    short_close = [x for x in short_close if abs(x.get('oiChange30m') or 0) >= OI_FOR_4_STAR]
    long_open.sort(key=lambda x: x['oiChange30m'], reverse=True)
    long_close.sort(key=lambda x: x['oiChange30m'])
    short_open.sort(key=lambda x: x['oiChange30m'], reverse=True)
    short_close.sort(key=lambda x: x['oiChange30m'])
    top_long_open = long_open[:3]
    top_long_close = long_close[:3]
    top_short_open = short_open[:3]
    top_short_close = short_close[:3]

    # 對 top 標的取 RSI/布林帶（僅 4 星以上候選，3 星不計算）
    time.sleep(2)
    all_top = []
    for item, cat in [(x, "long_open") for x in top_long_open] + [(x, "long_close") for x in top_long_close] + [(x, "short_open") for x in top_short_open] + [(x, "short_close") for x in top_short_close]:
        sym = item.get("symbol", "")
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        preferred = base_to_symbol.get(clean_base) if base_to_symbol else None
        if not preferred:
            preferred = base_to_symbol.get(sym.upper()) if base_to_symbol else None
        time.sleep(0.2)
        tech = calculate_technicals(sym, bingx_symbol_override=preferred)
        funding_rate = _fetch_bingx_funding_rate(sym, preferred_symbol=preferred)
        price_24h = item.get("priceChange24h") if isinstance(item.get("priceChange24h"), (int, float)) else None
        if price_24h is None:
            price_24h = coinglass_24h_map.get(clean_base)
        if price_24h is None:
            price_24h = fetch_price_change_24h_coinglass_klines(sym, preferred)
        if price_24h is None:
            price_24h = fetch_price_change_24h_bingx(sym, preferred)
        cvd_change_1h = _cvd_change_last2(clean_base, "1h")
        time.sleep(0.25)
        whale_idx = _whale_index_latest(clean_base, "1d")
        time.sleep(0.2)
        # v3.0 散戶多空比（僅對 4/5 星候選額外調用）
        symbol_param = clean_base + "USDT"
        global_data = fetch_global_account_ratio(symbol_param, "1h")
        time.sleep(0.5)
        latest_point = get_latest_data_point(global_data) if global_data else None
        retail_ratio = latest_point.get("global_account_long_short_ratio") if isinstance(latest_point, dict) else None
        if retail_ratio is not None and isinstance(retail_ratio, (int, float)):
            logger.info(f"散戶多空比 {clean_base}: {retail_ratio}")
        signal_label, zone, stars, rsi_desc, reason = _classify_signal_and_tier(
            item, cat, tech, funding_rate,
            price_chg_24h=price_24h,
            cvd_change_1h=cvd_change_1h,
            whale_index=whale_idx,
            retail_ratio=retail_ratio,
        )
        if (stars or 0) < 4:
            continue  # 3 星不納入報表、不統計，省運算
        rsi_val = tech.get("rsi") if tech else None
        ub_val = tech.get("ub_value") if tech else None
        lb_val = tech.get("lb_value") if tech else None
        atr_val = tech.get("atr") if tech else None
        all_top.append({
            **item,
            "priceChange24h": price_24h,
            "category": cat,
            "current_price": tech.get("current_price") if tech else None,
            "rsi": rsi_val,
            "atr": atr_val,
            "whale_index": whale_idx,
            "signal_label": signal_label,
            "zone": zone,
            "stars": stars,
            "rsi_desc": rsi_desc,
            "reason": reason,
            "funding_rate": funding_rate,
        })
        logger.info(
            f"Top 入選 {sym}: 星{stars} 區={zone} RSI={rsi_val} 布林上={ub_val} 布林下={lb_val} ATR={atr_val} 鯨魚指數={whale_idx} | {reason}"
        )

    # 用 BingX ticker 一次取現價 + 24h 成交額；5 星僅允許 >7M，否則降為 4 星；<10M 標示成交量極低
    VOLUME_HARD_MIN_USD = 1_000_000    # <1M 直接排除
    VOLUME_SOFT_MIN_USD = 7_000_000   # <7M 標示「成交量極低 小心滑價」
    VOLUME_5STAR_MIN_USD = 7_000_000   # 5 星僅允許成交量大於 7M，≤7M 一律降為 4 星
    filtered_top = []
    for x in all_top:
        sym = x.get("symbol", "")
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        preferred = base_to_symbol.get(clean_base) if base_to_symbol else None
        if not preferred:
            preferred = base_to_symbol.get(sym.upper()) if base_to_symbol else None
        snap = _fetch_bingx_ticker_snapshot(sym, preferred_symbol=preferred)
        vol = None
        if snap:
            if snap.get("price") is not None:
                x["current_price"] = snap["price"]
            vol = snap.get("volume_usd")
            if vol is not None:
                if vol < VOLUME_HARD_MIN_USD:
                    continue
                x["low_liquidity_warning"] = vol < VOLUME_SOFT_MIN_USD
                x["volume_usd"] = float(vol)
                if (x.get("stars") or 0) == 5 and vol <= VOLUME_5STAR_MIN_USD:
                    x["stars"] = 4
            else:
                x["low_liquidity_warning"] = False
                x["volume_usd"] = 0
                if (x.get("stars") or 0) == 5:
                    x["stars"] = 4
        else:
            x["low_liquidity_warning"] = False
            x["volume_usd"] = 0
            if (x.get("stars") or 0) == 5:
                x["stars"] = 4
        filtered_top.append(x)
    all_top = filtered_top
    low_liq_count = sum(1 for x in all_top if x.get("low_liquidity_warning"))
    logger.info(
        f"成交量二次過濾: 門檻 1M USD（<1M 排除），剩餘 {len(all_top)} 筆進入推播；其中 {low_liq_count} 筆標示低流動性 (<7M)"
    )

    # 同幣同向：X 小時內不重複推（時間窗口冷卻）；方向反轉仍會推並標記「訊號反轉」
    COOLDOWN_HOURS = 4   # 同幣同向 4 小時內只推一次，避免短時間重複推同一檔
    HISTORY_HOURS = 24   # 冷卻歷史保留 24 小時（供 cooldown 與 direction_flip 使用）

    def _item_direction(x: Dict) -> str:
        sig = x.get("signal_label") or ""
        return "多" if ("做多" in sig or "追多" in sig or "嘎空" in sig or "抄底" in sig) else "空"

    SNIPER_COOLDOWN_FILE = DATA_DIR / "sniper_cooldown.json"
    now_ts = time.time()
    cooldown_sec = COOLDOWN_HOURS * 3600
    history_sec = HISTORY_HOURS * 3600
    history: List[Dict] = []
    try:
        if SNIPER_COOLDOWN_FILE.exists():
            raw = json.loads(SNIPER_COOLDOWN_FILE.read_text(encoding="utf-8"))
            history = raw.get("history") or []
            # 相容舊格式：只有 last_round 時轉成 history（ts 設為 1 小時前，讓本輪仍可能冷卻）
            if not history and raw.get("last_round"):
                last_round = raw.get("last_round") or []
                if last_round and isinstance(last_round[0], dict):
                    history = [{"symbol": str(p.get("symbol")), "dir": str(p.get("dir")), "ts": int(now_ts) - 3600} for p in last_round if p.get("symbol") and p.get("dir")]
                else:
                    history = [{"symbol": str(p[0]), "dir": str(p[1]), "ts": int(now_ts) - 3600} for p in last_round if isinstance(p, (list, tuple)) and len(p) >= 2]
            logger.info(f"冷卻檔已讀取: {SNIPER_COOLDOWN_FILE} | 歷史 {len(history)} 筆，{COOLDOWN_HOURS}h 內同幣同向不重推")
        else:
            logger.info(f"冷卻檔不存在，本輪無冷卻限制: {SNIPER_COOLDOWN_FILE}")
    except Exception as e:
        history = []
        logger.warning(f"讀取冷卻檔失敗，本輪無冷卻限制: {e}")
    # 冷卻集合：過去 COOLDOWN_HOURS 內推過的 (symbol, 方向) 本輪不重複報
    cooldown_set = set()
    for e in history:
        if isinstance(e, dict) and e.get("symbol") and e.get("dir"):
            if (now_ts - e.get("ts", 0)) <= cooldown_sec:
                cooldown_set.add((str(e["symbol"]), str(e["dir"])))
    # 上一輪方向（用於「多轉空/空轉多」提示）：取每幣最近一次推播的方向
    last_round_by_sym = {}
    for e in sorted(history, key=lambda x: x.get("ts", 0), reverse=True):
        if isinstance(e, dict) and e.get("symbol") and e.get("dir"):
            s = str(e["symbol"])
            if s not in last_round_by_sym:
                last_round_by_sym[s] = str(e["dir"])

    cooled_top = []
    for x in all_top:
        sym = x.get("symbol") or ""
        if not sym:
            continue
        cur_dir = _item_direction(x)
        key = (sym, cur_dir)
        if key in cooldown_set:
            logger.info(f"冷卻跳過: {sym} {cur_dir} (上輪已報)")
            continue
        # 上一輪有報過此幣但方向不同 → 標記多轉空/空轉多，報表會多一行提醒
        if sym in last_round_by_sym and last_round_by_sym[sym] != cur_dir:
            x["direction_flip"] = last_round_by_sym[sym] + "轉" + cur_dir
        else:
            x["direction_flip"] = None
        cooled_top.append(x)

    pairs_this_run = [(x.get("symbol"), _item_direction(x)) for x in cooled_top if x.get("symbol")]

    msg = build_report_message_tiered(cooled_top, processed_count, oi_success_count)
    send_telegram_message(msg, TG_THREAD_IDS['position_change'], parse_mode="Markdown")

    try:
        SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_entries = [{"symbol": s, "dir": d, "ts": int(now_ts)} for (s, d) in pairs_this_run]
        history = history + new_entries
        history = [e for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= history_sec]
        SNIPER_COOLDOWN_FILE.write_text(
            json.dumps({"history": history}, ensure_ascii=False),
            encoding="utf-8"
        )
        logger.info(f"冷卻檔已寫入: 本輪 {len(pairs_this_run)} 筆，歷史共 {len(history)} 筆 (保留 {HISTORY_HOURS}h) -> {SNIPER_COOLDOWN_FILE}")
    except Exception as e:
        logger.warning(f"寫入狙擊冷卻檔失敗: {e}")

    logger.info("持倉變化篩選執行完成並已推播")



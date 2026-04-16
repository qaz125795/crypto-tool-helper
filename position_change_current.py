def fetch_position_change():
    """
    【1H MTF 四層漏斗策略】中期波段持倉狙擊主流程。
    漏斗：1H OI/Price 大格局掃描 → 30m OI 確認延續性 → 15m OI 短期結構 → 5m OI 精準進場點。
    訊號：✅ 確定籌碼（四層共振）｜🎯 潛在機會（順勢回踩 / 逆勢摸頂底）。
    """
    global _coinglass_oi_first_failure_logged
    _coinglass_oi_first_failure_logged = False

    # 熔斷器狀態報告（每輪開始時印出，便於 GitHub Actions 日誌診斷）
    _cb_cnt = _circuit_breaker.get("consecutive_429", 0)
    if _cb_is_tripped():
        logger.warning(f"[熔斷器🚨] 本輪以 MAX_WORKERS=1 單執行緒模式啟動（連續429={_cb_cnt}）")
    elif _cb_is_warned():
        logger.warning(f"[熔斷器⚠️] 本輪以 MAX_WORKERS=2 警戒模式啟動（連續429={_cb_cnt}）")
    else:
        logger.info(f"[熔斷器✅] 正常模式（連續429={_cb_cnt}）")

    logger.info("🚀 山寨幣莊家狙擊鏡 啟動 | 純 CoinGlass 模式 | 1H MTF 四層漏斗掃描")

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 0：資料源初始化（純 CoinGlass 模式）
    # ════════════════════════════════════════════════════════
    logger.info("📊 [掃描漏斗] Step 0：純 CoinGlass 模式，所有數據（成交值/K線/OI）均來自 CoinGlass API")

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 1：CoinGlass 全市場數據（帶分頁，抓取 300~500 個幣種）
    # ════════════════════════════════════════════════════════
    all_symbols_data = fetch_coinglass_coins_markets()
    if not all_symbols_data:
        logger.warning("[漏斗] coins-markets 失敗，嘗試 coins-price-change 備援")
        all_symbols_data = fetch_coins_price_change()
        if all_symbols_data:
            logger.info(f"[備援✅] coins-price-change 取得 {len(all_symbols_data)} 個幣種")
    if not all_symbols_data:
        send_telegram_message("⚠️ 無法取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
        return
    logger.info(f"📊 [漏斗 1] CoinGlass 全網 {len(all_symbols_data)} 幣種")

    # ── 單次迴圈完成兩件事：BTC/ETH 大盤、24h快取 ──────────────────────────────
    global _btc_30m_pct, _btc_1h_pct, _btc_oi_1h_pct, _eth_30m_pct, _eth_1h_pct
    _btc_30m_pct = None
    _btc_1h_pct = None
    _btc_oi_1h_pct = None
    _eth_30m_pct = None
    _eth_1h_pct = None
    coinglass_24h_map: Dict[str, float] = {}
    active_symbols: List[Dict] = []
    for coin in all_symbols_data:
        sym_raw = normalize_symbol(coin) or ""
        clean_sym = sym_raw.replace("USDT", "").replace("-", "").replace("_", "").upper()

        # ① BTC 大盤環境
        if clean_sym == "BTC" and _btc_30m_pct is None:
            _btc_30m_pct = extract_price_change_30m(coin)
            _btc_1h_pct_raw = coin.get("price_change_percent_1h")
            try:
                _btc_1h_pct = float(_btc_1h_pct_raw) if _btc_1h_pct_raw is not None else None
            except (TypeError, ValueError):
                _btc_1h_pct = None
            _btc_oi_1h_pct = extract_oi_change_1h(coin)
            logger.info(
                f"📊 [大盤參考] BTC 價格30m {(_btc_30m_pct or 0):+.2f}%  1H {(_btc_1h_pct or 0):+.2f}%"
                f" | BTC OI 1H {(_btc_oi_1h_pct or 0):+.2f}%"
            )

        # ①-2 ETH 大盤環境（山寨幣主要參考）
        if clean_sym == "ETH" and _eth_30m_pct is None:
            _eth_30m_pct = extract_price_change_30m(coin)
            _eth_1h_pct_raw = coin.get("price_change_percent_1h")
            try:
                _eth_1h_pct = float(_eth_1h_pct_raw) if _eth_1h_pct_raw is not None else None
            except (TypeError, ValueError):
                _eth_1h_pct = None
            logger.info(f"📊 [大盤濾網] ETH 30m {(_eth_30m_pct or 0):+.2f}%  1H {(_eth_1h_pct or 0):+.2f}%")

        # ② 24h 漲跌幅快取
        pct24 = extract_price_change_24h(coin)
        if pct24 is not None and clean_sym:
            coinglass_24h_map[clean_sym] = pct24

        active_symbols.append(coin)

    # Gate 可交易白名單：僅保留 Gate USDT 永續存在的標的（降低用戶下單滑點/不可交易風險）
    gate_bases = fetch_gate_usdt_contract_bases()
    if gate_bases:
        _before_gate = len(active_symbols)
        active_symbols = [
            c for c in active_symbols
            if str((normalize_symbol(c) or "")).replace("USDT", "").replace("-", "").replace("_", "").upper() in gate_bases
        ]
        logger.info(
            f"[Gate白名單] 保留 {len(active_symbols)}/{_before_gate} 個可交易標的（Gate USDT 永續）"
        )

    if not coinglass_24h_map:
        coinglass_24h_map = _fetch_coinglass_24h_map()

    # ════════════════════════════════════════════════════════
    # Plan B：Gate 永續合約 24h USDT 成交值（備援，用於 CoinGlass 無資料的幣種）
    # 單一 API call，失敗時靜默回傳空 dict 不影響主流程
    # ════════════════════════════════════════════════════════
    _binance_vol_map: Dict[str, float] = fetch_bingx_futures_24h_vol()

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 4：成交值預篩（三路來源：CoinGlass A → Binance B → 待 K 線估算 C）
    # 規則：
    #   combined_vol ≥ MTF_VOLUME_MIN_USD → 放行（門檻由頂部常數控制，預設 5M）
    #   combined_vol = 0                  → A+B 均無資料 → 放行，等 K 線估算（Plan C）
    #   0 < combined_vol < MTF_VOLUME_MIN_USD → 確認流動性不足 → 過濾
    # ════════════════════════════════════════════════════════
    VOLUME_PREFILTER_MIN_USD = MTF_VOLUME_MIN_USD  # 從常數讀取（預設 5M，可在頂部常數區調整）

    active_above_volume: List[Dict[str, Any]] = []
    vol_cg = 0         # Plan A (CoinGlass) 有資料且 ≥ MTF_VOLUME_MIN_USD
    vol_binance = 0    # Plan B (Gate備援) 補救且 ≥ MTF_VOLUME_MIN_USD
    vol_no_data = 0    # A+B 均無資料 → 放行等 Plan C
    vol_below = 0      # 確認不足門檻 → 過濾

    for coin in active_symbols:
        # ── Plan A：CoinGlass 成交值 ─────────────────────────────
        cg_vol = coin.get("_cg_volume_usd")
        try:
            cg_vol = float(cg_vol) if cg_vol is not None else 0.0
        except (TypeError, ValueError):
            cg_vol = 0.0

        # ── Plan B：Binance 備援（CoinGlass 無資料時使用）──────────
        combined_vol = cg_vol
        _vol_source = "CoinGlass"
        if cg_vol == 0.0 and _binance_vol_map:
            base_key = (coin.get("symbol") or coin.get("coin") or "").replace("USDT", "").replace("-", "").upper()
            b_vol = _binance_vol_map.get(base_key, 0.0)
            if b_vol > 0:
                combined_vol = b_vol
                _vol_source = "Gate"

        coin["_volume_usd"] = combined_vol
        coin["_cg_volume_usd"] = combined_vol
        coin["_vol_source"] = _vol_source

        if combined_vol == 0.0:
            # A+B 均無資料 → 放行，等 enrichment 階段 Plan C（K 線估算）補充
            coin["_vol_need_planc"] = True
            vol_no_data += 1
            active_above_volume.append(coin)
        elif combined_vol >= VOLUME_PREFILTER_MIN_USD:
            if _vol_source == "CoinGlass":
                vol_cg += 1
            else:
                vol_binance += 1
            active_above_volume.append(coin)
        else:
            vol_below += 1

    logger.info(
        f"📊 [漏斗 4] 成交值篩選 ≥{MTF_VOLUME_MIN_USD/1e6:.1f}M: 通過 {len(active_above_volume)} 個"
        f"（CoinGlass: {vol_cg} | Gate備援: {vol_binance} | 待K線估算: {vol_no_data} | 淘汰[確認<{MTF_VOLUME_MIN_USD/1e6:.1f}M]: {vol_below}）"
    )

    # ── Step 5：排序 + 限制數量（前 50 固定，其餘隨機保多樣性）─────────────────
    MAX_OI_SYMBOLS = 320
    target_symbols: List[Dict[str, Any]] = []
    if active_above_volume:
        active_above_volume.sort(key=lambda c: c.get("_volume_usd", 0.0), reverse=True)
        top_fixed = active_above_volume[:50]
        rest = active_above_volume[50:]
        if rest:
            random.shuffle(rest)
        combined = top_fixed + rest
        target_symbols = combined[:MAX_OI_SYMBOLS]
    if len(active_above_volume) > MAX_OI_SYMBOLS:
        logger.info(
            f"成交量過篩後共 {len(active_above_volume)} 個，本輪僅處理前 {MAX_OI_SYMBOLS} 個以確保準時推播 "
            f"(前 50 依成交額固定，其餘隨機採樣)"
        )
    
    long_open = []
    long_close = []
    short_open = []
    short_close = []
    
    processed_count = 0
    oi_success_count = 0
    oi_fail_count = 0
    
    # 並行處理配置：標準版高頻模式，預設 12 執行緒；熔斷器啟動時自動降為 1
    MAX_WORKERS = _cb_get_max_workers(default=15)
    _cb_tripped = _circuit_breaker.get("tripped", False)
    logger.info(f"[啟動環境] CG_API_KEY={'已設定('+CG_API_KEY[:6]+'...)' if CG_API_KEY else '❌未設定'}"
                f" | MAX_WORKERS={MAX_WORKERS} | 熔斷器={'⚠️降速模式' if _cb_tripped else '✅正常'}")
    if MAX_WORKERS == 1:
        logger.warning("[熔斷器作用中] MAX_WORKERS 已降為 1，本輪採單執行緒保護模式")
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
                price_change_1h = result.get('priceChange1h')
                price_change = result.get('priceChange30m') or price_change_1h
                oi_change = result.get('oiChange1h') or result.get('oiChange30m')
                price_change_24h = result.get('priceChange24h')
                item = {
                    'symbol': symbol,
                    'priceChange1h': price_change_1h,
                    'priceChange30m': price_change,
                    'oiChange1h': oi_change,
                    'oiChange30m': oi_change,    # 向後相容
                    'priceChange24h': price_change_24h,
                    'price_change_percent_1h': price_change_1h,
                    '_cg_volume_usd': result.get('_cg_volume_usd'),
                    '_taker_ratio_15m': result.get('_taker_ratio_15m'),
                }
                base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
                oi_min = OI_THRESHOLD_MAIN if base in MAIN_COINS else (
                    OI_THRESHOLD_HIGH_LIQ if (result.get("_cg_volume_usd") or 0) >= HIGH_LIQ_VOLUME_USD
                    else OI_THRESHOLD_SMALL
                )
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
    in_four = len(long_open) + len(long_close) + len(short_open) + len(short_close)
    below_oi_threshold = oi_success_count - in_four
    logger.info(
        f"📊 [Step1 1H OI掃描] 共 {processed_count} 幣 | 成功 {oi_success_count} 失敗 {oi_fail_count} | 用時 {total_time/60:.1f}min | "
        f"入選: 多開 {len(long_open)} 多平 {len(long_close)} 空開 {len(short_open)} 空平 {len(short_close)} "
        f"（達門檻 {in_four} / OI成功 {oi_success_count}）"
    )

    # 分層 OI 門檻：一律依幣種套用 4%（主流）/ 6%（高流動）/ 8%（小幣），無樣本 fallback
    logger.info(
        f"【OI門檻】強制分層：主流 {OI_THRESHOLD_MAIN:.0f}% / "
        f"高流動 {OI_THRESHOLD_HIGH_LIQ:.0f}% / 小幣 {OI_THRESHOLD_SMALL:.0f}%"
    )
    long_open = [x for x in long_open if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    long_close = [x for x in long_close if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    short_open = [x for x in short_open if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    short_close = [x for x in short_close if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    # ── 按 1H OI 絕對值排名（取前3名，OI越大=主力動作越明確）────────────────
    # 目的：找「持倉變化最劇烈」的幣，不是隨機取樣
    long_open.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    long_close.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    short_open.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    short_close.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    top_long_open  = long_open[:5]
    top_long_close = long_close[:5]
    top_short_open = short_open[:5]
    top_short_close = short_close[:5]
    # 記錄各類別排名（1=OI最大），供後續評級使用
    for _cat_list in (top_long_open, top_long_close, top_short_open, top_short_close):
        for _rank_i, _item in enumerate(_cat_list):
            _item["_oi_rank"] = _rank_i + 1
    logger.info(
        f"📊 [TOP候選] 多開 {len(top_long_open)} 多平 {len(top_long_close)} 空開 {len(top_short_open)} 空平 {len(top_short_close)}（各取前3）→ 開始 enrichment"
    )

    # ════════════════════════════════════════════════════════
    # Enrichment：核心資料（CoinGlass 技術指標 + 資金費率）
    # ════════════════════════════════════════════════════════
    _cg_fr_map: Dict[str, float] = _fetch_funding_rate_map()
    logger.info(f"[FR批次] CoinGlass Funding Rate 預載完成，共 {len(_cg_fr_map)} 個幣種")

    all_top = []
    for item, cat in [(x, "long_open") for x in top_long_open] + [(x, "long_close") for x in top_long_close] + [(x, "short_open") for x in top_short_open] + [(x, "short_close") for x in top_short_close]:
        sym = item.get("symbol", "")

        # ── 黑名單前置過濾（在 K 線抓取前攔截，節省 API 次數）──────────────────────
        _sym_base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        # 代幣化股票自動攔截：PLTRSTOCK / MASTOCK / NVDASTOCK 等以 STOCK 結尾的格式
        _is_tokenized_stock = _sym_base.endswith("STOCK") or _sym_base.endswith("TOKEN")
        if _sym_base in SYMBOL_BLACKLIST or _is_tokenized_stock:
            logger.info(f"[黑名單🚫] {sym} 在 enrichment 前即封鎖，跳過 K 線抓取")
            continue

        # 技術指標：CoinGlass K 線計算 RSI / ATR / 結構高低點
        # （_fetch_cg_klines_and_calc 內部已有 _respect_coinglass_rate_limit 限速，無需額外 sleep）
        tech = calculate_technicals(sym)
        # K 線無效則立即結束本幣種 enrichment：不呼叫 OI 多週期 / CVD 背離，節省 API
        if not tech:
            logger.info(f"[K線無效⚠️] {sym}: 無法取得技術指標，跳過 enrichment（不呼叫 CVD/30m/15m/5m）")
            continue
        if tech.get("recent_high_2h") is None or tech.get("recent_low_2h") is None:
            logger.info(
                f"[K線無效⚠️] {sym}: 缺 2H 結構高低（recent_high_2h/recent_low_2h），"
                f"跳過 enrichment（不呼叫 CVD/30m/15m/5m）"
            )
            continue

        # ── Plan C：K 線估算成交值（補充 CoinGlass + Binance 均無資料的幣種）──────
        if item.get("_vol_need_planc") and tech:
            kline_vol_est = tech.get("kline_vol_usd_24h")
            if kline_vol_est and kline_vol_est > 0:
                item["_volume_usd"] = kline_vol_est
                item["_cg_volume_usd"] = kline_vol_est
                item["_vol_source"] = "K線估算"
                item.pop("_vol_need_planc", None)
                logger.debug(f"[Plan C] {sym}: K線估算 24h 成交值 {kline_vol_est/1e6:.2f}M USD")

        # 補取 1H/4H OI（Enrichment 用，僅對 top 少量候選幣種呼叫）
        _oi_tf = _fetch_oi_multi_tf(sym)
        item["oi_change_1h_pct"] = _oi_tf.get("1h")
        item["oi_change_4h_pct"] = _oi_tf.get("4h")

        # 4H 宏觀天候：EMA20 + RSI（Google 建議新增，僅作輔助資訊不作濾網阻斷）
        _tech_4h = _fetch_cg_klines_and_calc(sym, interval="4h", limit=20)
        _ema20_4h = _tech_4h.get("ema20_close") if _tech_4h else None
        _rsi_4h   = _tech_4h.get("rsi")        if _tech_4h else None
        # 判斷現價是否站上 4H EMA20（順/逆勢天候）
        # CoinGlass 有 price 的幣優先用 CoinGlass；Gate-only 幣（price=None）
        # 用 1H K線收盤（tech.current_price）作備援，確保 4H EMA 比對不失效
        _cur_price_prelim = item.get("price") or (tech.get("current_price") if tech else None)
        _is_above_4h_ema  = (
            bool(_cur_price_prelim > _ema20_4h)
            if (_cur_price_prelim and _ema20_4h and _ema20_4h > 0)
            else None
        )

        # 資金費率：CoinGlass 批次表（純 CoinGlass 模式，不再呼叫 Gate fallback）
        _base_fr = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        funding_rate = _cg_fr_map.get(_base_fr)

        # ── 勝率強化防線 A：聰明錢 OI 驗證（API 失敗時中性放行）──────────────
        _smart_money_pack = {"smart_money": None, "stable_chg": None, "coin_chg": None}
        try:
            _sm = _fetch_smart_money_oi_split(_base_fr)
            if isinstance(_sm, dict):
                _smart_money_pack["smart_money"] = _sm.get("smart_money")
                _smart_money_pack["stable_chg"] = _sm.get("stable_chg")
                _smart_money_pack["coin_chg"] = _sm.get("coin_chg")
        except Exception as _e:
            logger.debug(f"[SmartMoneyOI] {sym} 取得失敗（中性放行）: {_e}")

        # ── 勝率強化防線 B：CVD 背離（API 失敗時中性放行）────────────────────
        _cvd_div = None
        try:
            _cvd_div = detect_cvd_divergence(_base_fr)  # 回傳 bullish / bearish / None
        except Exception as _e:
            logger.debug(f"[CVD] {sym} 背離檢測失敗（中性放行）: {_e}")

        # 24h 漲跌幅
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        price_24h = item.get("priceChange24h") if isinstance(item.get("priceChange24h"), (int, float)) else None
        if price_24h is None:
            price_24h = coinglass_24h_map.get(clean_base)

        # 1H 趨勢方向（MTF 濾網）
        price_1h = item.get("priceChange1h")
        try:
            price_1h = float(price_1h) if price_1h is not None else None
        except (TypeError, ValueError):
            price_1h = None

        # 四象限分類（15m 扳機 + 1h 趨勢濾網）
        classified = _classify_signal_and_tier(
            item, cat, tech, funding_rate,
            price_chg_24h=price_24h,
            price_chg_1h=price_1h,
        )
        if classified is None:
            logger.debug(f"[MTF] 跳過 {sym}: OI<動態門檻 或 Price<{PRICE_THRESHOLD_30M}%")
            continue
        signal_label, zone, stars, rsi_desc, reason = classified
        rsi_val = tech.get("rsi") if tech else None
        atr_val = tech.get("atr") if tech else None

        # ── 反畫門防護（Anti-Manipulation Gate）────────────────────────────
        # 放在分類後（已知是真實訊號候選）、推播前，封鎖莊家假突破/畫門特徵
        _manip_result = _check_manipulation_risk(item, tech, atr_val, category=cat)
        _manip_reason = _manip_result[0] if isinstance(_manip_result, tuple) else _manip_result
        _energy_exhausted_manip = _manip_result[1] if isinstance(_manip_result, tuple) else False
        if _manip_reason:
            logger.info(
                f"[反畫門🚫] {sym}（{cat}）封鎖推播：{_manip_reason}"
            )
            continue

        # ── K 線新鮮度驗證（防止 Gate/Bybit 回傳舊蠟燭導致進場價嚴重偏差）──────────
        # 若 K 線最新收盤與 CoinGlass 即時現價偏差 > 3%，代表 K 線已過期（例如幣種剛暴噴
        # 但 API 仍回傳噴前的收盤），整組技術指標全部失效，直接跳過此訊號。
        _cg_price = item.get("price")  # CoinGlass 即時現價（掃描週期取得，較即時）
        _kline_close = tech.get("current_price") if tech else None
        if _cg_price and _kline_close and _cg_price > 0 and _kline_close > 0:
            _kline_divergence = abs(_kline_close - _cg_price) / _cg_price
            if _kline_divergence > 0.03:
                logger.warning(
                    f"[K線過期⚠️] {sym}: K線收盤 {_kline_close:.6f} 與 CoinGlass現價 "
                    f"{_cg_price:.6f} 偏差 {_kline_divergence:.1%}（>3%），K線為舊數據，跳過此訊號"
                )
                continue

        # 現價：優先採用 CoinGlass 即時現價，K 線收盤作備援
        _cur_price = _cg_price if (_cg_price and _cg_price > 0) else _kline_close

        # ════════════════════════════════════════════════════════════════════
        # 漏斗式延遲 API 請求（Lazy Fetching）— 貼合 300次/分鐘 商業標準版
        # 速率控制：每筆請求前 sleep(0.2) = 5次/秒，完全不觸發 429
        # 策略：不符合條件就立刻 continue，不浪費後續 API 額度
        # ════════════════════════════════════════════════════════════════════

        # ── Step 2：取 30m OI，立即做方向衝突預篩 ────────────────────────────
        time.sleep(0.2)
        _oi_30m = fetch_oi_change_tf(sym, "30m")
        _p_30m  = item.get("priceChange30m")

        # 30m 四象限分類（行內計算，不依賴外部函數）
        if _oi_30m is not None:
            if _oi_30m > 0:
                _cat_30m_prelim = "long_open"  if (_p_30m is None or _p_30m >= 0) else "short_open"
            else:
                _cat_30m_prelim = "short_cover" if (_p_30m is not None and _p_30m > 0) else "long_close"
        else:
            _cat_30m_prelim = None

        logger.info(
            f"[Step2 30m OI] {sym}: OI={(_oi_30m or 0):+.2f}% → {_cat_30m_prelim or 'N/A'}"
            f"  (1H={cat})"
        )

        # Step 2 衝突改為「降級不阻斷」：
        # 30m 與 1H 方向相反時，不再直接淘汰；保留進入後續流程，交由 MTF 分級為逆勢/觀察。
        _is_1h_bull_ctx = cat in ("long_open", "short_cover")
        _is_1h_bear_ctx = cat in ("short_open", "long_close")
        _tf_conflict_soft = False
        if _cat_30m_prelim is not None:
            if (_is_1h_bull_ctx and _cat_30m_prelim == "short_open") or \
               (_is_1h_bear_ctx and _cat_30m_prelim == "long_open"):
                _tf_conflict_soft = True
                logger.info(
                    f"[Step2⚠️方向衝突] {sym}: 30m={_cat_30m_prelim} 與 1H={cat} "
                    f"方向相反，降級為觀察/逆勢候選，續跑 15m+5m 檢查"
                )

        # ── Step 3 & 4：15m + 5m OI（僅針對通過 Step 2 的極少數幣種）──────────
        # short_open / long_open 訊號額外抓取 OI 歷史（4 根），供籌碼三步驟陷阱偵測使用
        time.sleep(0.2)
        _need_oi_history = (cat in ("short_open", "long_open"))
        if _need_oi_history:
            _oi_15m_result = fetch_oi_change_tf(sym, "15m", return_candles=6)
            if isinstance(_oi_15m_result, tuple):
                _oi_15m, _oi_15m_candles = _oi_15m_result
            else:
                _oi_15m, _oi_15m_candles = _oi_15m_result, []
            _oi_15m_candle_ts = _oi_15m_candles[-1]["t"] if _oi_15m_candles else 0
        else:
            _oi_15m_result = fetch_oi_change_tf(sym, "15m", return_ts=True)
            if isinstance(_oi_15m_result, tuple):
                _oi_15m, _oi_15m_candle_ts = _oi_15m_result
            else:
                _oi_15m, _oi_15m_candle_ts = _oi_15m_result, 0
            _oi_15m_candles = []
        logger.info(f"[Step3 15m OI] {sym}: OI={(_oi_15m or 0):+.2f}%")
        time.sleep(0.2)
        _oi_5m  = fetch_oi_change_tf(sym, "5m")
        logger.info(f"[Step4  5m OI] {sym}: OI={(_oi_5m or 0):+.2f}%")

        # ── MTF 訊號分類（嚴格版：不符合 A/B → None → continue）──────────────
        _mtf_item_preview = {
            "category":         cat,
            "oiChange1h":       item.get("oiChange1h") or item.get("oiChange30m") or 0,
            "priceChange1h":    price_1h or 0,
            "oiChange_30m":     _oi_30m,
            "priceChange30m":   _p_30m,
            "oiChange_15m":     _oi_15m,
            "oiChange_5m":      _oi_5m,
            "rsi":              rsi_val,
            "oi_change_4h_pct": _oi_tf.get("4h"),
            "tf_conflict_soft": _tf_conflict_soft,
        }
        _mtf_result = _classify_mtf_signal(_mtf_item_preview)

        # 嚴格訊號過濾：None = 弱訊號/方向凌亂，寧缺勿濫直接放棄
        if _mtf_result is None:
            logger.info(
                f"[嚴格過濾❌] {sym}: 不符合確定籌碼/完美回踩條件"
                f"（1H={cat}, 30m={_cat_30m_prelim}, OI15m={_oi_15m}, OI5m={_oi_5m}），放棄"
            )
            continue

        # ── CVD / Taker（順勢突破型）：加入「雙確認 / 強衝突」結構 ──────────────
        # 參考實戰判斷：方向一致（CVD + Taker 同向）才算主動資金真突破；
        # 若兩者同時反向，視為「強衝突」，先降級訊號版本以提高勝率。
        _cvd_1h = None
        _cvd_conflict_strong = False
        _cvd_confirmed = False
        if cat in ("long_open", "short_open"):
            try:
                time.sleep(0.15)
                _cvd_1h = _cvd_change_last2(sym, "1h")
            except Exception:
                pass
            _taker_chk = item.get("_taker_ratio_15m")
            try:
                _taker_chk = float(_taker_chk) if _taker_chk is not None else None
            except (TypeError, ValueError):
                _taker_chk = None
            if cat == "long_open":
                _cvd_support = (_cvd_1h is not None and _cvd_1h > 0)
                _taker_support = (_taker_chk is not None and _taker_chk >= 52)
                _cvd_opp = (_cvd_1h is not None and _cvd_1h < 0)
                _taker_opp = (_taker_chk is not None and _taker_chk < 45)
                _cvd_confirmed = bool(_cvd_support and _taker_support)
                _cvd_conflict_strong = bool(_cvd_opp and _taker_opp)
                if _cvd_conflict_strong:
                    logger.info(
                        f"[CVD/Taker強衝突🚫] {sym}: 做多但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 雙反向，降級為觀察名單以提高勝率"
                    )
                elif _cvd_opp or _taker_opp:
                    logger.info(
                        f"[CVD/Taker⚠️扣分] {sym}: 做多但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 不封鎖，改由綜合評分扣減（可能限價吸籌）"
                    )
            else:  # short_open
                _cvd_support = (_cvd_1h is not None and _cvd_1h < 0)
                _taker_support = (_taker_chk is not None and _taker_chk <= 48)
                _cvd_opp = (_cvd_1h is not None and _cvd_1h > 0)
                _taker_opp = (_taker_chk is not None and _taker_chk > 55)
                _cvd_confirmed = bool(_cvd_support and _taker_support)
                _cvd_conflict_strong = bool(_cvd_opp and _taker_opp)
                if _cvd_conflict_strong:
                    logger.info(
                        f"[CVD/Taker強衝突🚫] {sym}: 做空但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 雙反向，降級為觀察名單以提高勝率"
                    )
                elif _cvd_opp or _taker_opp:
                    logger.info(
                        f"[CVD/Taker⚠️扣分] {sym}: 做空但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 不封鎖，改由綜合評分扣減"
                    )

        # ── 資金費率多空壅擠過濾 ──────────────────────────────────────────────
        # 原理：費率偏負 = 空頭支付費率給多頭 = 空頭部位壅擠
        #       → 做空時風險高（嘎空）；做多時是順風（空頭補倉推升）
        #       費率偏正 = 多頭支付費率給空頭 = 多頭部位壅擠
        #       → 做多時風險高（多頭爆倉拋售）；做空時是順風
        _effective_version = _mtf_result.get("version", "potential")
        _fr_crowding_note = ""
        # CVD + Taker 強衝突：版本降級（後續版本門檻會濾掉），只保留更乾淨訊號
        if _cvd_conflict_strong and _effective_version == "confirmed":
            _effective_version = "tier2"
            _fr_crowding_note = "CVD/Taker 強衝突（疑似被動吸收，先觀察）"

        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            _fr_abs = abs(funding_rate)
            _is_short_sig = cat in ("long_close", "short_open")
            _is_long_sig  = cat in ("long_open", "short_close")
            _fr_pct_str   = f"{funding_rate * 100:+.4f}%"

            if _is_short_sig and funding_rate < -FR_SHORT_SQUEEZE_BLOCK:
                # 費率 < -0.3%：空頭嚴重壅擠，嘎空風險極高，封鎖做空訊號
                logger.info(
                    f"[FR封鎖🚫] {sym}: 做空訊號 費率={_fr_pct_str}"
                    f"（空頭嚴重壅擠 ≤ -{FR_SHORT_SQUEEZE_BLOCK*100}%），封鎖"
                )
                continue
            elif _is_short_sig and funding_rate < -FR_SHORT_SQUEEZE_RISK:
                # 費率 -0.1%~-0.3%：空頭壅擠警戒，做空訊號降級
                _effective_version = "tier2"
                _fr_crowding_note = f"空頭壅擠警示（費率{_fr_pct_str}，嘎空風險偏高）"
                logger.info(
                    f"[FR降級⚠️] {sym}: 做空訊號 費率={_fr_pct_str} 空頭壅擠 → 降為觀察名單"
                )
            elif _is_long_sig and funding_rate > FR_LONG_LIQUIDATION_BLOCK:
                # 費率 > +0.5%：多頭嚴重壅擠，爆倉風險高，封鎖做多訊號
                logger.info(
                    f"[FR封鎖🚫] {sym}: 做多訊號 費率={_fr_pct_str}"
                    f"（多頭嚴重壅擠 ≥ +{FR_LONG_LIQUIDATION_BLOCK*100}%），封鎖"
                )
                continue
            elif _is_long_sig and funding_rate > FR_LONG_LIQUIDATION_RISK:
                # 費率 +0.2%~+0.5%：多頭壅擠警戒，做多訊號降級
                _effective_version = "tier2"
                _fr_crowding_note = f"多頭壅擠警示（費率{_fr_pct_str}，爆倉風險偏高）"
                logger.info(
                    f"[FR降級⚠️] {sym}: 做多訊號 費率={_fr_pct_str} 多頭壅擠 → 降為觀察名單"
                )

        # ── 3 步反轉陷阱偵測（short_open 摸頭 / long_open 摸底，OI+價格雙重確認）──
        _bull_trap_result = {"detected": False, "matched_steps": 0, "note": ""}
        if cat in ("short_open", "long_open") and _oi_15m_candles:
            _trap_type = "short" if cat == "short_open" else "long"
            time.sleep(0.15)
            _kline_15m = _fetch_15m_klines_raw(sym, limit=6) if sym else None
            _bull_trap_result = detect_trap_setup(_oi_15m_candles, _trap_type, _kline_15m)
            if _bull_trap_result.get("detected"):
                _label = "摸頭" if _trap_type == "short" else "摸底"
                logger.info(
                    f"[籌碼陷阱🎯] {sym}: 三步驟形態完整吻合"
                    f"（{_bull_trap_result['matched_steps']}/3 步）→ 強化 {_label} 訊號"
                )
            elif _bull_trap_result.get("matched_steps", 0) >= 2:
                logger.info(
                    f"[籌碼陷阱⚡] {sym}: 部分吻合"
                    f"（{_bull_trap_result['matched_steps']}/3 步）"
                )

        # ── 動能透支/乖離過大：confirmed 訊號價格偏離 VWAP > 1% → 強制限價掛單 ─────
        _energy_exhausted = _energy_exhausted_manip
        if _effective_version == "confirmed" and not _energy_exhausted:
            _vwap = tech.get("vwap_2h") if tech else None
            if _vwap and _cur_price and float(_vwap) > 0:
                _dev_pct = abs(float(_cur_price) - float(_vwap)) / float(_vwap) * 100
                if _dev_pct > 1.0:
                    _energy_exhausted = True
                    logger.info(
                        f"[動能透支⚠️] {sym}: confirmed 訊號價格偏離 VWAP {_dev_pct:.1f}% > 1%，"
                        f"強制限價掛單於 EMA20"
                    )

        _io_flag, _lp_val = derive_limit_order_from_inputs(
            cat,
            _cur_price,
            tech.get("vwap_2h") if tech else None,
            tech.get("ema20_close") if tech else None,
            _effective_version,
            _energy_exhausted,
        )

        all_top.append({
            **item,
            "priceChange24h": price_24h,
            "priceChange1h": price_1h,
            # 15m 價格變動（獨立欄位，供車已發動偵測使用）
            "priceChange15m": item.get("price_change_percent_15m") or item.get("priceChange15m"),
            "category": cat,
            "current_price": _cur_price,
            "rsi": rsi_val,
            "atr": atr_val,
            "recent_high_2h": tech.get("recent_high_2h") if tech else None,
            "recent_low_2h": tech.get("recent_low_2h") if tech else None,
            "pre_breakout_low": tech.get("pre_breakout_low") if tech else None,
            "pre_breakout_high": tech.get("pre_breakout_high") if tech else None,
            "ema20": tech.get("ema20_close") if tech else None,
            "ema20_touch_low": tech.get("ema20_touch_low") if tech else None,
            "ema20_touch_high": tech.get("ema20_touch_high") if tech else None,
            "last_kline_high_30m": tech.get("last_kline_high_30m") if tech else None,
            "last_kline_low_30m": tech.get("last_kline_low_30m") if tech else None,
            "last_kline_open_30m": tech.get("last_kline_open_30m") if tech else None,
            "last_kline_close_30m": tech.get("last_kline_close_30m") if tech else None,
            "signal_label": signal_label,
            "zone": zone,
            "stars": stars,
            "rsi_desc": rsi_desc,
            "reason": reason,
            "funding_rate": funding_rate,
            # 勝率強化欄位：smart money + CVD（grade 層做硬過濾與加分）
            "smart_money": _smart_money_pack.get("smart_money"),
            "stable_oi_chg": _smart_money_pack.get("stable_chg"),
            "coin_oi_chg": _smart_money_pack.get("coin_chg"),
            "cvd_divergence": _cvd_div,
            # 1h CVD 變化（僅 long_open/short_open 有值），供 _calc_signal_grade 5b 扣分
            "_cvd_1h": _cvd_1h,
            "_cvd_confirmed": _cvd_confirmed,
            "_cvd_conflict_strong": _cvd_conflict_strong,
            "vwap_2h": tech.get("vwap_2h") if tech else None,
            # _scan_ts = 1H OI 首次偵測時間（process_single_symbol 打上），保留原始時間
            # 若 item 無此欄位（舊路徑），以當前時間補足
            "_detected_ts": item.get("_scan_ts") or time.time(),
            # 15m OI K線起始時間（CoinGlass 資料本身的時間戳，代表持倉異動發生的時間窗）
            "_oi_15m_candle_ts": locals().get("_oi_15m_candle_ts") or 0,
            # MTF 四層數據
            "oiChange_30m": _oi_30m,
            "oiChange_15m": _oi_15m,
            "oiChange_5m":  _oi_5m,
            # MTF 訊號版本（已套入 FR 壅擠過濾，_effective_version 可能降級）
            "signal_version":  _effective_version,
            "signal_subtype":  _mtf_result.get("subtype", "") or _fr_crowding_note,
            "mtf_desc":        _mtf_result.get("mtf_desc", ""),
            "mtf_oi_line":     _mtf_result.get("mtf_oi_line", ""),
            "mtf_aligned":     _mtf_result.get("aligned_count", 1),
            "reversal_hint":   _mtf_result.get("reversal_hint", ""),
            # 4H 宏觀天候（輔助資訊）
            "ema20_4h":        _ema20_4h,
            "rsi_4h":          _rsi_4h,
            "is_above_4h_ema": _is_above_4h_ema,
            # 誘多摸頭陷阱偵測（short_open 專屬）
            "_bull_trap_detected": _bull_trap_result.get("detected", False),
            "_bull_trap_steps":    _bull_trap_result.get("matched_steps", 0),
            "_bull_trap_note":     _bull_trap_result.get("note", ""),
            # 動能透支/乖離過大：強制限價掛單於 EMA20，拒絕市價進場
            "_energy_exhausted": _energy_exhausted,
            # 限價單統一標記（與推播進場價、即時觸損/達標略過邏輯一致）
            "is_limit_order": _io_flag,
            "limit_price": _lp_val,
            # 衰竭反轉：抄底/摸頭方向（long/short），供推播覆寫 is_bull_sig 與標題
            "_exhaustion_reversal_direction": _mtf_result.get("exhaustion_direction"),
        })
        _ver_tag = (
            "🔥衰竭反轉" if _effective_version == "exhaustion_reversal"
            else "✅確定籌碼（鐵三角）" if _effective_version == "confirmed"
            else f"⚠️觀察名單({_fr_crowding_note or _mtf_result.get('subtype','')})" if _effective_version == "tier2"
            else f"🎯潛在機會({_mtf_result.get('subtype','')})"
        )
        logger.info(f"[Enrichment] {sym} 已加入 all_top：RSI={rsi_val} ATR={atr_val} 現價={_cur_price} | {_ver_tag} | {reason}")

    # 品質門撒①：ATR=None → K 線無數據，SL/TP/RSI 均無法計算，不推播
    pre_quality = len(all_top)
    all_top = [x for x in all_top if x.get("atr") is not None]
    skipped_no_kline = pre_quality - len(all_top)
    if skipped_no_kline > 0:
        logger.info(f"[品質門撒①] 淘汰 {skipped_no_kline} 個 ATR=None（K線無數據小幣），剩餘 {len(all_top)} 個訊號")

    # 品質門撒②：成交值仍未確認（三路均無資料：CoinGlass / Gate / K線估算全失敗）
    # 這些幣是在漏斗4以「待K線估算」名義放行的，但 Plan C 也沒估出來
    # → 無法確認流動性達標，不推播，避免推出「成交值 無數據」的訊號
    pre_vol = len(all_top)
    all_top = [x for x in all_top if not x.get("_vol_need_planc")]
    skipped_no_vol = pre_vol - len(all_top)
    if skipped_no_vol > 0:
        logger.info(f"[品質門撒②] 淘汰 {skipped_no_vol} 個成交值未確認（三路均無資料），剩餘 {len(all_top)} 個訊號")

    # 成交額同步（從 _cg_volume_usd 寫入供推播使用）
    for x in all_top:
        x["volume_usd"] = x.get("_volume_usd") or x.get("_cg_volume_usd") or 0

    # 品質門撒③（微調）：OI 續航軟過濾
    # - 以前是硬淘汰，訊號容易被砍光
    # - 現在改成只淘汰「15m+5m 都明顯反向」；其餘降權放行
    # - 目的：維持抗畫門能力，同時避免 0 訊號
    def _oi_flow_consistent(_x: Dict) -> bool:
        _cat = (_x.get("category") or "").strip()
        try:
            _oi15 = float(_x.get("oiChange_15m") or 0.0)
            _oi5 = float(_x.get("oiChange_5m") or 0.0)
        except (TypeError, ValueError):
            # 無 15m/5m 資料不直接砍，交由後續評分處理
            return True
        _is_open = _cat in ("long_open", "short_open")
        _is_close = _cat in ("long_close", "short_close")
        if not (_is_open or _is_close):
            return False
        # 持倉「建倉」理論上 15m/5m 應偏正；若雙週期都明顯反向才淘汰
        if _is_open:
            if _oi15 <= -0.35 and _oi5 <= -0.20:
                return False
            return True
        # 持倉「平倉」理論上 15m/5m 應偏負；若雙週期都明顯反向才淘汰
        if _oi15 >= 0.35 and _oi5 >= 0.20:
            return False
        return True

    _pre_oi_flow = len(all_top)
    all_top = [x for x in all_top if _oi_flow_consistent(x)]
    _drop_oi_flow = _pre_oi_flow - len(all_top)
    if _drop_oi_flow > 0:
        logger.info(
            f"[品質門撒③ OI續航(軟過濾)] 淘汰 {_drop_oi_flow} 個『15m+5m雙週期明顯反向』訊號，"
            f"剩餘 {len(all_top)} 個"
        )

    # 訊號版本門檻（Classic 80%）：
    # - 保留 confirmed / exhaustion_reversal
    # - 放行 tier2（觀察轉實戰）與 pullback（回踩跟隨）
    #   讓節奏更接近 2 月短線狙擊版本，不再只剩極少數訊號
    _ALLOW_PUSH_SIGNAL_VERSIONS = frozenset({"confirmed", "exhaustion_reversal", "tier2", "pullback"})
    _pre_ver_filt = len(all_top)
    all_top = [
        x for x in all_top
        if (x.get("signal_version") or "") in _ALLOW_PUSH_SIGNAL_VERSIONS
    ]
    if _pre_ver_filt - len(all_top) > 0:
        logger.info(
            f"[版本門檻] 淘汰 {_pre_ver_filt - len(all_top)} 個非 confirmed/衰竭反轉，"
            f"剩餘 {len(all_top)} 個"
        )

    _confirmed_cnt = sum(1 for x in all_top if x.get("signal_version") == "confirmed")
    _exhaust_cnt   = sum(1 for x in all_top if x.get("signal_version") == "exhaustion_reversal")
    _tier2_cnt     = sum(1 for x in all_top if x.get("signal_version") == "tier2")
    _pullback_cnt = sum(1 for x in all_top if x.get("signal_version") == "pullback")
    logger.info(
        f"[Enrichment 完成] {len(all_top)} 個訊號進入推播流程"
        f"（✅確定籌碼 {_confirmed_cnt} | 🔥衰竭反轉 {_exhaust_cnt}"
        f"{' | ⚠️Tier2 ' + str(_tier2_cnt) if _tier2_cnt else ''}"
        f"{' | ↩️回踩 ' + str(_pullback_cnt) if _pullback_cnt else ''}）"
    )
    if len(all_top) == 0:
        logger.info(f"本輪無符合條件訊號（1H OI≥動態門檻 & 成交值≥{MTF_VOLUME_MIN_USD/1e6:.0f}M USD & MTF共振未達標）")

    # 冷卻規則：同幣同方向 N 小時內不重複推；同輪每方向最多 M 檔（強籌碼優先）
    # 統一預設 2 小時冷卻（同幣同方向）；需其他值可設 SNIPER_COOLDOWN_HOURS
    _default_cd_hours = 2.0
    COOLDOWN_HOURS = int(max(1, round(_env_float("SNIPER_COOLDOWN_HOURS", _default_cd_hours))))
    HISTORY_HOURS = 24   # 冷卻歷史保留 24h（每日自動清理）
    # 順勢 S/A 推過後，此時間內不推「反向」R（S 為主、R 為輔；避免敘事打架）
    TREND_VS_R_OPPOSITE_HOURS = 12

    def _item_direction(x: Dict) -> str:
        """只回傳 多/空。優先用 build_report 已設定的 dir 欄位，其次用 category，最後才解析 signal_label。"""
        # 1. 最可靠：build_report_message_tiered 在每個推播項目上直接設定的 dir
        d = (x.get("dir") or "").strip()
        if d in ("多", "空"):
            return d
        # 2. 從 category 判斷（long_open / short_close = 看多訊號）
        cat = (x.get("category") or x.get("entry_category") or "").strip()
        if cat in ("long_open", "short_close"):
            return "多"
        if cat in ("short_open", "long_close"):
            return "空"
        # 3. fallback：嘗試解析 signal_label（關鍵字擴充）
        sig = x.get("signal_label") or ""
        bull_kws = ("做多", "追多", "嘎空", "抄底", "多頭入場", "空頭平倉", "強勢做多", "Long")
        return "多" if any(kw in sig for kw in bull_kws) else "空"

    def _cooldown_symbol(s: str) -> str:
        """冷卻 key 統一用「幣種基底」比對，避免 BNLIFE / BNLIFEUSDT / BNLIFE-USDT 被當不同幣重複推。"""
        if not s:
            return ""
        return str(s).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()

    # 本輪四類籌碼分類（全表，供出場提示比對）：當初推多→若本輪變 short_open/long_close 即反轉；當初推空→若本輪變 long_open/short_close 即反轉
    current_category_by_base: Dict[str, str] = {}
    for x in long_open:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "long_open"
    for x in long_close:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "long_close"
    for x in short_open:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "short_open"
    for x in short_close:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "short_close"

    # 冷卻檔路徑：cron/雲端環境若 data/ 不持久，可設 SNIPER_COOLDOWN_DIR 指向同一目錄（絕對路徑）
    _cooldown_dir = os.getenv("SNIPER_COOLDOWN_DIR")
    if _cooldown_dir:
        _cooldown_dir = Path(_cooldown_dir).resolve()
        _cooldown_dir.mkdir(parents=True, exist_ok=True)
        SNIPER_COOLDOWN_FILE = _cooldown_dir / "sniper_cooldown.json"
    else:
        SNIPER_COOLDOWN_FILE = (DATA_DIR / "sniper_cooldown.json").resolve()
    _cooldown_path_abs = str(SNIPER_COOLDOWN_FILE)
    # 冷卻 + 推播紀錄改為「單一 JSON」一併讀寫，避免 CI cache 還原時兩檔不一致（冷卻有、推播紀錄 0 筆）
    logger.info(f"狙擊狀態檔路徑（冷卻+推播紀錄）: {_cooldown_path_abs}")
    # 註冊緊急備援路徑，確保 GitHub Action timeout (SIGTERM / atexit) 前能寫回磁碟
    global _emergency_sniper_path, _emergency_sniper_state
    _emergency_sniper_path = _cooldown_path_abs
    now_ts = time.time()
    cooldown_sec = COOLDOWN_HOURS * 3600
    history_sec = HISTORY_HOURS * 3600
    history: List[Dict] = []
    push_log_signals: List[Dict] = []
    # 檔案鎖：避免 CI 或多進程同時寫入導致 JSON 損毀
    lock_file = SNIPER_COOLDOWN_FILE.with_suffix(".lock")

    @contextlib.contextmanager
    def _sniper_file_lock(timeout: float = 10.0, poll_interval: float = 0.2):
        start = time.time()
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
                try:
                    yield
                finally:
                    try:
                        os.unlink(str(lock_file))
                    except FileNotFoundError:
                        pass
                break
            except FileExistsError:
                if time.time() - start > timeout:
                    logger.warning("取得狙擊狀態檔鎖超時，放棄鎖定直接讀寫（可能存在競爭風險）")
                    yield
                    break
                time.sleep(poll_interval + random.uniform(0, poll_interval))

    # ── Gist 優先讀取冷卻狀態，失敗則 fallback 到本地 JSON ──────────
    _gist_data = _gist_load_cooldown()
    if _gist_data is not None:
        history = _gist_data.get("history") or []
        _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
        logger.info(f"冷卻檔已讀取(Gist): history {len(history)} 筆，{COOLDOWN_HOURS}h 內 {_in_window} 筆")

    try:
        with _sniper_file_lock():
            if SNIPER_COOLDOWN_FILE.exists() and _gist_data is None:
                raw = json.loads(SNIPER_COOLDOWN_FILE.read_text(encoding="utf-8"))
                history = raw.get("history") or []
                # 相容舊格式：只有 last_round 時轉成 history
                if not history and raw.get("last_round"):
                    last_round = raw.get("last_round") or []
                    if last_round and isinstance(last_round[0], dict):
                        history = [{"symbol": str(p.get("symbol")), "dir": str(p.get("dir")), "ts": int(now_ts) - 3600} for p in last_round if p.get("symbol") and p.get("dir")]
                    else:
                        history = [{"symbol": str(p[0]), "dir": str(p[1]), "ts": int(now_ts) - 3600} for p in last_round if isinstance(p, (list, tuple)) and len(p) >= 2]
                logger.info(f"冷卻檔已讀取: {_cooldown_path_abs} | 歷史 {len(history)} 筆")
            else:
                if _gist_data is None:
                    logger.info(f"冷卻狀態檔不存在，本輪無冷卻限制: {_cooldown_path_abs}")
    except Exception as e:
        history = []
        logger.warning(f"讀取冷卻狀態檔失敗，本輪無冷卻限制: {e}")

    now_tw = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
    _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
    logger.info(f"冷卻狀態: {len(history)} 筆歷史，{COOLDOWN_HOURS}h 內 {_in_window} 筆（同幣同方向才冷卻）")

    # 冷卻集合：同幣同方向在 COOLDOWN_HOURS 內已推過則阻擋
    cooldown_symbol_dir_4h: Set[Tuple[str, str]] = set()
    last_round_by_sym: Dict[str, str] = {}
    last_push_ts_by_sym_dir: Dict[Tuple[str, str], float] = {}
    for e in history:
        if not isinstance(e, dict) or not e.get("symbol") or not e.get("dir"):
            continue
        s = _cooldown_symbol(str(e["symbol"]))
        d = str(e["dir"])
        if (now_ts - e.get("ts", 0)) <= cooldown_sec:
            cooldown_symbol_dir_4h.add((s, d))
        if s not in last_round_by_sym:
            last_round_by_sym[s] = d
        key = (s, d)
        if key not in last_push_ts_by_sym_dir or (e.get("ts") or 0) > last_push_ts_by_sym_dir[key]:
            last_push_ts_by_sym_dir[key] = float(e.get("ts") or 0)
    latest_signal_by_sym: Dict[str, Dict[str, Any]] = {}

    # ── 黑名單二道防線（enrichment 前已擋一次，此處確保無漏網之魚）────────────────
    _before_bl = len(all_top)
    all_top = [
        x for x in all_top
        if _cooldown_symbol(x.get("symbol") or "").upper() not in SYMBOL_BLACKLIST
    ]
    _bl_removed = _before_bl - len(all_top)
    if _bl_removed > 0:
        logger.info(f"[黑名單🚫] 二道防線攔截 {_bl_removed} 個標的")

    cooled_top = []
    for x in all_top:
        sym = x.get("symbol") or ""
        if not sym:
            continue
        sym_norm = _cooldown_symbol(sym)
        cur_dir = _item_direction(x)
        # 冷卻視窗內是否剛推過「反向」：同標的、異方向（允許推播，但需要提醒）
        _opp_dir = "空" if cur_dir == "多" else "多"
        x["cooldown_reverse_recent"] = (sym_norm, _opp_dir) in cooldown_symbol_dir_4h

        # 同幣同方向：COOLDOWN_HOURS 內阻擋重推
        if (sym_norm, cur_dir) in cooldown_symbol_dir_4h:
            logger.info(f"冷卻跳過: {sym_norm} ({cur_dir}) ({COOLDOWN_HOURS}h 內同幣同方向已報過)")
            continue

        # 同幣換方向：標記多轉空/空轉多提醒
        if sym_norm in last_round_by_sym and last_round_by_sym[sym_norm] != cur_dir:
            x["direction_flip"] = last_round_by_sym[sym_norm] + "轉" + cur_dir
        else:
            x["direction_flip"] = None
        cooled_top.append(x)

    _skipped = len(all_top) - len(cooled_top)
    if _skipped > 0:
        logger.info(f"本輪冷卻跳過 {_skipped} 檔（同幣同方向 {COOLDOWN_HOURS}h 內不重推）")

    # 依方向分組後以 |1H OI%| 排序（大者在前）；不設每方向檔數上限
    def _oi_abs_round_cap(xx: Dict) -> float:
        try:
            return abs(float(xx.get("oiChange1h") or 0))
        except (TypeError, ValueError):
            return 0.0

    _by_dir_lists: Dict[str, List] = {"多": [], "空": []}
    for _cx in cooled_top:
        _dkey = _item_direction(_cx)
        if _dkey in _by_dir_lists:
            _by_dir_lists[_dkey].append(_cx)
    _cooled_sorted: List = []
    for _dkey in ("多", "空"):
        _lst = _by_dir_lists[_dkey]
        _lst.sort(key=_oi_abs_round_cap, reverse=True)
        _cooled_sorted.extend(_lst)
    cooled_top = _cooled_sorted

    # ── 多所共識已移除（原 fetch_exchange_oi_consensus API 回傳資料與 15m 時間窗口不符，誤判多）────
    # is_global_consensus 欄位保留但固定為 False，is_premium 已不依賴此欄位
    if cooled_top:
        for _item in cooled_top:
            _item["is_global_consensus"] = False
            _item["volume_oi_warn"] = False

    # ── 推播前即時報價快照 + 結構 SL 觸損／達標防護 ─────────────────────────────
    # 與 build_report_message_tiered 相同：compute_structural_sl_tp；即時價已破 SL 或已過 TP1 → 不推。
    if cooled_top:
        _drop_low_r: List = []
        for _x in cooled_top:
            _sym_rt = _x.get("symbol") or ""
            _sig_price = _x.get("current_price")
            _x["signal_price"] = _sig_price
            if not _sig_price or not isinstance(_sig_price, (int, float)) or float(_sig_price) <= 0:
                continue
            try:
                _snap = _fetch_bingx_ticker_snapshot(_sym_rt)
                if _snap and _snap.get("price") and float(_snap["price"]) > 0:
                    _live = float(_snap["price"])
                    _drift = abs(_live - float(_sig_price)) / float(_sig_price)
                    _x["current_price"] = _live
                    if _drift >= 0.003:
                        logger.info(
                            f"[即時報價🔄] {_sym_rt}: 觸發 {_sig_price:.6f} → 即時 {_live:.6f}"
                            f"（偏差 {_drift:.1%}）"
                        )
                    _is_long_rt = (_x.get("category") or "") in ("long_open", "short_close")
                    _vwap_2h = _x.get("vwap_2h")
                    _ema_rt = _x.get("ema20") or _x.get("ema20_close")
                    # 與推播一致：僅市價進場，結構 SL/TP 一律以即時價為進場基準
                    _entry_rt = _live
                    _sl_i, _tp1_i, _tp2_i, _one_ri, _slp_rt = compute_structural_sl_tp(
                        _entry_rt,
                        _is_long_rt,
                        _vwap_2h,
                        _ema_rt,
                        _x.get("recent_low_2h"),
                        _x.get("recent_high_2h"),
                    )
                    if _sl_i is None or _tp1_i is None:
                        continue

                    _blocked = False
                    if _is_long_rt:
                        if _live <= _sl_i:
                            logger.info(
                                f"[已觸損跳過] {_sym_rt}: 即時 {_live:.6f} ≤ 結構SL {_sl_i:.6f}"
                            )
                            _blocked = True
                        elif _live >= _tp1_i:
                            logger.info(
                                f"[已達標跳過] {_sym_rt}: 即時 {_live:.6f} ≥ TP1 {_tp1_i:.6f}"
                            )
                            _blocked = True
                    else:
                        if _live >= _sl_i:
                            logger.info(
                                f"[已觸損跳過] {_sym_rt}: 即時 {_live:.6f} ≥ 結構SL {_sl_i:.6f}"
                            )
                            _blocked = True
                        elif _live <= _tp1_i:
                            logger.info(
                                f"[已達標跳過] {_sym_rt}: 即時 {_live:.6f} ≤ TP1 {_tp1_i:.6f}"
                            )
                            _blocked = True
                    if _blocked:
                        _drop_low_r.append(_x)
            except Exception as _e:
                logger.debug(f"[即時報價] {_sym_rt} 快照失敗，沿用 K 線價格: {_e}")
        # 移除觸損／已達 TP1 的訊號
        for _drop in _drop_low_r:
            if _drop in cooled_top:
                cooled_top.remove(_drop)

    # 僅在「實際有至少一則訊號」時才推主報表；無訊號或全被風報比篩掉 → 不推，安靜
    has_any = False
    if cooled_top:
        msg, has_any, push_count, s_grade_msgs, cards_payload = build_report_message_tiered(
            cooled_top,
            processed_count,
            oi_success_count,
            sa_conflict_history=history,
            sa_conflict_max_age_sec=TREND_VS_R_OPPOSITE_HOURS * 3600,
            pipeline_now_ts=now_ts,
        )
        if has_any:
            logger.info(
                f"【推播總結】本輪最終推播 {push_count} 檔"
                f"（冷卻後候選 {len(cooled_top)} 個，RSI+風報比篩選後實推 {push_count} 個）"
                f"，處理幣種 {processed_count} 個，OI 成功 {oi_success_count} 個"
            )
            # ── 每檔訊號一張 K 線卡片（caption 用原推播訊息文字不變）────────────────────
            card_dir = (DATA_DIR / "kline_cards").resolve()
            card_dir.mkdir(parents=True, exist_ok=True)

            _ohlc_cache: Dict[str, Optional[List[Dict]]] = {}
            _oi_cache: Dict[str, Optional[List[Dict]]] = {}

            sent_cnt = 0
            for idx, payload in enumerate(cards_payload or []):
                sym_b = payload.get("symbol_base") or ""
                if not sym_b:
                    continue
                caption_txt = payload.get("caption") or ""
                if not caption_txt:
                    continue

                if sym_b not in _ohlc_cache:
                    _ohlc_cache[sym_b] = fetch_ohlc_5m(sym_b, limit=60)
                ohlc = _ohlc_cache.get(sym_b)

                if sym_b not in _oi_cache:
                    _oi_cache[sym_b] = fetch_coinglass_oi_5m(sym_b, limit=60)
                oi = _oi_cache.get(sym_b)

                # 若 K 線資料不足，仍至少推文字（不影響原推播）
                img_path = str(card_dir / f"{sym_b}_{int(now_ts)}_{idx}.png")
                if ohlc and len(ohlc) >= 2:
                    try:
                        def _posf(v):
                            try:
                                vf = float(v)
                                return vf if vf > 0 else None
                            except Exception:
                                return None
                        render_kline_oi_card(
                            symbol_base=sym_b,
                            direction_is_long=bool(payload.get("direction_is_long")),
                            ohlc_5m=ohlc,
                            oi_5m=oi,
                            sl=_posf(payload.get("sl")),
                            tp1=_posf(payload.get("tp1")),
                            tp2=_posf(payload.get("tp2")),
                            entry=_posf(payload.get("entry")),
                            vwap=_posf(payload.get("vwap")),
                            ema20=payload.get("ema20"),
                            ema20_touch_low=payload.get("ema20_touch_low"),
                            ema20_touch_high=payload.get("ema20_touch_high"),
                            ema20_4h=payload.get("ema20_4h"),
                            out_path=img_path,
                            title_line=f"{sym_b} | 60x5m(~5h) EMA20=purple VWAP=cyan OI=bars",
                        )
                        ok = send_telegram_photo(
                            img_path,
                            caption_txt,
                            TG_THREAD_IDS['position_change'],
                            parse_mode="Markdown",
                        )
                        if ok:
                            sent_cnt += 1
                        else:
                            # caption 可能超長/格式衝突：退回文字推播，確保內容不變
                            send_telegram_message(
                                caption_txt,
                                TG_THREAD_IDS['position_change'],
                                parse_mode="Markdown",
                            )
                    except Exception as e:
                        logger.warning(f"[K線卡片渲染/推送失敗] {sym_b}: {e}；改推文字")
                        send_telegram_message(
                            caption_txt,
                            TG_THREAD_IDS['position_change'],
                            parse_mode="Markdown",
                        )
                else:
                    logger.warning(
                        f"[K線卡片跳過] {sym_b}: fetch_ohlc_5m 回傳不足 "
                        f"(ohlc_len={len(ohlc) if ohlc else None})；改推文字"
                    )
                    send_telegram_message(
                        caption_txt,
                        TG_THREAD_IDS['position_change'],
                        parse_mode="Markdown",
                    )

            logger.info(f"[推播] 本輪已送出 {sent_cnt}/{len(cards_payload or [])} 張 K 線卡片")
        else:
            logger.info(
                f"【未推播原因】本輪 {len(cooled_top)} 筆通過冷卻，"
                f"但即時觸損/達標或評級篩選後 0 筆可推播，不發送主報表"
            )
    else:
        if len(all_top) == 0:
            logger.info(f"【未推播原因】本輪無達 OI 門檻之標的（四類皆 0 筆），不發送主報表")
        else:
            logger.info(f"【未推播原因】本輪 {len(all_top)} 筆候選皆被冷卻（4h 內同幣同方向已推過），不發送主報表")

    # 冷卻用：僅「本輪實際有推播」的標的才寫入 history（selected_for_push 在 build_report_message_tiered 內設定）
    pairs_this_run = [
        (
            _cooldown_symbol(x.get("symbol")),
            _item_direction(x),
            str(x.get("_push_grade") or ""),
        )
        for x in cooled_top
        if x.get("symbol") and x.get("selected_for_push")
    ]

    # GitHub Step Summary：若在 GitHub Actions 環境中，輸出本輪關鍵統計摘要
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            pushed_symbols = sorted({_cooldown_symbol(x.get("symbol") or "") for x in cooled_top if x.get("symbol")}) if cooled_top else []
            pushed_list = ", ".join(pushed_symbols) if pushed_symbols else "無"
            summary_lines = [
                "## 持倉變化篩選摘要",
                "",
                "| 指標 | 數值 |",
                "| --- | --- |",
                f"| 處理幣種總數 | {processed_count} |",
                f"| OI 成功數 | {oi_success_count} |",
                f"| OI 失敗數 | {oi_fail_count} |",
                f"| OI 門檻 | 分層：主流 {OI_THRESHOLD_MAIN:.0f}% / "
                f"高流動 {OI_THRESHOLD_HIGH_LIQ:.0f}% / 小幣 {OI_THRESHOLD_SMALL:.0f}% |",
                f"| 進入 TOP 候選數 | {len(all_top)} |",
                f"| 最終推播標的數 | {len(cooled_top)} |",
                f"| 推播標的列表 | {pushed_list} |",
                "",
            ]
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            logger.warning(f"寫入 GitHub Step Summary 失敗: {e}")

    # 寫回冷卻狀態（只保留 history，移除倉位追蹤）
    try:
        SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_entries = [
            {"symbol": s, "dir": d, "grade": g, "ts": int(now_ts)}
            for (s, d, g) in pairs_this_run
            if s
        ]
        history = history + new_entries
        history = [e for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= history_sec]
        state = {"history": history}
        _emergency_sniper_state = state
        with _sniper_file_lock():
            save_json_file(SNIPER_COOLDOWN_FILE, state)
        logger.info(f"冷卻檔已寫入: 本輪 {len(new_entries)} 筆，歷史共 {len(history)} 筆 (保留 {HISTORY_HOURS}h)")
        _gist_save_cooldown(state)
    except Exception as e:
        logger.warning(f"寫入冷卻狀態檔失敗: {e}")

    logger.info("持倉變化篩選執行完成並已推播")



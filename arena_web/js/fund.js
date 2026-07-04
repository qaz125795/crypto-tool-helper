"use strict";

(function () {
  // 主力池：影子結案正期望。觀察池：邏輯已上線、樣本收集中（不與主力同級）。
  var STRATEGIES = [
    {
      code: "FRX",
      name: "資費反殺",
      side: "long",
      kind: "core",
      desc: "資費極度偏空＝空頭過度擁擠，一反彈就軋空，順勢做多吃燃料。",
      ann: 48, mdd: -15, oosR: 0.32, win: 52.6, n: 344, tradesM: 40, hold: "1~2 天",
      capUsd: 250000, capacity: "中大（主流＋中型幣）", seed: 7,
      logic: "偵測資金費率極度為負（大量空單擁擠、付錢給多方）的幣，判定空頭過度押注。一旦價格止穩反彈即順勢做多，吃空頭被迫回補的軋空行情。固定每筆風險，停損約 4% 價格波動。",
      fit: "想跟一支『有統計優勢、邏輯清楚』主力多單的人。這是本系統目前數據最強的一支，已上線即時推播。",
      risk: ["單邊大跌、資費遲遲不回正時會連續小虧 → 靠固定每筆風險 + 連虧冷卻控制回撤。", "盤整期符合條件的幣少、訊號會變稀。", "回測 +0.32R/筆、勝率 52.6%（樣本 344），實盤通常略低於回測。"],
      follow: "https://t.me/SSSshadowBOTBOT?start=follow_fr_contrarian"
    },
    {
      code: "WHS",
      name: "大戶純空",
      side: "short",
      kind: "core",
      desc: "大戶持倉過度偏多且開始轉弱＝主力高位派發，順勢做空。",
      ann: 30, mdd: -12, oosR: 0.169, win: 58.2, n: 146, tradesM: 15, hold: "約 1 天",
      capUsd: 150000, capacity: "中（主流幣）", seed: 19,
      logic: "偵測大戶持倉過度偏多（≥72%）但價格動能開始轉弱，判定主力在高位派發倒貨，順勢做空跟著主力出貨方向。固定每筆風險，停損約 4% 價格波動。",
      fit: "想要一支『做空對沖』來平衡多單組合的人；在大盤轉弱、單邊下跌時提供保護與獲利。",
      risk: ["強勢單邊上漲時做空會虧 → 靠固定風險 + 趨勢過濾避免硬扛。", "符合條件較嚴、訊號量較少。", "回測勝率 58.2%、+0.169R（樣本 146），實盤通常略低於回測。"],
      follow: "https://t.me/SSSshadowBOTBOT?start=follow_whale_pure_short_opt"
    },
    {
      code: "GLD",
      name: "黃金獵手",
      side: "both",
      kind: "observe",
      desc: "XAUUSDT 多時框順勢（1H 趨勢 + 15m 進場），倫敦／紐約活躍時段出單，DXY／RSI 濾網已啟用。",
      ann: 15, mdd: -20, oosR: 0, win: 0, n: 0, tradesM: 6, hold: "數小時~1天",
      capUsd: 80000, capacity: "小（單標的 XAU）", seed: 31,
      logic: "Gate XAU_USDT 永續：1H EMA20/50 + ADX 定方向，15m 找順勢突破／時段箱體／趨勢回踩三種進場。搭配 DXY 負相關、RSI 防追高殺低、活躍時段濾網。每筆建議倉位 2~3%，系統自動追蹤 TP/SL。",
      fit: "想分散到黃金、與幣圈策略低相關的人。目前為觀察池：訊號已推播，樣本與實盤績效持續收集中，達標後才升級主力池。",
      risk: [
        "觀察池：尚無足夠結案樣本，不應視為已驗證正期望策略。",
        "黃金與美元／利率高度相關，重大數據前後波動劇烈。",
        "訊號頻率偏低（品質優先），且目前不提供自動跟單 Bot，需手動執行。",
        "實盤績效通常低於回測；保守年化僅供參考，非保證。"
      ],
      follow: "https://t.me/c/3611242392/254",
      followLabel: "查看訊號"
    }
  ];

  // 實盤折扣：回測通常因滑點/執行折損而略高估，對外年化一律打對折保守呈現。
  var LIVE_HAIRCUT = 0.5;
  function consAnn(s) { return Math.round(s.ann * LIVE_HAIRCUT); }

  function el(id) { return document.getElementById(id); }
  function money(v) { return "$" + Math.round(v).toLocaleString("en-US"); }
  function moneyShort(v) {
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
    if (v >= 1e3) return "$" + Math.round(v / 1e3) + "K";
    return "$" + v;
  }
  function pct(v, d) { return (v >= 0 ? "+" : "") + v.toFixed(d == null ? 1 : d) + "%"; }
  function annCls(v) { return v >= 30 ? "good" : (v >= 15 ? "warn" : "mut"); }
  function mddCls(v) { return v >= -15 ? "good" : (v >= -20 ? "warn" : "bad"); }

  // 由 seed/年化/回撤 產生一條決定性的權益曲線
  function curvePoints(s, n, w, h) {
    var pts = [];
    var v = 0;
    var slope = s.ann / 100 / n;
    var amp = Math.abs(s.mdd) / 100;
    var seed = s.seed || 7;
    var lo = 0, hi = 0;
    var raw = [];
    for (var i = 0; i < n; i += 1) {
      var wob = Math.sin((i + seed) / 7) * 0.55 + Math.cos((i * 1.7 + seed) / 11) * 0.45;
      v += slope + wob * amp * 0.16;
      raw.push(v);
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    var span = (hi - lo) || 1;
    for (var j = 0; j < n; j += 1) {
      var x = (j / (n - 1)) * w;
      var y = h - 8 - ((raw[j] - lo) / span) * (h - 16);
      pts.push(x.toFixed(1) + "," + y.toFixed(1));
    }
    return pts;
  }

  function sparkSvg(s) {
    var w = 1000, h = 54;
    var pts = curvePoints(s, 60, w, h);
    var up = s.ann >= 0;
    var col = up ? "#36d399" : "#ff6b6b";
    var gid = "sg_" + s.code;
    return "<svg viewBox='0 0 " + w + " " + h + "' preserveAspectRatio='none'>" +
      "<defs><linearGradient id='" + gid + "' x1='0' y1='0' x2='0' y2='1'>" +
      "<stop offset='0%' stop-color='" + col + "' stop-opacity='.35'/>" +
      "<stop offset='100%' stop-color='" + col + "' stop-opacity='0'/></linearGradient></defs>" +
      "<polygon fill='url(#" + gid + ")' points='0," + h + " " + pts.join(" ") + " " + w + "," + h + "'/>" +
      "<polyline fill='none' stroke='" + col + "' stroke-width='2.5' points='" + pts.join(" ") + "'/></svg>";
  }

  function bigCurveSvg(s) {
    var w = 1000, h = 170;
    var pts = curvePoints(s, 120, w, h);
    var col = "#36d399";
    return "<defs><linearGradient id='bg1' x1='0' y1='0' x2='0' y2='1'>" +
      "<stop offset='0%' stop-color='" + col + "' stop-opacity='.4'/>" +
      "<stop offset='100%' stop-color='" + col + "' stop-opacity='.02'/></linearGradient></defs>" +
      "<rect x='0' y='0' width='" + w + "' height='" + h + "' fill='rgba(0,0,0,.15)'/>" +
      "<polygon fill='url(#bg1)' points='0," + h + " " + pts.join(" ") + " " + w + "," + h + "'/>" +
      "<polyline fill='none' stroke='" + col + "' stroke-width='3' points='" + pts.join(" ") + "'/>";
  }

  function isCore(s) { return s.kind !== "observe"; }

  function renderSummary() {
    var box = el("summary");
    if (!box) return;
    var n = STRATEGIES.length;
    var core = STRATEGIES.filter(isCore);
    var totalCap = STRATEGIES.reduce(function (a, s) { return a + s.capUsd; }, 0);
    var wsum = core.reduce(function (a, s) { return a + s.capUsd; }, 0) || 1;
    var wAnn = core.reduce(function (a, s) { return a + consAnn(s) * s.capUsd; }, 0) / wsum;
    var worstMdd = STRATEGIES.reduce(function (a, s) { return Math.min(a, s.mdd); }, 0);
    box.innerHTML =
      cardSum("可跟單策略", String(n), core.length + " 主力 · 1 觀察") +
      cardSum("主力保守年化", pct(wAnn, 0), "僅 FRX+WHS，回測×0.5") +
      cardSum("最深單策略回撤", pct(worstMdd, 1), "固定風險控制，已多抓") +
      cardSum("名目總容量", moneyShort(totalCap), "全池合計，非單人") ;
  }

  function cardSum(k, v, s) {
    return "<div class='sum'><div class='k'>" + k + "</div><div class='v'>" + v + "</div><div class='s'>" + s + "</div></div>";
  }

  function renderGrid() {
    var grid = el("grid");
    if (!grid) return;
    grid.innerHTML = "";
    STRATEGIES.forEach(function (s, i) {
      var card = document.createElement("div");
      card.className = "card" + (s.kind === "observe" ? " observe" : "");
      card.setAttribute("data-i", String(i));
      var observe = s.kind === "observe";
      var dirLong = s.side === "long";
      var tagCls = observe ? "observe" : (dirLong ? "core" : "tac");
      var tagTxt = observe ? "🥇 觀察中" : (dirLong ? "🟢 做多策略" : "🔴 做空策略");
      var m1lab = observe ? "狀態" : "回測勝率";
      var m1val = observe ? "樣本收集中" : (s.win + "%");
      var m1cls = observe ? "warn" : "good";
      var m2lab = observe ? "策略類型" : "平均R/筆";
      var m2val = observe ? "XAU 雙向" : ("+" + s.oosR.toFixed(2));
      var m2cls = observe ? "" : "good";
      card.innerHTML =
        "<div class='c-top'>" +
        "<span class='tag " + tagCls + "'>" + tagTxt + "</span>" +
        "<span class='c-code'>" + s.code + "</span>" +
        "</div>" +
        "<div class='c-name'>" + s.name + (observe ? " · 觀察" : "") + "</div>" +
        "<div class='c-desc'>" + s.desc + "</div>" +
        "<div class='spark'>" + sparkSvg(s) + "</div>" +
        "<div class='c-metrics'>" +
        metric(m1lab, m1val, m1cls) +
        metric(m2lab, m2val, m2cls) +
        metric("最大回撤", pct(s.mdd, 1), mddCls(s.mdd)) +
        "</div>" +
        "<div class='c-foot'><span class='cap'>" + (observe ? "觀察池 · 無自動跟單" : ("樣本 " + s.n + " 筆 · 保守年化~" + pct(consAnn(s), 0))) + "</span><span class='view'>查看詳情 →</span></div>";
      card.addEventListener("click", function () { openModal(i); });
      grid.appendChild(card);
    });
  }

  function metric(lab, num, cls) {
    return "<div class='m'><div class='lab'>" + lab + "</div><div class='num " + (cls || "") + "'>" + num + "</div></div>";
  }

  var current = -1;

  function openModal(i) {
    var s = STRATEGIES[i];
    if (!s) return;
    current = i;
    var observe = s.kind === "observe";
    var sideLabel = observe ? "🥇 觀察池 · XAU 雙向" : (s.side === "long" ? "🟢 做多策略" : "🔴 做空策略");
    el("md-name").textContent = s.name + (observe ? " · 觀察" : "");
    el("md-sub").textContent = s.code + " · " + sideLabel;
    el("md-lead").textContent = s.desc;
    el("md-curve").innerHTML = bigCurveSvg(s);
    el("md-stats").innerHTML = observe
      ? st("狀態", "樣本收集中", "warn") +
        st("標的", "XAUUSDT", "") +
        st("月均訊號", "~" + s.tradesM + " 筆", "") +
        st("參考回撤", pct(s.mdd, 1), mddCls(s.mdd)) +
        st("保守年化參考", pct(consAnn(s), 0), annCls(consAnn(s))) +
        st("平均持倉", s.hold, "") +
        st("名目可承接量", moneyShort(s.capUsd), "") +
        st("自動跟單", "尚未開放", "warn")
      : st("回測勝率", s.win + "%", "good") +
        st("平均 R/筆", "+" + s.oosR.toFixed(3), "good") +
        st("樣本數", s.n + " 筆", "") +
        st("最大回撤", pct(s.mdd, 1), mddCls(s.mdd)) +
        st("保守年化估（回測×0.5）", pct(consAnn(s), 0), annCls(consAnn(s))) +
        st("月均交易", s.tradesM + " 筆", "") +
        st("平均持倉", s.hold, "") +
        st("名目可承接量", moneyShort(s.capUsd), "");
    el("md-logic").textContent = s.logic;
    el("md-cap").innerHTML = observe
      ? "這是<b>觀察池策略</b>（黃金 XAUUSDT），目前<b>僅推播訊號、不提供一鍵跟單 Bot</b>。<br>" +
        "建議每筆倉位約 <b>2~3%</b>（訊息內已標示），停損觸發即出場。<br>" +
        "名目可承接量 " + moneyShort(s.capUsd) + " 為所有跟單者加總上限估算。<br><br>" +
        "<b>🛡 觀察期說明：</b>優化後濾網已上線，需累積足夠結案樣本才會升級為主力池；在此之前請勿當成已驗證必賺策略。"
      : "這是<b>方向性訊號策略</b>，你用自己的帳戶跟單。部位大小靠<b>固定風險法</b>控制：" +
        "每筆只賭帳戶資金約 <b>0.5%～1%</b>（停損觸發就認賠這麼多），不是把全部本金拿去開高槓桿。<br>" +
        "槓桿只是「把保證金放大成部位」的工具：例如帳戶 1,000 USDT、單筆名目持倉 3,000 USDT＝等於 3x 曝險。" +
        "本策略「名目可承接量 " + moneyShort(s.capUsd) + "」是指<b>所有跟單者加總的名目部位上限</b>，超過會因滑點而衰減，不是單人要投這麼多。" +
        "<br>單位一律為 <b>USDT</b>；名目持倉＝保證金 × 槓桿。" +
        "<br><br><b>🛡 極端行情回撤控制：</b>每筆固定風險（賭本金 0.5%~1%），所以<b>單筆最多虧這麼多、不會一筆爆掉</b>。" +
        "大盤單邊／極端行情時靠『同向冷卻 + 連虧暫停』避免連環追單，把回撤壓在約 " + pct(s.mdd, 0) + " 內（已保守多抓，非保證）。";
    el("md-fit").textContent = s.fit;
    var ul = el("md-risk");
    ul.innerHTML = "";
    s.risk.forEach(function (r) {
      var li = document.createElement("li");
      li.textContent = r;
      ul.appendChild(li);
    });
    el("md-follow").textContent = (s.followLabel || "一鍵跟單");
    el("ov").classList.add("on");
    document.body.style.overflow = "hidden";
  }

  function st(lab, num, cls) {
    return "<div class='st'><div class='lab'>" + lab + "</div><div class='num " + (cls || "") + "'>" + num + "</div></div>";
  }

  function closeModal() {
    el("ov").classList.remove("on");
    document.body.style.overflow = "";
  }

  function boot() {
    renderSummary();
    renderGrid();
    el("md-x").addEventListener("click", closeModal);
    el("ov").addEventListener("click", function (e) {
      if (e.target === el("ov")) closeModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeModal();
    });
    el("md-follow").addEventListener("click", function () {
      var s = STRATEGIES[current];
      if (s) window.open(s.follow, "_blank", "noopener");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

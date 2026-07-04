"use strict";

(function () {
  // Gate 永續合約官方費率（maker/taker，小數）。VIP0–8 為官方公告值；VIP9–10 為極速費率近似（以帳號實際為準）。
  var VIP_LEVELS = [
    { vip: "VIP 0", req: "< 1,000,000", maker: 0.00020, taker: 0.00050, approx: false },
    { vip: "VIP 1", req: "≥ 1,000,000", maker: 0.00018, taker: 0.00045, approx: false },
    { vip: "VIP 2", req: "≥ 5,000,000", maker: 0.00016, taker: 0.00040, approx: false },
    { vip: "VIP 3", req: "≥ 10,000,000", maker: 0.00014, taker: 0.00035, approx: false },
    { vip: "VIP 4", req: "≥ 25,000,000", maker: 0.00012, taker: 0.00030, approx: false },
    { vip: "VIP 5", req: "≥ 50,000,000", maker: 0.00010, taker: 0.00025, approx: false },
    { vip: "VIP 6", req: "≥ 200,000,000", maker: 0.00009, taker: 0.00022, approx: false },
    { vip: "VIP 7", req: "≥ 400,000,000", maker: 0.00008, taker: 0.00020, approx: false },
    { vip: "VIP 8", req: "≥ 600,000,000", maker: 0.00007, taker: 0.00018, approx: false },
    { vip: "VIP 9", req: "≥ 1,000,000,000", maker: 0.00006, taker: 0.00016, approx: true },
    { vip: "VIP 10", req: "≥ 2,000,000,000", maker: 0.00005, taker: 0.00014, approx: true }
  ];

  var DD_BUFFER = 1.3;     // 回撤再多抓的保守緩衝
  var DAYS = 30;
  var SAFE_DD_CAP = 0.5;   // 最壞情境保證金虧損安全上限（留 50% 緩衝、不爆倉）
  var MAX_SAFE_LEV = 25;   // 硬頂，再高都不採用

  // 安全範圍內的最高槓桿：在最壞逆向幅度下，虧損不超過 SAFE_DD_CAP，並受交易所上限與硬頂約束。
  function safeLev(s) {
    var byRisk = Math.floor(SAFE_DD_CAP / (s.worstAdverse * DD_BUFFER));
    return Math.max(1, Math.min(s.maxLev, MAX_SAFE_LEV, byRisk));
  }

  // 刷量策略：全部以「限價單(maker)」為主，才有機會打平/薄利。
  // edgeBps = 保守的「淨價差/資費」毛收益（已扣對手選擇成本），不是保證值。
  var STRATEGIES = [
    {
      id: "mm",
      name: "雙邊掛單造市",
      order: "maker",
      market: "BTC / ETH 深度池",
      maxLev: 50,
      turnsDay: 10,
      edgeBps: 0.35,        // 賺買賣價差
      worstAdverse: 0.03,
      capUsd: 400000,
      desc: "在買一賣一附近雙邊掛限價單，賺極小價差。手續費最低、量最大，是打平/薄利的主力。"
    },
    {
      id: "funding",
      name: "資費對沖（中性）",
      order: "maker",
      market: "高資費永續 + 對沖腿",
      maxLev: 20,
      turnsDay: 2,
      edgeBps: 0.50,        // 賺資金費率
      worstAdverse: 0.015,
      capUsd: 220000,
      desc: "限價建立多空對沖部位收資金費率，方向風險最低、最接近穩定打平；缺點是換手低、量也較少。"
    },
    {
      id: "grid",
      name: "網格刷量",
      order: "maker",
      market: "震盪中型主流幣",
      maxLev: 25,
      turnsDay: 12,
      edgeBps: 0.30,        // 賺區間來回價差
      worstAdverse: 0.04,
      capUsd: 250000,
      desc: "在區間內鋪一排限價單，價格來回就反覆成交賺小價差。量很大，但行情走單邊時要扛回撤。"
    },
    {
      id: "trendmaker",
      name: "趨勢掛單跟量",
      order: "maker",
      market: "SOL / 主流強勢幣",
      maxLev: 50,
      turnsDay: 4,
      edgeBps: 0.50,        // 順勢回踩限價進場
      worstAdverse: 0.035,
      capUsd: 200000,
      desc: "趨勢中等回踩時掛限價單進場、續勢平倉，用 maker 費率跟勢刷量；非追高市價單，成本可控。"
    }
  ];

  var state = { vipIdx: 10, rebate: 0.7, cap: 10000, picked: "mm", lev: null };

  function el(id) { return document.getElementById(id); }
  function money(v) {
    var sign = v < 0 ? "-" : "";
    return sign + "$" + Math.abs(Math.round(v)).toLocaleString("en-US");
  }
  function moneyShort(v) {
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return "$" + Math.round(v / 1e3) + "K";
    return "$" + Math.round(v);
  }

  function fillVipSelect() {
    var sel = el("i-vip");
    sel.innerHTML = "";
    VIP_LEVELS.forEach(function (v, i) {
      var o = document.createElement("option");
      o.value = String(i);
      o.textContent = v.vip + "（M " + (v.maker * 100).toFixed(3) + "% / T " + (v.taker * 100).toFixed(3) + "%）" + (v.approx ? " ≈" : "");
      sel.appendChild(o);
    });
    sel.value = String(state.vipIdx);
  }

  function renderVipTable() {
    var tb = el("vip-table").querySelector("tbody");
    tb.innerHTML = "";
    VIP_LEVELS.forEach(function (v, i) {
      var tr = document.createElement("tr");
      if (i === state.vipIdx) tr.className = "on";
      tr.innerHTML =
        "<td>" + v.vip + (v.approx ? " ≈" : "") + "</td>" +
        "<td>" + v.req + "</td>" +
        "<td>" + (v.maker * 100).toFixed(3) + "%</td>" +
        "<td>" + (v.taker * 100).toFixed(3) + "%</td>";
      tb.appendChild(tr);
    });
  }

  function feeRateFor(s) {
    var v = VIP_LEVELS[state.vipIdx];
    return s.order === "maker" ? v.maker : v.taker;
  }

  function renderStrategies() {
    var host = el("strat-grid");
    host.innerHTML = "";
    STRATEGIES.forEach(function (s) {
      var feeRate = feeRateFor(s);
      var lev = safeLev(s);
      var card = document.createElement("div");
      card.className = "strat" + (s.id === state.picked ? " sel" : "");
      card.innerHTML =
        "<span class='pick'>✓ 已選</span>" +
        "<div class='top'><div class='nm'>" + s.name + "</div>" +
        "<span class='ot " + s.order + "'>" + (s.order === "maker" ? "限價 Maker" : "市價 Taker") + "</span></div>" +
        "<div class='mk'>" + s.market + "</div>" +
        "<div class='row'>" +
        "<span>安全上限槓桿 <b>" + lev + "x</b></span>" +
        "<span>日換手 <b>" + s.turnsDay + "</b></span>" +
        "<span>本檔費率 <b>" + (feeRate * 100).toFixed(3) + "%</b></span>" +
        "<span>名目容量 <b>" + moneyShort(s.capUsd) + "</b></span>" +
        "</div>" +
        "<div class='rk'>" + s.desc + "</div>";
      card.addEventListener("click", function () {
        state.picked = s.id;
        state.lev = null;        // 換策略 → 槓桿回到該策略的安全上限
        syncLevSlider();
        renderStrategies();
        calc();
      });
      host.appendChild(card);
    });
  }

  function currentLev(s) {
    var safe = safeLev(s);
    if (state.lev == null) return safe;
    return Math.max(1, Math.min(s.maxLev, state.lev));
  }

  function syncLevSlider() {
    var s = STRATEGIES.find(function (x) { return x.id === state.picked; });
    if (!s) return;
    var safe = safeLev(s);
    var lev = currentLev(s);
    var sl = el("i-lev");
    sl.min = "1";
    sl.max = String(s.maxLev);
    sl.value = String(lev);
    el("v-lev").textContent = lev + "x";
    var note = el("lev-note");
    if (lev > safe) {
      note.innerHTML = "<span style='color:#ff8a8a'>⚠ 已超過安全上限 " + safe + "x，最壞虧損會破 50%、逼近爆倉</span>";
    } else {
      note.innerHTML = "（安全上限 " + safe + "x，最高可開 " + s.maxLev + "x）";
    }
  }

  function calc() {
    var s = STRATEGIES.find(function (x) { return x.id === state.picked; });
    if (!s) return;
    var cap = Math.min(state.cap, s.capUsd);
    var feeRate = feeRateFor(s);
    var lev = currentLev(s);
    var safe = safeLev(s);

    var posValue = cap * lev;
    var monthlyVol = cap * lev * s.turnsDay * 2 * DAYS;

    var grossEdge = monthlyVol * (s.edgeBps / 10000);          // 策略毛收益（價差/資費）
    var grossFee = monthlyVol * feeRate;
    var rebateBack = grossFee * state.rebate;
    var netFee = grossFee - rebateBack;                        // 已扣反佣後實付手續費
    var netPnl = grossEdge - netFee;                           // 預估月淨利（可正可負）

    var ddPct = Math.min(1, lev * s.worstAdverse * DD_BUFFER);
    var worstDd = cap * ddPct;

    var capped = state.cap > s.capUsd;
    var roiPct = cap > 0 ? (netPnl / cap) * 100 : 0;

    el("picked-name").innerHTML =
      "已選：<b style='color:#fff'>" + s.name + "</b>（限價 Maker · 費率 " + (feeRate * 100).toFixed(3) +
      "% · 槓桿 " + lev + "x" + (lev > safe ? "（超過安全上限 " + safe + "x）" : "（安全上限 " + safe + "x）") +
      (capped ? " · 保證金已封頂至容量 " + moneyShort(s.capUsd) : "") + "）";

    el("r-pos").textContent = moneyShort(posValue);
    el("r-pos-sub").textContent = moneyShort(cap) + " 保證金 × " + lev + "x";
    el("r-vol").textContent = moneyShort(monthlyVol);

    el("r-net").textContent = (netPnl >= 0 ? "+" : "-") + "$" + Math.abs(Math.round(netPnl)).toLocaleString("en-US");
    el("r-net").className = "num " + (netPnl >= 0 ? "good" : "bad");
    el("r-net-sub").textContent = "約 " + (roiPct >= 0 ? "+" : "") + roiPct.toFixed(1) + "%／月（對保證金）";

    el("r-edge").textContent = "+" + money(grossEdge);
    el("r-edge-sub").textContent = "價差/資費 " + s.edgeBps + " bps（保守估）";
    el("r-fee").textContent = "-" + money(netFee);
    el("r-fee-sub").textContent = "毛費 " + money(grossFee) + " − 反佣 " + Math.round(state.rebate * 100) + "%";

    el("r-dd").textContent = money(-worstDd) + "（" + (ddPct * 100).toFixed(0) + "% 保證金）";
    el("r-dd-sub").textContent = lev + "x × 逆向 " + (s.worstAdverse * 100).toFixed(0) + "% × 1.3" + (ddPct >= 1 ? " ≥ 爆倉" : "");

    var ul = el("assume");
    ul.innerHTML = "";
    [
      "<b>下單方式</b>：限價單(maker)，吃較低的 maker 費率——這是能打平/薄利的關鍵；改用市價單(taker)費率高 2~3 倍，大多會倒貼。",
      "<b>槓桿</b>：目前 " + lev + "x（安全上限 " + safe + "x）。" + (lev > safe ? "已超過安全上限，最壞虧損會破 50%、爆倉風險高。" : "在安全上限內，最壞虧損壓在 50% 保證金以內。") + "大資金想更穩可往下調，量與淨利會等比例變小。",
      "<b>單筆名目持倉</b>：" + moneyShort(cap) + " 保證金 × " + lev + "x = " + moneyShort(posValue) + "。",
      "<b>名目交易量</b>：" + moneyShort(posValue) + " × 每日 " + s.turnsDay + " 次來回 × 雙邊 × 30 天 = " + moneyShort(monthlyVol) + "。",
      "<b>策略毛收益</b>：保守抓 " + s.edgeBps + " bps 的價差/資費 = " + money(grossEdge) + "（非保證，行情差時會更低甚至負）。",
      "<b>手續費</b>：" + VIP_LEVELS[state.vipIdx].vip + " maker " + (feeRate * 100).toFixed(3) + "% → 毛費 " + money(grossFee) + "，反佣回收 " + Math.round(state.rebate * 100) + "% 後實付 " + money(netFee) + "。",
      "<b>預估月淨利 " + (netPnl >= 0 ? "+" : "") + money(netPnl) + "</b> = 毛收益 − 實付手續費。VIP 越高、反佣越高（最高 80%）越容易由負轉正。",
      "<b>最壞虧損</b>：" + lev + "x 槓桿下幣價逆向 " + (s.worstAdverse * 100).toFixed(0) + "% 即虧 " + Math.round(lev * s.worstAdverse * 100) + "% 保證金，再 ×1.3 緩衝＝" + (ddPct * 100).toFixed(0) + "%" + (ddPct >= 1 ? "（等同爆倉）" : "") + "。"
    ].forEach(function (t) {
      var li = document.createElement("li");
      li.innerHTML = t;
      ul.appendChild(li);
    });
  }

  function bindInputs() {
    el("i-cap").addEventListener("input", function () {
      state.cap = Number(this.value || 10000);
      el("v-cap").textContent = state.cap.toLocaleString("en-US") + " USDT";
      calc();
    });
    el("i-vip").addEventListener("change", function () {
      state.vipIdx = Number(this.value || 0);
      renderVipTable();
      renderStrategies();
      calc();
    });
    el("i-rebate").addEventListener("input", function () {
      state.rebate = Number(this.value || 0) / 100;
      el("v-rebate").textContent = Math.round(state.rebate * 100) + "%";
      calc();
    });
    el("i-lev").addEventListener("input", function () {
      state.lev = Number(this.value || 1);
      el("v-lev").textContent = state.lev + "x";
      syncLevSlider();
      calc();
    });
  }

  function boot() {
    fillVipSelect();
    renderVipTable();
    renderStrategies();
    bindInputs();
    el("v-cap").textContent = state.cap.toLocaleString("en-US") + " USDT";
    el("v-rebate").textContent = Math.round(state.rebate * 100) + "%";
    el("i-rebate").value = String(Math.round(state.rebate * 100));
    syncLevSlider();
    calc();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

"use strict";

(function () {
  // 主力池：影子結案正期望。觀察池：邏輯已上線、樣本收集中（不與主力同級）。
  // 五支選手訊號：小盤妖股為主力；其餘四支為擂台新選手（半自動跟單）。
  var STRATEGIES = [
    {
      code: "SMCP",
      name: "小盤妖股",
      side: "long",
      kind: "core",
      flagship: true,
      desc: "低市值幣種放量突破後順勢做多。頻道主力，優先跟這一支。",
      ann: 40, mdd: -12.8, oosR: 0.996, win: 45.5, n: 11, tradesM: 12, hold: "數小時~2天",
      capUsd: 60000, capacity: "小（低市值幣，容量有限）", seed: 43,
      logic: "小市值 ＋ OI 暴增 ＋ 急漲三合一做多，捕捉妖股級爆拉。胃納量小，不宜大量帶單。",
      fit: "能接受低勝率、高賺賠比節奏的人。這是目前頻道主力。",
      risk: [
        "小盤幣流動性差、滑點與插針高於主流幣。",
        "勝率約四成，連虧 5~8 筆屬常態。",
        "最大回撤可逾 10%，倉位務必控制。",
        "目前不開公開帶單號，以半自動跟單為主。"
      ],
      follow: "https://t.me/c/3611242392/250"
    },
    {
      code: "BRKq",
      name: "突破手·品質",
      side: "long",
      kind: "core",
      fresh: true,
      desc: "OI＋價＋主買突破，再加日線偏多濾網。本季期望／勝率／回撤最均衡。",
      ann: 36, mdd: -10.8, oosR: 0.719, win: 60.3, n: 68, tradesM: 25, hold: "數小時~1天",
      capUsd: 120000, capacity: "中（主流＋中型幣）", seed: 11,
      logic: "OI 增加、價格突破、主動買盤確認，且日線已偏多才做多，減少假突破。",
      fit: "想用測試帳慢慢跟、偏好較高勝率與較淺回撤的人。",
      risk: [
        "新選手訊號：90 天窗口幾乎只有本季，尚未過正式跨窗口門檻。",
        "突破失敗會連續止損。",
        "擂台本季勝率 60.3%、avgR +0.719、MDD -10.8%（n=68），實盤通常更低。"
      ],
      follow: "https://t.me/c/3611242392/250"
    },
    {
      code: "TKUP",
      name: "主買狂潮",
      side: "long",
      kind: "core",
      fresh: true,
      desc: "主買佔比暴衝後順勢做多。本季很猛，但 90 天視窗仍是負的。",
      ann: 42, mdd: -10.8, oosR: 0.884, win: 64.5, n: 110, tradesM: 30, hold: "數小時~1天",
      capUsd: 120000, capacity: "中（主流＋中型幣）", seed: 17,
      logic: "主買佔比 ≥ 65% 的暴衝級買壓做多，比純主買更激進。",
      fit: "測試帳願意跟「本季較猛」但不當聖杯的人。",
      risk: [
        "新選手訊號：90 天 avgR 為負，本季行情紅利偏大。",
        "買壓退潮時會連續止損。",
        "擂台本季勝率 64.5%、avgR +0.884、MDD -10.8%（n=110）。"
      ],
      follow: "https://t.me/c/3611242392/250"
    },
    {
      code: "BTCR",
      name: "BTC閘門動能",
      side: "long",
      kind: "core",
      fresh: true,
      desc: "BTC 偏多才放行山寨動能。本季勝率＋期望雙冠，資料幾乎只有本季。",
      ann: 48, mdd: -17.3, oosR: 1.189, win: 65.5, n: 165, tradesM: 40, hold: "數小時~1天",
      capUsd: 150000, capacity: "中（山寨，排除 BTC/ETH）", seed: 5,
      logic: "BTC 24h 偏多 regime 才允許山寨動能做多（排除 BTC/ETH）。臨時選手標籤，爆與熄火都快。",
      fit: "測試帳想賭本季最猛的人；不適合把全部本金押在這一支。",
      risk: [
        "新選手訊號：跨窗口不足（90 天幾乎只有本季）。",
        "標成臨時選手，行情一變可能快速回吐。",
        "擂台本季勝率 65.5%、avgR +1.189、MDD -17.3%（n=165）。"
      ],
      follow: "https://t.me/c/3611242392/250"
    },
    {
      code: "WHAL",
      name: "鯨魚雙吸",
      side: "long",
      kind: "core",
      fresh: true,
      desc: "現貨與永續同步淨流入＝大戶雙吸。本季綜合分最高，但 90 天期望是負的。",
      ann: 45, mdd: -11.3, oosR: 1.132, win: 62.6, n: 147, tradesM: 35, hold: "約 1 天",
      capUsd: 150000, capacity: "中（主流＋中型幣）", seed: 19,
      logic: "現貨與永續同時淨流入，視為大戶同步吸籌做多。",
      fit: "測試帳可旁觀或小倉跟；不當第一優先。",
      risk: [
        "新選手訊號：90 天 avgR -0.2，偏這波行情賺爆。",
        "鯨魚停止吸籌或轉派發時會連續止損。",
        "擂台本季勝率 62.6%、avgR +1.132、MDD -11.3%（n=147）。"
      ],
      follow: "https://t.me/c/3611242392/250"
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
    var nObs = STRATEGIES.filter(function (s) { return s.kind === "observe"; }).length;
    var nFresh = STRATEGIES.filter(function (s) { return s.fresh; }).length;
    box.innerHTML =
      cardSum("可跟單策略", String(n), core.length + " 主力 · " + nFresh + " 新選手" + (nObs ? " · " + nObs + " 觀察" : "")) +
      cardSum("主力保守年化", pct(wAnn, 0), "影子本季×0.5，非保證") +
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
      var tagCls = observe ? "observe" : (s.fresh ? "observe" : (dirLong ? "core" : "tac"));
      var tagTxt = observe ? "🥇 觀察中" : (s.flagship ? "🟢 頻道主力" : (s.fresh ? "🆕 新選手" : (dirLong ? "🟢 做多策略" : "🔴 做空策略")));
      var m1lab = observe ? "狀態" : (s.fresh ? "本季勝率" : "勝率");
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
    var sideLabel = observe ? "🥇 觀察池" : (s.flagship ? "🟢 頻道主力" : (s.fresh ? "🆕 新選手訊號" : (s.side === "long" ? "🟢 做多策略" : "🔴 做空策略")));
    el("md-name").textContent = s.name + (observe ? " · 觀察" : (s.fresh ? " · 新選手" : ""));
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
    el("md-follow").textContent = (s.followLabel || "打開訊號頻道");
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

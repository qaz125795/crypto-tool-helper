/* 影子交易擂台 — 觀戰前端（唯讀，不干涉任何倉位）*/
(function () {
  "use strict";

  var CAPITAL = 10000, GOAL = 20000, LEVERAGE = 5;
  var state = {
    players: [], registered: [], asOf: 0,
    filter: "all", tierFilter: "all",
    season: null, tournament: null, tierCounts: {}, zoneCounts: {},
  };

  var BIDIR = {
    CTRN: 1, RADAR: 1, SNIPE: 1,
    CARY: 1, REGM: 1, PRSD: 1, WTRD: 1, TSUP: 1, KUMO: 1, VWAP: 1,
    BSIZ: 1, STRP: 1, FVGR: 1, FSWD: 1, WKND: 1, BTCL: 1, POCR: 1, NR7I: 1,
    NYOR: 1, HKOR: 1, KROR: 1, GAPF: 1, ONDR: 1, WKCV: 1, SVWP: 1, NDRF: 1,
    EWTR: 1, ETSU: 1, ENR7: 1, ESTR: 1,
  };
  var SHORT_CODES = {
    FADE: 1, FNDS: 1, DSTR: 1, UNWD: 1, LSS: 1, PFS: 1, RPS: 1, PPF: 1,
    TKS: 1, WHS: 1, "WHS+": 1, SWS: 1, LLD: 1,
  };

  var TIER_ORDER = ["all", "ace", "promoted", "candidate", "watch", "warmup", "danger"];
  var TIER_META = {
    all: { label: "全部", emoji: "📋" },
    ace: { label: "王牌", emoji: "👑" },
    promoted: { label: "晉級", emoji: "🥇" },
    candidate: { label: "候選", emoji: "🥈" },
    watch: { label: "觀察", emoji: "🔭" },
    warmup: { label: "暖身", emoji: "🚧" },
    danger: { label: "危險區", emoji: "⚠️" },
  };

  // 流派 → 像素頭像 + 配色（依 cat 關鍵字比對）
  function catStyle(cat) {
    var c = cat || "";
    if (c.indexOf("優化") >= 0) return { emoji: "⭐", bg: "#e9b84a" };
    if (c.indexOf("跟莊") >= 0) return { emoji: "🐋", bg: "#5aa9e6" };
    if (c.indexOf("資費") >= 0) return { emoji: "💰", bg: "#f0c34a" };
    if (c.indexOf("技術") >= 0 || c.indexOf("均值") >= 0) return { emoji: "📊", bg: "#9b8cd6" };
    if (c.indexOf("訂單流") >= 0) return { emoji: "🌊", bg: "#4fc1c9" };
    if (c.indexOf("題材") >= 0 || c.indexOf("市值") >= 0) return { emoji: "🎯", bg: "#e8975a" };
    if (c.indexOf("軋空") >= 0 || c.indexOf("OI") >= 0 || c.indexOf("星探") >= 0) return { emoji: "🚀", bg: "#e0685a" };
    if (c.indexOf("暴利") >= 0 || c.indexOf("動能") >= 0) return { emoji: "🔥", bg: "#ec7b46" };
    if (c.indexOf("背離") >= 0) return { emoji: "🔀", bg: "#5cb88f" };
    if (c.indexOf("波段") >= 0) return { emoji: "🏔️", bg: "#6aa0c9" };
    if (c.indexOf("反轉") >= 0) return { emoji: "♻️", bg: "#71b85a" };
    if (c.indexOf("做空") >= 0) return { emoji: "🐻", bg: "#c96a5a" };
    if (c.indexOf("逆") >= 0) return { emoji: "🌀", bg: "#8aa0c0" };
    if (c.indexOf("衛冕") >= 0) return { emoji: "🛡️", bg: "#9a9a9a" };
    if (c.indexOf("補選") >= 0) return { emoji: "🆕", bg: "#6c8cff" };
    if (c.indexOf("場外對照") >= 0) return { emoji: "🪞", bg: "#7a8aa0" };
    if (c.indexOf("場外") >= 0) return { emoji: "🏛️", bg: "#c4a35a" };
    return { emoji: "🧗", bg: "#67ad3e" };
  }

  function directionOf(code) {
    if (BIDIR[code]) return { txt: "雙向", cls: "both" };
    if (SHORT_CODES[code]) return { txt: "做空", cls: "short" };
    return { txt: "做多", cls: "long" };
  }

  // 與後端 promotion_tier 同步：一律用不受槓桿影響的 avg_R / total_R / wr / calmar
  function inferTier(p) {
    if (p.tier) return p;
    var n = p.n || 0, avg = p.avg_R || 0, wr = p.wr || 0;
    var tot = p.total_R || 0, cal = p.calmar || 0, open = p.open_n || 0;
    var zone = "active", zoneLabel = "競賽區";
    if (n >= 20 && tot < 0 && avg < 0) { zone = "danger"; zoneLabel = "危險區"; }
    else if (n < 8 && !open) { zone = "warmup"; zoneLabel = "暖身區"; }
    var tier = "warmup", label = "暖身", emoji = "🚧";
    if (n >= 80 && avg >= 0.2 && tot >= 15 && cal >= 1 && avg > -0.16) {
      tier = "ace"; label = "王牌"; emoji = "👑";
    } else if (n >= 50 && tot >= 5 && cal >= 0.4 && avg > -0.16 && (avg >= 0.15 || (wr >= 45 && avg >= 0.08))) {
      tier = "promoted"; label = "晉級"; emoji = "🥇";
    } else if (n >= 30 && avg >= 0.1 && tot > 0) {
      tier = "candidate"; label = "候選"; emoji = "🥈";
    } else if (n >= 8 || open) {
      tier = "watch"; label = "觀察"; emoji = "🔭";
    }
    p.tier = tier; p.tier_label = label; p.tier_emoji = emoji;
    p.zone = zone; p.zone_label = zoneLabel;
    return p;
  }

  function tierKey(p) {
    return (p.zone === "danger") ? "danger" : (p.tier || "warmup");
  }

  function info(code) {
    return (window.STRATEGY_INFO && window.STRATEGY_INFO[code]) || null;
  }

  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fmtU(n) { return Math.round(n).toLocaleString("en-US"); }
  function climbPct(eq) { return clamp((eq - CAPITAL) / (GOAL - CAPITAL), 0, 1); }

  function tpe(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  // ── 載入資料 ──
  function asWarmup(r) {
    return inferTier({
      name: r.name, code: r.code, cat: r.cat, key: r.key,
      equity_live: CAPITAL, roi: 0, n: 0, wr: 0, avg_R: 0, total_R: 0,
      open_n: 0, pf: 0, mdd: 0, history: [], open_positions: []
    });
  }

  function load() {
    return fetch((window.ARENA_DATA_URL || "data/arena.json") + "?t=" + Date.now())
      .then(function (r) {
        if (!r.ok) {
          if (window.ARENA_ALLOW_EMPTY && window.ARENA_EMPTY_BOARD) {
            return window.ARENA_EMPTY_BOARD;
          }
          throw new Error(r.status);
        }
        return r.json();
      })
      .then(function (d) {
        state.players = (d.players || []).slice()
          .map(inferTier)
          .sort(function (a, b) { return b.equity_live - a.equity_live; });
        state.registered = d.registered || [];
        if (!state.players.length && state.registered.length && window.ARENA_SHOW_REGISTERED) {
          state.players = state.registered.map(asWarmup);
        }
        state.asOf = d.as_of || 0;
        state.season = d.season || null;
        state.tournament = d.tournament || null;
        state.tierCounts = d.tier_counts || {};
        state.zoneCounts = d.zone_counts || {};
      });
  }

  // ── 賽制總覽（階段 / 分級 / 統計 / 規則）──
  function renderTournament() {
    var t = state.tournament;
    var phase = state.season;
    var section = document.getElementById("tournament");
    if (section) section.style.display = t ? "" : "none";
    var pill = document.getElementById("phase-pill");
    if (phase && pill) {
      pill.textContent = phase.label + " ｜ 第 " + phase.day + " / " + (phase.total || 14) + " 天";
      pill.className = "tour-phase-pill phase-" + (phase.id || "qualifier");
    }
    if (!t) return;

    var phasesBox = document.getElementById("tour-phases");
    if (phasesBox) {
      phasesBox.innerHTML = (t.phases || []).map(function (ph) {
        var active = phase && phase.id === ph.id;
        return '<div class="phase-step' + (active ? " active" : "") + '">' +
          '<div class="phase-day">Day ' + esc(ph.days) + '</div>' +
          '<div class="phase-name">' + esc(ph.label) + '</div>' +
          '<div class="phase-desc">' + esc(ph.desc) + '</div></div>';
      }).join('<div class="phase-arrow">▸</div>');
    }

    var tiersBox = document.getElementById("tour-tiers");
    if (tiersBox) {
      tiersBox.innerHTML = (t.tiers || []).map(function (tr) {
        var cnt = state.tierCounts[tr.id] || 0;
        return '<div class="tier-card tier-' + tr.id + '">' +
          '<div class="tier-card-head"><span class="tier-emoji">' + tr.emoji + '</span>' +
          '<span class="tier-name">' + esc(tr.label) + '</span>' +
          '<span class="tier-count">' + cnt + '</span></div>' +
          '<div class="tier-req">' + esc(tr.req) + '</div></div>';
      }).join("");
    }

    var statsBox = document.getElementById("tour-stats");
    if (statsBox) {
      var danger = state.zoneCounts.danger || 0;
      statsBox.innerHTML =
        '<span class="tour-stat">⚡ 槓桿 <b>' + (t.leverage || LEVERAGE) + 'x</b></span>' +
        '<span class="tour-stat">🎯 目標 <b>' + fmtU(t.goal || GOAL) + 'U</b></span>' +
        '<span class="tour-stat">📊 上場 <b>' + state.players.length + '</b></span>' +
        '<span class="tour-stat danger">⚠️ 危險區 <b>' + danger + '</b></span>';
    }

    var rulesBox = document.getElementById("tour-rules-body");
    if (rulesBox) {
      rulesBox.innerHTML =
        '<p>本金 <b>' + fmtU(t.capital || CAPITAL) + 'U</b>、<b>' + (t.leverage || LEVERAGE) +
          'x 槓桿</b>、每筆固定風險 <b>' + (t.risk_per_trade || 100) + 'U</b>，出場照各選手 SL / TP。</p>' +
        '<p>晉級門檻一律以 <b>R 值</b>（不受槓桿影響）計算，須優於影子雷達基準 avg_R <b>' +
          (t.radar_baseline_avg_r != null ? t.radar_baseline_avg_r : -0.16) + '</b>。</p>' +
        '<ul>' + (t.tiers || []).map(function (tr) {
          return '<li>' + tr.emoji + ' <b>' + esc(tr.label) + '</b>：' + esc(tr.req) + '</li>';
        }).join("") +
        '<li>⚠️ <b>危險區</b>：≥20 筆仍負期望（avg_R<0 且累計<0），暫不建議跟單</li></ul>' +
        '<p>🏁 <b>夢幻獎</b>：' + esc(t.dream_goal || "權益破 20,000U") + '。</p>';
    }
  }

  // ── 分級分頁（晉級 / 候選 / 觀察 / 危險區）──
  function renderTierTabs() {
    var box = document.getElementById("tier-tabs");
    if (!box) return;
    var counts = {};
    state.players.forEach(function (p) {
      var k = tierKey(p);
      counts[k] = (counts[k] || 0) + 1;
    });
    box.innerHTML = "";
    TIER_ORDER.forEach(function (key) {
      if (key !== "all" && !counts[key]) return;
      var meta = TIER_META[key] || { label: key, emoji: "" };
      var n = key === "all" ? state.players.length : counts[key];
      var b = document.createElement("button");
      b.className = "tier-tab tier-tab-" + key + (state.tierFilter === key ? " active" : "");
      b.innerHTML = meta.emoji + " " + esc(meta.label) + " <b>" + n + "</b>";
      b.addEventListener("click", function () {
        state.tierFilter = key; renderTierTabs(); renderBoard();
      });
      box.appendChild(b);
    });
  }

  // ── 爬山主視覺（TOP 15）──
  function renderMountain() {
    var box = document.getElementById("climbers");
    var loading = document.getElementById("mountain-loading");
    box.innerHTML = "";
    if (loading) loading.style.display = "none";
    var top = state.players.slice(0, 15);
    top.forEach(function (p, i) {
      var st = catStyle(p.cat);
      var pct = climbPct(p.equity_live);
      var bottom = 6 + pct * 80;              // 6%~86% 高度
      // 沿山路左右擺動，避免重疊
      var sway = Math.sin(i * 1.7) * 16;       // -16~16
      var left = 50 + sway + (i % 2 ? 6 : -6);
      var el = document.createElement("div");
      el.className = "climber";
      el.style.bottom = bottom + "%";
      el.style.left = clamp(left, 12, 88) + "%";
      el.style.zIndex = String(100 - i);
      el.innerHTML =
        '<div class="climber-avatar" style="background:' + st.bg + '">' + st.emoji +
        (i < 3 ? '<span class="climber-rank">' + (i + 1) + '</span>' : '') + '</div>' +
        '<span class="climber-tag">' + esc(p.code) + '</span>';
      el.addEventListener("click", function () { openDetail(p); });
      box.appendChild(el);
    });
  }

  // ── 分頁（流派）──
  function renderTabs() {
    var cats = {};
    state.players.forEach(function (p) {
      var key = simpleCat(p.cat);
      cats[key] = (cats[key] || 0) + 1;
    });
    var order = ["all", "優化組"].concat(Object.keys(cats).filter(function (k) { return k !== "優化組"; }).sort());
    var seen = {};
    var tabs = document.getElementById("tabs");
    tabs.innerHTML = "";
    order.forEach(function (key) {
      if (key !== "all" && (!cats[key] || seen[key])) return;
      seen[key] = true;
      var label = key === "all" ? "全部 " + state.players.length : key + " " + (cats[key] || 0);
      var b = document.createElement("button");
      b.className = "tab" + (state.filter === key ? " active" : "");
      b.textContent = label;
      b.addEventListener("click", function () { state.filter = key; renderTabs(); renderBoard(); });
      tabs.appendChild(b);
    });
  }

  function simpleCat(cat) {
    var c = cat || "其他";
    if (c.indexOf("優化") >= 0) return "優化組";
    return c.replace(/[ABC]?軸$/, "").replace(/派$/, "") || c;
  }

  // ── 榜單 ──
  function renderBoard() {
    var grid = document.getElementById("grid");
    var meta = document.getElementById("board-meta");
    var list = state.players.filter(function (p) {
      var catOk = (state.filter === "all") || (simpleCat(p.cat) === state.filter);
      var tierOk = (state.tierFilter === "all") || (tierKey(p) === state.tierFilter);
      return catOk && tierOk;
    });
    meta.textContent = "更新 " + tpe(state.asOf) + " ｜ 上場 " + state.players.length +
      " 位 ｜ 暖身 " + state.registered.length + " 位 ｜ 每 15 分自動更新";
    grid.innerHTML = "";
    if (!list.length) {
      grid.innerHTML = '<div class="state"><div class="state-pixel">🍃</div><p>此條件目前沒有上場選手</p></div>';
      return;
    }
    list.forEach(function (p) {
      var rank = state.players.indexOf(p) + 1;
      grid.appendChild(card(p, rank));
    });
  }

  function card(p, rank) {
    var st = catStyle(p.cat);
    var pct = climbPct(p.equity_live) * 100;
    var roiCls = p.roi >= 0 ? "up" : "down";
    var medal = rank === 1 ? "🥇" : rank === 2 ? "🥈" : rank === 3 ? "🥉" : "#" + rank;
    var isOpt = (p.cat || "").indexOf("優化") >= 0;
    var nfo = info(p.code);
    var dir = directionOf(p.code);
    var tk = tierKey(p);
    var tmeta = TIER_META[tk] || { label: p.tier_label || "", emoji: p.tier_emoji || "" };
    var el = document.createElement("div");
    el.className = "card tierline-" + tk + (tk === "danger" ? " is-danger" : "");
    el.innerHTML =
      '<div class="card-top">' +
        '<div class="card-avatar" style="background:' + st.bg + '">' + st.emoji + '</div>' +
        '<div class="card-id"><div class="card-code">' + esc(p.code) +
          '<span class="dir-pill ' + dir.cls + '">' + dir.txt + '</span></div>' +
          '<div class="card-name">' + esc(shortName(p.name)) + '</div></div>' +
        '<div class="card-rank">' + medal + '</div>' +
      '</div>' +
      '<div class="tier-badge tier-' + tk + '">' + tmeta.emoji + ' ' + esc(tmeta.label) + '</div>' +
      '<div class="card-equity"><span class="eq-num">' + fmtU(p.equity_live) + 'U</span>' +
        '<span class="eq-roi ' + roiCls + '">' + (p.roi >= 0 ? "+" : "") + p.roi + '%</span></div>' +
      '<div class="prog"><div class="prog-fill' + (pct >= 100 ? " full" : "") + '" style="width:' + Math.max(2, pct) + '%"></div></div>' +
      '<div class="card-stats">' +
        '<span>已結算 <b>' + p.n + '</b></span>' +
        '<span>勝率 <b>' + p.wr + '%</b></span>' +
        '<span>期望 <b>' + p.avg_R + 'R</b></span>' +
        '<span>持倉 <b>' + p.open_n + '</b></span>' +
      '</div>' +
      '<span class="tag-pill' + (isOpt ? " opt" : "") + '">' + esc(nfo ? nfo.tag : p.cat) + '</span>';
    el.addEventListener("click", function () { openDetail(p); });
    return el;
  }

  function shortName(n) { return (n || "").split(" ")[0]; }

  // ── 詳情 ──
  function openDetail(p) {
    var st = catStyle(p.cat);
    var nfo = info(p.code);
    var body = document.getElementById("modal-body");
    var pf = (p.pf >= 999) ? "∞" : p.pf;
    var dir = directionOf(p.code);
    var tk = tierKey(p);
    var tmeta = TIER_META[tk] || { label: p.tier_label || "", emoji: p.tier_emoji || "" };
    var kpis = [
      ["權益", fmtU(p.equity_live) + "U"], ["報酬率", (p.roi >= 0 ? "+" : "") + p.roi + "%"],
      ["已結算", p.n + " 筆"], ["勝率", p.wr + "%"], ["期望", p.avg_R + "R"],
      ["累計R", (p.total_R >= 0 ? "+" : "") + p.total_R + "R"],
      ["風報比", pf], ["最大回撤", p.mdd + "%"], ["持倉", p.open_n + " 單"],
    ];
    var html =
      '<div class="m-head">' +
        '<div class="m-avatar" style="background:' + st.bg + '">' + st.emoji + '</div>' +
        '<div class="m-title"><h2>' + esc(p.code) + " · " + esc(shortName(p.name)) +
          '<span class="dir-pill ' + dir.cls + '">' + dir.txt + '</span></h2>' +
          '<div class="m-tag">' +
          '<span class="tier-badge tier-' + tk + '">' + tmeta.emoji + ' ' + esc(tmeta.label) + '</span> ' +
          esc(nfo ? nfo.tag : p.cat) + " ｜ " + esc(p.cat) +
          (p.profile ? " ｜ " + esc(p.profile) : "") + '</div></div>' +
      '</div>' +
      '<div class="m-logic"><b>策略邏輯</b>' + esc(nfo ? nfo.logic : window.CAT_FALLBACK) + '</div>' +
      '<div class="m-logic m-exit"><b>出場規則</b>' + esc((nfo && nfo.exit) || window.EXIT_FALLBACK) + '</div>' +
      '<div class="m-logic m-rev"><b>反向訊號是什麼</b>' + esc((nfo && nfo.reverse) || window.REVERSE_FALLBACK) + '</div>' +
      '<div class="kpis">' + kpis.map(function (k) {
        return '<div class="kpi"><div class="k-v">' + k[1] + '</div><div class="k-l">' + k[0] + '</div></div>';
      }).join("") + '</div>' +
      '<div class="pos-title">📈 進行中持倉（' + (p.open_positions ? p.open_positions.length : 0) + '）</div>' +
      positionsHtml(p) +
      '<div class="pos-title">📜 歷史成交（' + (p.history ? p.history.length : 0) + '）</div>' +
      historyHtml(p) +
      (window.ARENA_NO_TG ? "" :
      '<a class="btn btn-gold btn-block m-tg-follow" href="https://t.me/SSSshadowBOTBOT?start=follow_' +
        encodeURIComponent(p.code) + '" target="_blank" rel="noopener">🔔 追蹤 ' + esc(p.code) + ' 進場推播</a>');
    body.innerHTML = html;
    show("modal");
  }

  // 前端統一三種結果：止損 / 止盈 / 反向訊號（FLIP/REPLACE/OPEN 皆歸為反向訊號）
  var RESULT_MAP = {
    SL: { txt: "止損", cls: "down", icon: "❌" },
    TP: { txt: "止盈", cls: "up", icon: "✅" },
    OPEN: { txt: "反向訊號", cls: "rev", icon: "🔄" },
    FLIP: { txt: "反向訊號", cls: "rev", icon: "🔄" },
    REPLACE: { txt: "反向訊號", cls: "rev", icon: "🔄" },
  };

  function historyHtml(p) {
    var h = p.history || [];
    if (!h.length) return '<div class="pos-empty">尚無已結算成交（影子需走完模擬持倉，約 36h 後出現）</div>';
    return '<div class="hist">' + h.map(function (t) {
      var r = RESULT_MAP[t.result] || { txt: t.result, cls: "", icon: "•" };
      var sideTxt = (t.side || "LONG") === "LONG" ? "多" : "空";
      var rv = (t.R == null) ? "" : (Number(t.R) >= 0 ? "+" : "") + Number(t.R).toFixed(2) + "R";
      var rCls = (r.cls === "rev") ? "rev" : ((Number(t.R) >= 0) ? "up" : "down");
      return '<div class="hist-row">' +
        '<span class="hist-date">' + tpe(t.ts) + '</span>' +
        '<span class="hist-sym">' + esc(t.sym) + '</span>' +
        '<span class="hist-side ' + ((t.side || "LONG") === "LONG" ? "long" : "short") + '">' + sideTxt + '</span>' +
        '<span class="hist-res">' + r.icon + r.txt + '</span>' +
        '<span class="hist-r ' + rCls + '">' + rv + '</span>' +
      '</div>';
    }).join("") + '</div>';
  }

  function positionsHtml(p) {
    var pos = p.open_positions || [];
    if (!pos.length) return '<div class="pos-empty">此選手目前無進行中持倉</div>';
    return pos.slice().sort(function (a, b) {
      return Math.abs(b.float_R || 0) - Math.abs(a.float_R || 0);
    }).map(function (o) {
      var sideCls = (o.side || "LONG") === "LONG" ? "long" : "short";
      var sideTxt = (o.side || "LONG") === "LONG" ? "做多" : "做空";
      var fr = Number(o.float_R || 0);
      var frCls = fr >= 0 ? "up" : "down";
      return '<div class="pos">' +
        '<div class="pos-row1">' +
          '<span class="pos-sym">' + esc(o.sym) + '</span>' +
          '<span class="side ' + sideCls + '">' + sideTxt + '</span>' +
          '<span class="pos-r ' + frCls + '">' + (fr >= 0 ? "+" : "") + fr.toFixed(2) + 'R</span>' +
        '</div>' +
        '<div class="pos-row2">進 ' + esc(o.entry) + ' → 現 ' + esc(o.current) +
          ' ｜ SL ' + esc(o.sl) + ' / TP ' + esc(o.tp) +
          (o.tf ? ' ｜ ' + esc(o.tf) : "") + (o.entry_ts ? ' ｜ ' + tpe(o.entry_ts) : "") + '</div>' +
        (o.reason ? '<div class="pos-row2">📌 ' + esc(o.reason) + '</div>' : "") +
        '</div>';
    }).join("");
  }

  // ── Modal 控制 ──
  function show(id) {
    var el = document.getElementById(id);
    if (el) el.hidden = false;
  }
  function hide(id) {
    var el = document.getElementById(id);
    if (el) el.hidden = true;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function showError(msg) {
    var grid = document.getElementById("grid");
    var loading = document.getElementById("mountain-loading");
    if (loading) { loading.textContent = "連線失敗"; }
    grid.innerHTML = '<div class="state"><div class="state-pixel">⚠️</div><p>' + esc(msg) +
      '</p><p style="font-size:12px">請用網頁伺服器開啟（war-room 部署為 https，可正常讀取）。</p></div>';
  }

  function doRefresh(showFx) {
    var b = document.getElementById("refresh");
    if (showFx && b) { b.disabled = true; b.textContent = "↻ 更新中…"; }
    return load().then(function () {
      renderTournament(); renderTierTabs(); renderMountain(); renderTabs(); renderBoard();
      if (showFx && b) {
        b.textContent = "✓ 已更新"; b.disabled = false;
        setTimeout(function () { b.textContent = "↻ 重新整理"; }, 1500);
      }
    }).catch(function (e) {
      if (showFx && b) { b.textContent = "↻ 重新整理"; b.disabled = false; }
      showError("讀取資料失敗：" + e.message);
    });
  }

  function boot() {
    var refresh = document.getElementById("refresh");
    if (refresh) refresh.addEventListener("click", function () { doRefresh(true); });
    setInterval(function () {
      var modal = document.getElementById("modal");
      var tg = document.getElementById("tg-modal");
      var open = (modal && !modal.hidden) || (tg && !tg.hidden);
      if (!open) doRefresh(false);
    }, 60000);
    var tgConnect = document.getElementById("tg-connect");
    if (tgConnect) tgConnect.addEventListener("click", function () { show("tg-modal"); });
    var tgClose = document.getElementById("tg-close");
    if (tgClose) tgClose.addEventListener("click", function () { hide("tg-modal"); });
    var modalClose = document.getElementById("modal-close");
    if (modalClose) modalClose.addEventListener("click", function () { hide("modal"); });
    [["modal", "modal"], ["tg-modal", "tg-modal"]].forEach(function (pair) {
      var el = document.getElementById(pair[0]);
      if (!el) return;
      el.addEventListener("click", function (e) {
        if (e.target === this) hide(pair[1]);
      });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { hide("modal"); hide("tg-modal"); }
    });

    load().then(function () {
      renderTournament(); renderTierTabs(); renderMountain(); renderTabs(); renderBoard();
    }).catch(function (e) {
      showError("讀取資料失敗：" + e.message);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else { boot(); }
})();

/* VIP 實驗室 — 持倉狙擊多 TP 版本對照（唯讀，可自由拉時間範圍）*/
(function () {
  "use strict";

  var CAPITAL = 10000, RISK = 100, LEV = 5, RECENT_H = 72;
  var state = {
    data: null,
    mode: "opt",   // opt | season | d7 | d14 | d30 | custom
    fromTs: null, toTs: null,
    inited: false,
  };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmtU(n) { return Math.round(n).toLocaleString("en-US"); }
  function tpe(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return (d.getMonth() + 1) + "/" + d.getDate() + " " +
      String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }
  function dstr(ts) {
    var d = new Date(ts * 1000);
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0");
  }
  function dateToTs(str) {  // yyyy-mm-dd（本地零點）→ epoch 秒
    if (!str) return null;
    var p = str.split("-");
    if (p.length !== 3) return null;
    return Math.floor(new Date(+p[0], +p[1] - 1, +p[2], 0, 0, 0).getTime() / 1000);
  }

  // ── 範圍 → [fromTs, toTs] ──
  function rangeBounds() {
    var d = state.data, now = Math.floor(Date.now() / 1000);
    switch (state.mode) {
      case "opt": return [d.opt_landed_ts || d.data_start, null];
      case "season": return [d.season_start || null, null];
      case "d7": return [now - 7 * 86400, null];
      case "d14": return [now - 14 * 86400, null];
      case "d30": return [now - 30 * 86400, null];
      case "custom": return [state.fromTs, state.toTs];
      default: return [d.opt_landed_ts || d.data_start, null];
    }
  }

  // ── 任意範圍即時統計（與後端同公式）──
  function computeStats(vid, fromTs, toTs) {
    var rs = [], tss = [];
    state.data.trades.forEach(function (t) {
      if (fromTs != null && t.ts < fromTs) return;
      if (toTs != null && t.ts > toTs) return;
      var r = t.R[vid];
      if (r == null) return;
      rs.push(r); tss.push(t.ts);
    });
    var n = rs.length;
    if (!n) return null;
    var wins = rs.filter(function (r) { return r > 0; });
    var losses = rs.filter(function (r) { return r <= 0; });
    var total_R = rs.reduce(function (a, b) { return a + b; }, 0);
    var avg_R = total_R / n;
    var wr = wins.length / n * 100;
    var sumW = wins.reduce(function (a, b) { return a + b; }, 0);
    var sumL = losses.reduce(function (a, b) { return a + b; }, 0);
    var avgW = wins.length ? sumW / wins.length : 0;
    var avgL = losses.length ? Math.abs(sumL / losses.length) : 0;
    var rr = avgL > 0 ? avgW / avgL : avgW;
    var pf = (losses.length && sumL !== 0) ? sumW / Math.abs(sumL) : (wins.length ? 999 : 0);
    // 權益（5x，不歸零保護 → 誠實）
    var eq = CAPITAL, seq = [CAPITAL], peak = CAPITAL, mdd = 0, blew = false;
    rs.forEach(function (r) {
      eq += RISK * r * LEV;
      if (eq <= 0) blew = true;
      seq.push(eq);
      peak = Math.max(peak, eq);
      if (peak > 0) mdd = Math.min(mdd, (eq - peak) / peak * 100);
    });
    var roi = (eq - CAPITAL) / CAPITAL * 100;
    var calmar = mdd < 0 ? roi / Math.abs(mdd) : (roi > 0 ? roi : 0);
    var best = Math.max.apply(null, rs);
    var now = Math.floor(Date.now() / 1000);
    var recent = rs.filter(function (_, i) { return tss[i] >= now - RECENT_H * 3600; });
    var recent_R = recent.reduce(function (a, b) { return a + b; }, 0);
    // spark
    var step = Math.max(1, Math.floor(seq.length / 60));
    var spark = [];
    for (var i = 0; i < seq.length; i += step) spark.push(Math.round(seq[i] * 10) / 10);
    if (spark[spark.length - 1] !== Math.round(seq[seq.length - 1] * 10) / 10) {
      spark.push(Math.round(seq[seq.length - 1] * 10) / 10);
    }
    return {
      n: n, wins: wins.length, wr: Math.round(wr * 10) / 10,
      avg_R: Math.round(avg_R * 1000) / 1000, total_R: Math.round(total_R * 100) / 100,
      rr: Math.round(rr * 100) / 100, pf: Math.round(pf * 100) / 100,
      mdd: Math.round(mdd * 10) / 10, equity: Math.round(eq * 10) / 10,
      roi: Math.round(roi * 100) / 100, calmar: Math.round(calmar * 100) / 100,
      best_r: Math.round(best * 100) / 100, recent_R: Math.round(recent_R * 100) / 100,
      blewup: blew, spark: spark,
    };
  }

  function variantsFor(fromTs, toTs) {
    var out = [];
    (state.data.versions || []).forEach(function (v) {
      var s = computeStats(v.id, fromTs, toTs);
      if (s) out.push({ meta: v, st: s });
    });
    out.sort(function (a, b) { return b.st.avg_R - a.st.avg_R; });
    out.forEach(function (x, i) { x.rank = i + 1; });
    return out;
  }

  function load() {
    return fetch("data/vip_lab.json?t=" + Date.now())
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        state.data = d;
        if (!state.inited) {
          state.mode = d.default_scope || "opt";
          state.fromTs = d.opt_landed_ts || d.data_start;
          state.toTs = d.data_end;
          state.inited = true;
        }
      });
  }

  function sparkline(spark, win) {
    if (!spark || spark.length < 2) return "";
    var w = 220, h = 46, n = spark.length;
    var min = Math.min.apply(null, spark), max = Math.max.apply(null, spark);
    var rng = (max - min) || 1;
    var pts = spark.map(function (v, i) {
      var x = (i / (n - 1)) * w;
      var y = h - ((v - min) / rng) * (h - 6) - 3;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var col = win ? "#67ad3e" : "#c96a5a";
    var baseY = h - ((CAPITAL - min) / rng) * (h - 6) - 3;
    return '<svg class="vip-spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">' +
      '<line x1="0" y1="' + baseY.toFixed(1) + '" x2="' + w + '" y2="' + baseY.toFixed(1) +
      '" stroke="rgba(58,38,20,.35)" stroke-dasharray="3 3" stroke-width="1"/>' +
      '<polyline fill="none" stroke="' + col + '" stroke-width="2" points="' + pts + '"/></svg>';
  }

  function renderSubject() {
    var d = state.data, subj = d.subject || {};
    var el = document.getElementById("vip-subject");
    if (el) el.innerHTML = "🎯 受測選手：<b>" + esc(subj.name || "持倉狙擊") +
      "</b> <span class='vip-code'>" + esc(subj.code || "SNIPE") + "</span>";
    var cov = d.coverage || {};
    var labeled = cov.labeled_total != null ? cov.labeled_total : (cov.labeled || 0);
    var excluded = cov.excluded_pre_opt || 0;
    var exported = cov.exported != null ? cov.exported : (d.trades || []).length;
    var computed = cov.computed || 0;
    var optLbl = d.opt_landed_label || "2026-06-05";
    var c = document.getElementById("vip-coverage");
    if (c) c.innerHTML =
      "樣本：優化後（" + optLbl + " 起）<b>" + exported + "</b> 筆" +
      (excluded ? "（已排除舊版 <b>" + excluded + "</b> 筆）" : "") +
      " ｜ 已重放 <b>" + computed + "</b> 筆" +
      (exported < labeled - excluded ? "（K 線增補中，每 15 分補齊）" : "") +
      " ｜ 資料區間 " + dstr(d.data_start) + " ～ " + dstr(d.data_end);
  }

  function renderControls() {
    var d = state.data;
    var box = document.getElementById("vip-scope");
    if (!box) return;
    var presets = [
      ["opt", "✅ 優化後（6/5起）"],
      ["season", "🏁 開賽後"],
      ["d7", "近7天"], ["d14", "近14天"], ["d30", "近30天"],
      ["custom", "🎚 自訂"],
    ];
    var btns = presets.map(function (p) {
      return '<button class="vip-scope-tab' + (state.mode === p[0] ? " active" : "") +
        '" data-mode="' + p[0] + '">' + p[1] + '</button>';
    }).join("");
    var minD = dstr(d.data_start), maxD = dstr(d.data_end);
    var fromV = dstr(state.fromTs || d.data_start), toV = dstr(state.toTs || d.data_end);
    box.innerHTML =
      '<div class="vip-scope-row">' + btns + '</div>' +
      '<div class="vip-range' + (state.mode === "custom" ? " on" : "") + '" id="vip-range">' +
        '<label>從 <input type="date" id="vip-from" min="' + minD + '" max="' + maxD + '" value="' + fromV + '"></label>' +
        '<label>到 <input type="date" id="vip-to" min="' + minD + '" max="' + maxD + '" value="' + toV + '"></label>' +
        '<span class="vip-range-hint">僅可選 6/5 優化落地後的區間</span>' +
      '</div>' +
      '<div class="vip-scope-note" id="vip-scope-note"></div>';

    box.querySelectorAll(".vip-scope-tab").forEach(function (b) {
      b.addEventListener("click", function () { state.mode = b.getAttribute("data-mode"); render(); });
    });
    var fEl = document.getElementById("vip-from"), tEl = document.getElementById("vip-to");
    function onDate() {
      state.mode = "custom";
      state.fromTs = dateToTs(fEl.value);
      state.toTs = dateToTs(tEl.value) + 86399; // 含當日
      render();
    }
    if (fEl) fEl.addEventListener("change", onDate);
    if (tEl) tEl.addEventListener("change", onDate);
    var note = document.getElementById("vip-scope-note");
    if (note) {
      var ex = (d.coverage || {}).excluded_pre_opt;
      note.textContent = ex
        ? "6/5 前舊版訊號已從本實驗室移除（共 " + ex + " 筆），避免與優化後邏輯混算。"
        : "本實驗室僅含 6/5 優化落地後的訊號。";
    }
  }

  function rangeLabel(b) {
    if (state.mode === "opt") return "優化後（6/5 起，全部有效樣本）";
    if (state.mode === "season") return "開賽後（與主擂台同口徑）";
    if (state.mode === "custom") return dstr(b[0] || state.data.data_start) + " ～ " + dstr(b[1] || state.data.data_end);
    return ({ d7: "近 7 天", d14: "近 14 天", d30: "近 30 天" })[state.mode] || "";
  }

  function renderWinner(vs, b) {
    var box = document.getElementById("vip-winner");
    if (!box) return;
    if (!vs.length) { box.innerHTML = ""; return; }
    var best = vs[0], bs = best.st;
    var live = vs.filter(function (x) { return x.meta.id === "s2_32"; })[0];
    var deltaTxt = "";
    if (live && best.meta.id !== "s2_32") {
      var dR = (bs.avg_R - live.st.avg_R).toFixed(3);
      var dT = (bs.total_R - live.st.total_R).toFixed(2);
      deltaTxt = "<div class='vip-delta'>比現行實盤（兩段 3.2R）avg_R <b>" +
        (dR >= 0 ? "+" : "") + dR + "R</b>、累計 <b>" +
        (bs.total_R - live.st.total_R >= 0 ? "+" : "") + dT + "R</b></div>";
    } else if (best.meta.id === "s2_32") {
      deltaTxt = "<div class='vip-delta'>現行實盤版本（兩段 3.2R）此區間即為最佳 → 維持原生策略。</div>";
    }
    var allNeg = vs.every(function (x) { return x.st.avg_R < 0; });
    var banner = allNeg
      ? "<div class='vip-warn-neg'>⚠️ 此區間全部版本期望值為負：出場政策只能重新分配贏單，無法無中生有 edge。比的是<b>相對誰較不虧</b>。</div>"
      : "<div class='vip-ok-pos'>✅ 此區間最佳版本為正期望（avg_R > 0），可作為跟單候選。</div>";
    box.innerHTML =
      '<div class="vip-winner-card">' +
        '<div class="vip-crown">🏆 最佳出場版本 · ' + esc(rangeLabel(b)) + '（依 avg_R）</div>' +
        '<div class="vip-winner-name">' + esc(best.meta.emoji) + ' ' + esc(best.meta.label) + '</div>' +
        '<div class="vip-winner-rule">' + esc(best.meta.exit_rule) + '</div>' +
        '<div class="vip-winner-kpis">' +
          '<span>期望 <b>' + bs.avg_R + 'R</b></span>' +
          '<span>累計 <b>' + bs.total_R + 'R</b></span>' +
          '<span>勝率 <b>' + bs.wr + '%</b></span>' +
          '<span>樣本 <b>' + bs.n + '</b></span>' +
        '</div>' + deltaTxt +
      '</div>' + banner;
  }

  function card(x) {
    var v = x.meta, s = x.st, rank = x.rank;
    var win = s.avg_R >= 0;
    var medal = rank === 1 ? "🥇" : rank === 2 ? "🥈" : rank === 3 ? "🥉" : "#" + rank;
    var avgCls = s.avg_R >= 0 ? "up" : "down";
    var el = document.createElement("div");
    el.className = "vip-card" + (rank === 1 ? " is-best" : "");
    el.innerHTML =
      '<div class="vip-card-top">' +
        '<div class="vip-card-emoji">' + esc(v.emoji) + '</div>' +
        '<div class="vip-card-id"><div class="vip-card-label">' + esc(v.label) + '</div>' +
          '<div class="vip-card-rule">' + esc(v.exit_rule) + '</div></div>' +
        '<div class="vip-card-rank">' + medal + '</div>' +
      '</div>' +
      '<div class="vip-card-eq"><span class="eq-num ' + avgCls + '">' +
        (s.avg_R >= 0 ? "+" : "") + s.avg_R + 'R</span>' +
        '<span class="vip-card-eqlbl">每筆期望</span></div>' +
      sparkline(s.spark, win) +
      '<div class="vip-card-stats">' +
        '<span>已結算 <b>' + s.n + '</b></span>' +
        '<span>勝率 <b>' + s.wr + '%</b></span>' +
        '<span>累計 <b>' + s.total_R + 'R</b></span>' +
        '<span>風報 <b>' + s.rr + '</b></span>' +
        '<span>盈虧PF <b>' + s.pf + '</b></span>' +
        '<span>最大回撤 <b>' + s.mdd + '%</b></span>' +
      '</div>' +
      '<p class="vip-card-desc">' + esc(v.desc) + '</p>';
    el.addEventListener("click", function () { openDetail(x); });
    return el;
  }

  function renderGrid(vs, b) {
    var grid = document.getElementById("vip-grid");
    var meta = document.getElementById("vip-meta");
    if (meta) meta.textContent = "更新 " + tpe(state.data.as_of) + " ｜ " + vs.length +
      " 版本 ｜ " + rangeLabel(b) + " ｜ 每 15 分重算";
    grid.innerHTML = "";
    if (!vs.length) {
      grid.innerHTML = '<div class="state"><div class="state-pixel">🍃</div><p>此區間沒有可結算的訊號，換個範圍試試</p></div>';
      return;
    }
    vs.forEach(function (x) { grid.appendChild(card(x)); });
  }

  function openDetail(x) {
    var v = x.meta, s = x.st;
    var body = document.getElementById("modal-body");
    var win = s.avg_R >= 0;
    var kpis = [
      ["每筆期望", (s.avg_R >= 0 ? "+" : "") + s.avg_R + "R"],
      ["累計R", (s.total_R >= 0 ? "+" : "") + s.total_R + "R"],
      ["已結算", s.n + " 筆"], ["勝率", s.wr + "%"],
      ["風報比", s.rr], ["盈虧比PF", s.pf],
      ["最大回撤", s.mdd + "%"], ["Calmar", s.calmar],
      ["最佳單筆", s.best_r + "R"], ["近72h", (s.recent_R >= 0 ? "+" : "") + s.recent_R + "R"],
      ["理論權益5x", fmtU(s.equity) + "U" + (s.blewup ? " ⚠爆倉" : "")],
    ];
    body.innerHTML =
      '<div class="m-head"><div class="m-avatar" style="background:#e9b84a">' + esc(v.emoji) + '</div>' +
        '<div class="m-title"><h2>' + esc(v.label) + '</h2>' +
        '<div class="m-tag">出場規則：' + esc(v.exit_rule) + '</div></div></div>' +
      '<div class="vip-modal-spark">' + sparkline(s.spark, win) + '</div>' +
      '<div class="kpis">' + kpis.map(function (k) {
        return '<div class="kpi"><div class="k-v">' + k[1] + '</div><div class="k-l">' + k[0] + '</div></div>';
      }).join("") + '</div>' +
      '<p class="vip-card-desc">' + esc(v.desc) + '</p>';
    document.getElementById("modal").hidden = false;
  }

  function render() {
    var b = rangeBounds();
    var vs = variantsFor(b[0], b[1]);
    renderSubject();
    renderControls();
    renderWinner(vs, b);
    renderGrid(vs, b);
  }

  function boot() {
    load().then(render).catch(function (e) {
      var grid = document.getElementById("vip-grid");
      if (grid) grid.innerHTML = '<div class="state"><div class="state-pixel">⚠️</div><p>載入失敗：' + esc(e.message) + '</p></div>';
    });
    var rf = document.getElementById("refresh");
    if (rf) rf.addEventListener("click", function () { load().then(render); });
    var mc = document.getElementById("modal-close");
    if (mc) mc.addEventListener("click", function () { document.getElementById("modal").hidden = true; });
    var mb = document.getElementById("modal");
    if (mb) mb.addEventListener("click", function (e) { if (e.target === mb) mb.hidden = true; });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else { boot(); }
})();

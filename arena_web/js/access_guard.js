"use strict";

(function () {
  var STORAGE_KEY = "jackbot.private.access.v1";
  var EXPIRE_HOURS = 12;
  var PASS_PLAIN = "@Jacky87084";
  var PAGE_TITLE = "JackBot 私密入口";
  document.documentElement.style.visibility = "hidden";

  function readSession() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
  }

  function isValidSession() {
    var s = readSession();
    if (!s || !s.t || s.k !== "ok") return false;
    return Date.now() - s.t < EXPIRE_HOURS * 3600 * 1000;
  }

  function saveSession() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ k: "ok", t: Date.now() }));
  }

  function lockScroll(lock) {
    document.documentElement.style.overflow = lock ? "hidden" : "";
    document.body.style.overflow = lock ? "hidden" : "";
  }

  function mountGate() {
    if (isValidSession()) return;

    var style = document.createElement("style");
    style.textContent =
      ".jb-guard{position:fixed;inset:0;z-index:99999;background:#0b0c11;display:flex;align-items:center;justify-content:center;padding:18px}" +
      ".jb-card{width:min(460px,100%);background:#141725;border:1px solid #3b4260;border-radius:14px;padding:24px;color:#eef2ff;" +
      "font-family:system-ui,-apple-system,'Segoe UI',Arial,sans-serif;box-shadow:0 20px 55px rgba(0,0,0,.45)}" +
      ".jb-card h1{margin:0 0 10px;font-size:22px;line-height:1.25}" +
      ".jb-sub{margin:0 0 16px;color:#b7bfd7;font-size:14px;line-height:1.65}" +
      ".jb-row{display:flex;gap:8px;flex-wrap:wrap}" +
      ".jb-input{flex:1 1 220px;background:#0e111b;border:1px solid #2d3552;color:#fff;border-radius:10px;padding:10px 12px;font-size:15px}" +
      ".jb-btn{background:#4f75ff;border:1px solid #6e92ff;color:#fff;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}" +
      ".jb-btn:hover{filter:brightness(1.06)}" +
      ".jb-err{margin-top:10px;min-height:20px;color:#ff9f9f;font-size:13px}";
    document.head.appendChild(style);

    var root = document.createElement("div");
    root.className = "jb-guard";
    root.innerHTML =
      "<div class='jb-card'>" +
      "<h1>" + PAGE_TITLE + "</h1>" +
      "<p class='jb-sub'>此頁面已改為私密模式，需密碼登入後才能查看。</p>" +
      "<div class='jb-row'>" +
      "<input id='jb-pass' class='jb-input' type='password' placeholder='請輸入密碼' />" +
      "<button id='jb-login' class='jb-btn' type='button'>登入</button>" +
      "</div>" +
      "<div id='jb-err' class='jb-err'></div>" +
      "</div>";
    document.body.appendChild(root);
    lockScroll(true);

    var input = document.getElementById("jb-pass");
    var btn = document.getElementById("jb-login");
    var err = document.getElementById("jb-err");

    function submit() {
      if ((input.value || "") === PASS_PLAIN) {
        saveSession();
        root.remove();
        lockScroll(false);
        return;
      }
      err.textContent = "密碼錯誤，請再試一次。";
      input.value = "";
      input.focus();
    }

    btn.addEventListener("click", submit);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") submit();
    });
    input.focus();
  }

  function revealPage() {
    document.documentElement.style.visibility = "";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      mountGate();
      revealPage();
    });
  } else {
    mountGate();
    revealPage();
  }
})();

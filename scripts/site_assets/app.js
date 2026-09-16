/* ==========================================================================
   文档站前端逻辑：侧边栏、全文搜索、代码复制、深浅色、目录高亮
   数据来自 build_site.py 生成的 assets/nav-data.js（window.SITE）
   ========================================================================== */
(function () {
  "use strict";

  var DATA = window.SITE || { nav: [], search: [], repo: "", branch: "master" };
  var BASE = (document.body && document.body.dataset.base) || "";
  var PAGE = (document.body && document.body.dataset.page) || "";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ------------------------------------------------------------ 主题 */
  var THEME_KEY = "cpr-theme";
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    var btn = $("#themeBtn");
    if (btn) {
      btn.textContent = t === "dark" ? "☀" : "☾";
      btn.title = t === "dark" ? "切换到浅色" : "切换到深色";
    }
  }
  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem(THEME_KEY); } catch (e) { /* 隐私模式 */ }
    if (!saved) {
      saved = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light";
    }
    applyTheme(saved);
    var btn = $("#themeBtn");
    if (btn) {
      btn.addEventListener("click", function () {
        var now = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        applyTheme(now);
        try { localStorage.setItem(THEME_KEY, now); } catch (e) { }
      });
    }
  }

  /* --------------------------------------------------------- 侧边栏 */
  function normUrl(u) {
    return String(u || "").replace(/^\.\//, "");
  }

  function buildSidebar() {
    var box = $("#sidebar");
    if (!box) return;
    var html = "";
    DATA.nav.forEach(function (g) {
      var items = g.items;
      if (!items.length) return;
      html += '<div class="nav-group"><div class="ng-title">' + esc(g.group) + "</div>";
      items.forEach(function (it) {
        var active = normUrl(it.url) === PAGE ? " active" : "";
        html += '<a class="nav-link' + active + '" href="' + BASE + it.url + '" data-title="' +
          esc(it.title) + '">' +
          (it.num ? '<span class="nl-num">' + esc(it.num) + "</span> " : "") +
          esc(it.label || it.title) + "</a>";
      });
      html += "</div>";
    });
    box.innerHTML = html;
    var cur = $(".nav-link.active", box);
    if (cur && cur.scrollIntoView) {
      var r = cur.getBoundingClientRect();
      if (r.top < 80 || r.bottom > window.innerHeight - 40) {
        cur.scrollIntoView({ block: "center" });
      }
    }
  }

  /* ----------------------------------------------------------- 搜索 */
  function flatten() {
    var out = [];
    (DATA.search || []).forEach(function (p) {
      out.push({ title: p.title, group: p.group, url: p.url, head: "", id: "" });
      (p.heads || []).forEach(function (h) {
        out.push({ title: p.title, group: p.group, url: p.url, head: h.t, id: h.id });
      });
    });
    return out;
  }

  function mark(text, q) {
    if (!q) return esc(text);
    var i = text.toLowerCase().indexOf(q);
    if (i < 0) return esc(text);
    return esc(text.slice(0, i)) + "<mark>" + esc(text.slice(i, i + q.length)) +
      "</mark>" + esc(text.slice(i + q.length));
  }

  function score(entry, q) {
    var t = entry.title.toLowerCase();
    var h = (entry.head || "").toLowerCase();
    if (t.indexOf(q) >= 0) return t.indexOf(q) === 0 ? 3 : 2;
    if (h.indexOf(q) >= 0) return 1;
    if ((entry.group || "").toLowerCase().indexOf(q) >= 0) return 0.5;
    return -1;
  }

  function initSearch() {
    var input = $("#searchInput");
    var panel = $("#searchResults");
    if (!input || !panel) return;
    var ALL = flatten();
    var items = [];
    var cursor = -1;

    function close() { panel.classList.remove("show"); cursor = -1; }

    function run(q) {
      q = q.trim().toLowerCase();
      if (!q) { close(); return; }
      var hits = [];
      for (var i = 0; i < ALL.length; i++) {
        var s = score(ALL[i], q);
        if (s > 0) hits.push({ e: ALL[i], s: s });
      }
      hits.sort(function (a, b) { return b.s - a.s; });
      hits = hits.slice(0, 40);
      items = hits;
      if (!hits.length) {
        panel.innerHTML = '<div class="sr-empty">没有匹配的章节<br><span style="font-size:12px">' +
          "试试「缺失值」「舍入」「merge」「Agent」</span></div>";
        panel.classList.add("show");
        return;
      }
      var html = "";
      hits.forEach(function (h) {
        var e = h.e;
        html += '<a class="sr-item" href="' + BASE + e.url + (e.id ? "#" + encodeURIComponent(e.id) : "") + '">' +
          '<span class="sr-group">' + esc(e.group) + "</span>" +
          '<span class="sr-title">' + mark(e.title, q) + "</span>" +
          (e.head ? ' <span class="sr-head">› ' + mark(e.head, q) + "</span>" : "") +
          "</a>";
      });
      panel.innerHTML = html;
      panel.classList.add("show");
    }

    input.addEventListener("input", function () { run(input.value); });
    input.addEventListener("focus", function () { if (input.value.trim()) run(input.value); });

    input.addEventListener("keydown", function (ev) {
      var nodes = $$(".sr-item", panel);
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        if (!nodes.length) return;
        cursor = ev.key === "ArrowDown"
          ? (cursor + 1) % nodes.length
          : (cursor - 1 + nodes.length) % nodes.length;
        nodes.forEach(function (n, i) { n.classList.toggle("active", i === cursor); });
        nodes[cursor].scrollIntoView({ block: "nearest" });
      } else if (ev.key === "Enter") {
        var pick = nodes[cursor >= 0 ? cursor : 0];
        if (pick) { window.location.href = pick.getAttribute("href"); }
      } else if (ev.key === "Escape") {
        input.value = ""; close(); input.blur();
      }
    });

    document.addEventListener("click", function (ev) {
      if (!panel.contains(ev.target) && ev.target !== input) close();
    });

    // 「/」或 Ctrl+K 聚焦搜索
    document.addEventListener("keydown", function (ev) {
      var tag = (ev.target.tagName || "").toLowerCase();
      var typing = tag === "input" || tag === "textarea" || ev.target.isContentEditable;
      if ((ev.key === "/" && !typing) || ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k")) {
        ev.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  /* ------------------------------------------------------ 代码块增强 */
  function enhanceCode() {
    $$(".codeblock").forEach(function (block) {
      var head = $(".cb-head", block);
      var code = $("pre code", block);
      if (!head || !code) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "cb-copy";
      btn.textContent = "复制";
      btn.addEventListener("click", function () {
        var text = code.innerText;
        var done = function () {
          btn.textContent = "已复制";
          btn.classList.add("done");
          setTimeout(function () {
            btn.textContent = "复制";
            btn.classList.remove("done");
          }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { fallback(text, done); });
        } else { fallback(text, done); }
      });
      head.appendChild(btn);
    });

    function fallback(text, done) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); done(); } catch (e) { /* 忽略 */ }
      document.body.removeChild(ta);
    }
  }

  /* ------------------------------------------------- 表格 / 标题锚点 */
  function enhanceTables() {
    $$(".markdown table").forEach(function (tb) {
      if (tb.parentElement && tb.parentElement.classList.contains("table-wrap")) return;
      var wrap = document.createElement("div");
      wrap.className = "table-wrap";
      tb.parentNode.insertBefore(wrap, tb);
      wrap.appendChild(tb);
    });
  }

  function enhanceHeadings() {
    var scope = $("#content");
    if (!scope) return;
    $$("h2[id], h3[id], h4[id]", scope).forEach(function (h) {
      var a = document.createElement("a");
      a.className = "anchor";
      a.href = "#" + encodeURIComponent(h.id);
      a.textContent = "#";
      a.setAttribute("aria-label", "本节链接");
      h.appendChild(a);
    });
  }

  /* ------------------------------------------------------- 右目录 */
  function buildToc() {
    var box = $("#toc");
    if (!box || !DATA.search || !DATA.search.length) return;
    var url = PAGE;
    var heads = [];
    DATA.search.forEach(function (p) {
      if (normUrl(p.url) === url) heads = p.heads || [];
    });
    if (!heads.length) { box.style.display = "none"; return; }
    var html = '<div class="toc-title">本页目录</div>';
    heads.forEach(function (h) {
      html += '<a class="' + (h.lv === 3 ? "lv3" : "") + '" href="#' +
        encodeURIComponent(h.id) + '">' + esc(h.t) + "</a>";
    });
    box.innerHTML = html;

    var links = $$("a", box);
    var targets = links.map(function (a) {
      var id = decodeURIComponent(a.getAttribute("href").slice(1));
      return document.getElementById(id);
    });
    var ticks = false;
    function sync() {
      ticks = false;
      var best = -1;
      for (var i = 0; i < targets.length; i++) {
        var el = targets[i];
        if (!el) continue;
        if (el.getBoundingClientRect().top <= 130) best = i;
      }
      links.forEach(function (a, i) { a.classList.toggle("active", i === best); });
    }
    window.addEventListener("scroll", function () {
      if (ticks) return;
      ticks = true;
      requestAnimationFrame(sync);
    }, { passive: true });
    sync();
  }

  /* ------------------------------------------------------ 移动端抽屉 */
  function initDrawer() {
    var btn = $("#menuBtn");
    var side = $("#sidebar");
    var ov = $("#overlay");
    if (!btn || !side || !ov) return;
    function set(open) {
      side.classList.toggle("open", open);
      ov.classList.toggle("show", open);
      document.body.style.overflow = open ? "hidden" : "";
    }
    btn.addEventListener("click", function () { set(!side.classList.contains("open")); });
    ov.addEventListener("click", function () { set(false); });
    $$(".nav-link", side).forEach(function (a) {
      a.addEventListener("click", function () { set(false); });
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 1024) set(false);
    });
  }

  /* ------------------------------------------------------ 回到顶部 */
  function initToTop() {
    var btn = $("#toTop");
    if (!btn) return;
    btn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
    var ticks = false;
    window.addEventListener("scroll", function () {
      if (ticks) return;
      ticks = true;
      requestAnimationFrame(function () {
        ticks = false;
        btn.classList.toggle("show", window.scrollY > 700);
      });
    }, { passive: true });
  }

  /* ---------------------------------------------------------- 启动 */
  function boot() {
    initTheme();
    buildSidebar();
    enhanceTables();
    enhanceHeadings();
    enhanceCode();
    initSearch();
    buildToc();
    initDrawer();
    initToTop();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();

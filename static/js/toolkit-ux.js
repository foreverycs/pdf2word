/**
 * Shared UX helpers: recent tools, friendly errors, result panel,
 * command palette, busy guard, job tray, keyboard shortcuts.
 * Loaded on home + tool pages (independent of ToolkitUpload).
 */
(function (global) {
  "use strict";

  var RECENT_KEY = "toolkit_recent_v1";
  var RECENT_MAX = 6;
  var JOBS_KEY = "toolkit_jobs_v1";
  var JOBS_MAX = 12;
  var JOBS_TTL_MS = 2 * 60 * 60 * 1000;
  var THEME_KEY = "toolkit_theme_v1";

  var busyDepth = 0;
  var paletteState = null;

  /* —— Friendly error messages —— */
  var ERROR_RULES = [
    { re: /413|too large|文件过大|超过.*MB|Payload Too Large/i, msg: "文件太大，请压缩后再试，或拆成更小的文件。" },
    { re: /429|rate limit|Too Many|请求过于频繁|限流/i, msg: "请求过于频繁，请稍等片刻再试。" },
    { re: /503|engine|LibreOffice|未检测到|无可用引擎|conversion engine/i, msg: "转换引擎暂不可用。请安装 LibreOffice，或联系管理员检查服务状态。" },
    { re: /Tesseract|OCR/i, msg: "OCR 不可用。请安装 Tesseract 后重试，或关闭 OCR 选项。" },
    { re: /网络错误|Failed to fetch|NetworkError|ERR_NETWORK/i, msg: "网络异常，请检查连接后重试。" },
    { re: /超时|timeout|Timeout/i, msg: "处理超时。可缩小页数、关闭 OCR，或检查反向代理超时设置后重试。" },
    { re: /已取消|abort/i, msg: "已取消。" },
    {
      re: /任务不存在|Job not found|workers\s*1|多 worker|多进程|结果文件已过期|任务尚未完成|已过期.*重启/i,
      msg:
        "转换任务已失效或不存在。可能因服务重启、任务过期，或部署了多个 worker。请重新提交；异步任务请使用 --workers 1。",
    },
    { re: /仅支持|不支持|invalid|格式/i, msg: null }, // keep original when specific
    { re: /403|禁用|disabled|未启用/i, msg: "该工具已关闭，请从首页选择其他工具，或联系管理员。" },
    { re: /404|不存在/i, msg: "接口不存在或页面已移动，请返回首页重试。" },
    { re: /500|Internal Server/i, msg: "服务处理失败，请稍后重试。若持续出现请联系管理员。" },
  ];

  var JOB_MISSING_MSG =
    "转换任务已失效或不存在。可能因服务重启、任务过期，或部署了多个 worker。请重新提交；异步任务请使用 --workers 1。";

  function friendlyError(raw) {
    var s = raw == null ? "" : String(raw);
    if (!s) return "操作失败，请重试。";
    for (var i = 0; i < ERROR_RULES.length; i++) {
      var rule = ERROR_RULES[i];
      if (rule.re.test(s) && rule.msg) return rule.msg;
    }
    // Strip leading "Error: " / HTTP codes noise when message is already Chinese-ish
    s = s.replace(/^Error:\s*/i, "");
    if (/^HTTP\s*\d+/.test(s) && s.length < 16) {
      return "请求失败（" + s + "），请稍后重试。";
    }
    return s;
  }

  /* —— Toast —— */
  var toastHost = null;
  function ensureToastHost() {
    if (toastHost && document.body.contains(toastHost)) return toastHost;
    toastHost = document.createElement("div");
    toastHost.className = "toolkit-toast-host";
    toastHost.setAttribute("aria-live", "polite");
    document.body.appendChild(toastHost);
    return toastHost;
  }

  /**
   * @param {string} message
   * @param {'ok'|'err'|'warn'|'info'} [kind]
   * @param {number} [ms]
   */
  function toast(message, kind, ms) {
    var text = message == null ? "" : String(message);
    if (!text) return;
    var host = ensureToastHost();
    var el = document.createElement("div");
    el.className = "toolkit-toast kind-" + (kind || "info");
    el.textContent = kind === "err" ? friendlyError(text) : text;
    host.appendChild(el);
    var life = typeof ms === "number" ? ms : kind === "err" ? 3200 : 2200;
    setTimeout(function () {
      el.style.opacity = "0";
      el.style.transition = "opacity 0.18s ease";
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 200);
    }, life);
  }

  /**
   * Copy text and toast. Returns Promise&lt;boolean&gt;.
   */
  function copyText(text, okMsg) {
    var t = text == null ? "" : String(text);
    if (!t) {
      toast("没有可复制的内容", "warn");
      return Promise.resolve(false);
    }
    var done = function () {
      toast(okMsg || "已复制到剪贴板", "ok");
      return true;
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(t).then(done).catch(function () {
        return legacyCopy(t) ? done() : (toast("复制失败，请手动选择", "err"), false);
      });
    }
    return Promise.resolve(
      legacyCopy(t) ? done() : (toast("复制失败，请手动选择", "err"), false)
    );
  }

  function legacyCopy(t) {
    try {
      var ta = document.createElement("textarea");
      ta.value = t;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return !!ok;
    } catch (e) {
      return false;
    }
  }

  /**
   * Bind paste of files/images onto a zone (or document).
   * @param {{
   *   target?: HTMLElement|Document,
   *   onFiles: (File[]) => void,
   *   accept?: (File) => boolean,
   *   enabled?: () => boolean
   * }} cfg
   */
  function bindPasteFiles(cfg) {
    var target = cfg.target || document;
    var onFiles = cfg.onFiles;
    var accept = cfg.accept || function () {
      return true;
    };
    var enabled = cfg.enabled || function () {
      return true;
    };
    target.addEventListener("paste", function (e) {
      if (!enabled()) return;
      var cd = e.clipboardData;
      if (!cd) return;
      var files = [];
      if (cd.files && cd.files.length) {
        files = Array.prototype.slice.call(cd.files);
      } else if (cd.items) {
        for (var i = 0; i < cd.items.length; i++) {
          var it = cd.items[i];
          if (it.kind === "file") {
            var f = it.getAsFile();
            if (f) files.push(f);
          }
        }
      }
      files = files.filter(accept);
      if (!files.length) return;
      e.preventDefault();
      onFiles(files);
      toast(
        files.length === 1
          ? "已从剪贴板粘贴文件"
          : "已从剪贴板粘贴 " + files.length + " 个文件",
        "ok"
      );
    });
  }

  /* —— Recent tools —— */
  function readRecent() {
    try {
      var raw = localStorage.getItem(RECENT_KEY);
      var list = raw ? JSON.parse(raw) : [];
      return Array.isArray(list) ? list : [];
    } catch (e) {
      return [];
    }
  }

  function writeRecent(list) {
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
    } catch (e) {
      /* quota / private mode */
    }
  }

  /**
   * @param {{slug:string,name?:string,route?:string,icon?:string,description?:string,accent?:string}} entry
   */
  function recordRecent(entry) {
    if (!entry || !entry.slug) return;
    var slug = String(entry.slug);
    var list = readRecent().filter(function (x) {
      return x && x.slug !== slug;
    });
    list.unshift({
      slug: slug,
      name: entry.name || slug,
      route: entry.route || "",
      icon: entry.icon || "🔧",
      description: entry.description || "",
      accent: entry.accent || "indigo",
      ts: Date.now(),
    });
    writeRecent(list);
  }

  function clearRecent() {
    try {
      localStorage.removeItem(RECENT_KEY);
    } catch (e) {}
  }

  /* —— Result panel —— */
  /**
   * @param {HTMLElement|null} el
   * @param {{
   *   kind?: 'ok'|'err'|'warn'|'info',
   *   title?: string,
   *   detail?: string,
   *   filename?: string,
   *   onAgain?: () => void,
   *   againLabel?: string,
   *   downloadBlob?: Blob,
   *   downloadName?: string
   * }} opts
   */
  function showResult(el, opts) {
    if (!el) return;
    opts = opts || {};
    var kind = opts.kind || "info";
    el.hidden = false;
    el.className = "tool-result show kind-" + kind;
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", kind === "err" ? "assertive" : "polite");

    var title =
      opts.title ||
      (kind === "ok" ? "完成" : kind === "err" ? "出错了" : kind === "warn" ? "注意" : "提示");
    var detail = opts.detail || "";
    if (kind === "err") detail = friendlyError(detail);

    var html = "";
    html += '<div class="tool-result-inner">';
    html += '<div class="tool-result-mark" aria-hidden="true">' +
      (kind === "ok" ? "✓" : kind === "err" ? "!" : kind === "warn" ? "!" : "i") +
      "</div>";
    html += '<div class="tool-result-body">';
    html += "<div class=\"tool-result-title\">" + escapeHtml(title) + "</div>";
    if (detail) {
      html += "<div class=\"tool-result-detail\">" + escapeHtml(detail) + "</div>";
    }
    if (opts.filename) {
      html +=
        '<div class="tool-result-file"><span class="tool-result-file-label">文件</span> ' +
        escapeHtml(opts.filename) +
        "</div>";
    }
    html += '<div class="tool-result-actions">';
    if (opts.downloadBlob && opts.downloadName) {
      html +=
        '<button type="button" class="tool-result-btn primary" data-act="redownload">再次下载</button>';
    }
    if (typeof opts.onAgain === "function") {
      html +=
        '<button type="button" class="tool-result-btn" data-act="again">' +
        escapeHtml(opts.againLabel || "继续处理") +
        "</button>";
    }
    html += "</div></div></div>";
    el.innerHTML = html;

    var blob = opts.downloadBlob;
    var dname = opts.downloadName;
    var onAgain = opts.onAgain;
    el.onclick = function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;
      var act = t.getAttribute("data-act");
      if (act === "redownload" && blob && dname) {
        if (global.ToolkitUpload && global.ToolkitUpload.saveBlob) {
          global.ToolkitUpload.saveBlob(blob, dname);
        } else {
          var a = document.createElement("a");
          var href = URL.createObjectURL(blob);
          a.href = href;
          a.download = dname;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(function () {
            URL.revokeObjectURL(href);
          }, 2000);
        }
      }
      if (act === "again" && typeof onAgain === "function") {
        hideResult(el);
        onAgain();
      }
    };
  }

  function hideResult(el) {
    if (!el) return;
    el.hidden = true;
    el.className = "tool-result";
    el.innerHTML = "";
    el.onclick = null;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /**
   * Prefer result panel; fall back to legacy #status text.
   */
  function setStatusOrResult(statusEl, resultEl, kind, text, resultOpts) {
    if (resultEl && (kind === "ok" || kind === "err" || kind === "warn")) {
      if (statusEl) {
        statusEl.textContent = "";
        statusEl.className = "";
      }
      showResult(
        resultEl,
        Object.assign({ kind: kind, detail: text }, resultOpts || {})
      );
      return;
    }
    if (resultEl && kind === "info") {
      hideResult(resultEl);
    }
    if (statusEl) {
      statusEl.className = kind || "";
      statusEl.textContent = kind === "err" ? friendlyError(text) : text || "";
    }
  }

  /* —— Auto: record recent on tool pages —— */
  function autoRecordFromBody() {
    var body = document.body;
    if (!body || !body.getAttribute) return;
    var slug = body.getAttribute("data-tool-slug");
    if (!slug) return;
    recordRecent({
      slug: slug,
      name: body.getAttribute("data-tool-name") || slug,
      route: body.getAttribute("data-tool-route") || location.pathname,
      icon: body.getAttribute("data-tool-icon") || "🔧",
      description: body.getAttribute("data-tool-desc") || "",
      accent: body.getAttribute("data-accent") || "indigo",
    });
  }

  /* —— Home: render recent strip —— */
  function appUrl(path) {
    var root = global.__ROOT__ || "";
    if (!path) return root || "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    return root ? root + path : path;
  }

  function catalogList() {
    var el = document.getElementById("tool-catalog-data");
    if (!el) return [];
    try {
      var list = JSON.parse(el.textContent || "[]");
      return Array.isArray(list) ? list : [];
    } catch (e) {
      return [];
    }
  }

  function resolveCatalog() {
    var map = {};
    catalogList().forEach(function (t) {
      if (t && t.slug) map[t.slug] = t;
    });
    return map;
  }

  /* —— Busy / leave guard —— */
  function setBusy(on, reason) {
    if (on) {
      busyDepth += 1;
    } else {
      busyDepth = Math.max(0, busyDepth - 1);
    }
    document.documentElement.classList.toggle("toolkit-busy", busyDepth > 0);
    if (busyDepth > 0) {
      document.documentElement.setAttribute(
        "data-busy-reason",
        reason || "working"
      );
    } else {
      document.documentElement.removeAttribute("data-busy-reason");
    }
  }

  function isBusy() {
    return busyDepth > 0;
  }

  function onBeforeUnload(e) {
    if (busyDepth <= 0) return;
    e.preventDefault();
    e.returnValue = "";
    return "";
  }

  /* —— Tracked async jobs (localStorage) —— */
  function readJobs() {
    try {
      var raw = localStorage.getItem(JOBS_KEY);
      var list = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(list)) return [];
      var now = Date.now();
      return list.filter(function (j) {
        return j && j.id && now - (j.created_at || 0) < JOBS_TTL_MS;
      });
    } catch (e) {
      return [];
    }
  }

  function writeJobs(list) {
    try {
      localStorage.setItem(JOBS_KEY, JSON.stringify(list.slice(0, JOBS_MAX)));
    } catch (e) {}
  }

  function trackJob(entry) {
    if (!entry || !entry.id) return;
    var list = readJobs().filter(function (j) {
      return j.id !== entry.id;
    });
    list.unshift(
      Object.assign(
        {
          status: "queued",
          progress: 0,
          message: "",
          created_at: Date.now(),
          updated_at: Date.now(),
        },
        entry
      )
    );
    writeJobs(list);
    renderJobsTray();
  }

  function updateTrackedJob(id, patch) {
    if (!id) return;
    var list = readJobs();
    var found = false;
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) {
        list[i] = Object.assign({}, list[i], patch, { updated_at: Date.now() });
        found = true;
        break;
      }
    }
    if (!found && patch) {
      list.unshift(
        Object.assign(
          { id: id, created_at: Date.now(), updated_at: Date.now() },
          patch
        )
      );
    }
    writeJobs(list);
    renderJobsTray();
  }

  function removeTrackedJob(id) {
    writeJobs(
      readJobs().filter(function (j) {
        return j.id !== id;
      })
    );
    renderJobsTray();
  }

  function clearFinishedJobs() {
    writeJobs(
      readJobs().filter(function (j) {
        return j.status === "queued" || j.status === "running";
      })
    );
    renderJobsTray();
  }

  function statusLabel(st) {
    if (st === "done") return "完成";
    if (st === "error") return "失败";
    if (st === "queued") return "排队";
    if (st === "running") return "进行中";
    return st || "—";
  }

  function ensureJobsTray() {
    var tray = document.getElementById("jobs-tray");
    if (tray) return tray;
    tray = document.createElement("div");
    tray.id = "jobs-tray";
    tray.className = "jobs-tray";
    tray.hidden = true;
    tray.innerHTML =
      '<button type="button" class="jobs-tray-toggle" id="jobs-tray-toggle" aria-expanded="false">' +
      '<span>任务</span><span class="jobs-count" id="jobs-tray-count">0</span></button>' +
      '<div class="jobs-tray-panel" id="jobs-tray-panel" hidden>' +
      '<div class="jobs-tray-head"><span>近期任务</span>' +
      '<button type="button" id="jobs-tray-clear">清除已完成</button></div>' +
      '<ul class="jobs-tray-list" id="jobs-tray-list"></ul></div>';
    document.body.appendChild(tray);

    document.getElementById("jobs-tray-toggle").addEventListener("click", function () {
      var panel = document.getElementById("jobs-tray-panel");
      var open = panel.hidden;
      panel.hidden = !open;
      this.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.getElementById("jobs-tray-clear").addEventListener("click", function () {
      clearFinishedJobs();
    });
    document.getElementById("jobs-tray-list").addEventListener("click", function (ev) {
      var t = ev.target;
      if (!t || !t.getAttribute) return;
      var act = t.getAttribute("data-job-act");
      var id = t.getAttribute("data-job-id");
      if (!act || !id) return;
      if (act === "dismiss") {
        removeTrackedJob(id);
        return;
      }
      if (act === "download") {
        var jobs = readJobs();
        var job = null;
        for (var i = 0; i < jobs.length; i++) {
          if (jobs[i].id === id) {
            job = jobs[i];
            break;
          }
        }
        if (!job || !job.download_url) return;
        var url =
          job.download_url.indexOf("http") === 0
            ? job.download_url
            : appUrl(job.download_url);
        if (global.ToolkitUpload && global.ToolkitUpload.downloadJob) {
          global.ToolkitUpload
            .downloadJob(url, job.download_name || "download.bin")
            .then(function () {
              updateTrackedJob(id, { downloaded: true, message: "已下载" });
            })
            .catch(function (err) {
              updateTrackedJob(id, {
                message: (err && err.message) || "下载失败",
              });
            });
        } else {
          window.open(url, "_blank");
        }
      }
    });
    return tray;
  }

  function renderJobsTray() {
    var jobs = readJobs();
    if (!jobs.length) {
      var existing = document.getElementById("jobs-tray");
      if (existing) existing.hidden = true;
      return;
    }
    var tray = ensureJobsTray();
    tray.hidden = false;
    var active = jobs.filter(function (j) {
      return j.status === "queued" || j.status === "running";
    }).length;
    var countEl = document.getElementById("jobs-tray-count");
    if (countEl) countEl.textContent = String(active || jobs.length);

    var list = document.getElementById("jobs-tray-list");
    if (!list) return;
    if (!jobs.length) {
      list.innerHTML = '<li class="jobs-tray-empty">暂无任务</li>';
      return;
    }
    var html = "";
    jobs.forEach(function (j) {
      var st = j.status || "queued";
      var pct =
        typeof j.progress === "number" && j.progress > 0 && j.progress <= 1
          ? Math.round(j.progress * 100) + "%"
          : "";
      var meta = j.message || "";
      if (pct && (st === "running" || st === "queued")) {
        meta = (meta ? meta + " · " : "") + pct;
      }
      if (j.error && st === "error") meta = j.error;
      html +=
        '<li class="jobs-tray-item">' +
        '<div class="jobs-tray-row">' +
        '<span class="jobs-tray-title">' +
        escapeHtml(j.title || j.tool || j.id) +
        "</span>" +
        '<span class="jobs-tray-st ' +
        escapeHtml(st) +
        '">' +
        escapeHtml(statusLabel(st)) +
        "</span></div>" +
        (meta
          ? '<div class="jobs-tray-meta">' + escapeHtml(meta) + "</div>"
          : "") +
        '<div class="jobs-tray-actions">';
      if (st === "done" && j.download_url && !j.downloaded) {
        html +=
          '<button type="button" class="primary" data-job-act="download" data-job-id="' +
          escapeHtml(j.id) +
          '">下载结果</button>';
      } else if (st === "done" && j.downloaded) {
        html +=
          '<button type="button" class="primary" data-job-act="download" data-job-id="' +
          escapeHtml(j.id) +
          '">再次下载</button>';
      }
      if (j.tool_route) {
        html +=
          '<a href="' +
          escapeHtml(appUrl(j.tool_route)) +
          '">打开工具</a>';
      }
      html +=
        '<button type="button" data-job-act="dismiss" data-job-id="' +
        escapeHtml(j.id) +
        '">移除</button></div></li>';
    });
    list.innerHTML = html;
  }

  function refreshActiveJobs() {
    var jobs = readJobs().filter(function (j) {
      return j.status === "queued" || j.status === "running";
    });
    if (!jobs.length) return;
    jobs.forEach(function (j) {
      var poll = j.poll_url || "/api/jobs/" + j.id;
      var url = poll.indexOf("http") === 0 ? poll : appUrl(poll);
      fetch(url, { credentials: "same-origin" })
        .then(function (r) {
          if (!r.ok) {
            if (r.status === 404) {
              updateTrackedJob(j.id, {
                status: "error",
                message: "任务已失效",
                error: JOB_MISSING_MSG,
              });
            }
            return null;
          }
          return r.json();
        })
        .then(function (job) {
          if (!job) return;
          updateTrackedJob(j.id, {
            status: job.status,
            progress: job.progress,
            message: job.message || job.status,
            download_url: job.download_url || j.download_url,
            download_name: job.download_name || j.download_name,
            error: job.error || null,
            tool: job.tool || j.tool,
          });
        })
        .catch(function () {
          /* ignore transient */
        });
    });
  }

  /* —— Command palette —— */
  function ensurePalette() {
    if (paletteState) return paletteState;
    var root = document.createElement("div");
    root.id = "cmd-palette";
    root.className = "cmd-palette";
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "搜索工具");
    root.innerHTML =
      '<div class="cmd-palette-panel">' +
      '<div class="cmd-palette-head">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>' +
      '<input class="cmd-palette-input" id="cmd-palette-input" type="search" placeholder="搜索工具名称、描述…" autocomplete="off" spellcheck="false" />' +
      '<span class="cmd-palette-hint">Esc 关闭</span></div>' +
      '<ul class="cmd-palette-list" id="cmd-palette-list" role="listbox"></ul>' +
      '<div class="cmd-palette-foot">' +
      "<span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>" +
      "<span><kbd>Enter</kbd> 打开</span>" +
      "<span><kbd>Ctrl</kbd><kbd>K</kbd> 开关</span></div></div>";
    document.body.appendChild(root);

    var input = document.getElementById("cmd-palette-input");
    var list = document.getElementById("cmd-palette-list");
    var active = 0;
    var items = [];
    var prevFocus = null;

    function focusables() {
      return [input].filter(function (el) {
        return el && !el.disabled;
      });
    }

    // Focus trap: keep Tab inside the dialog
    root.addEventListener("keydown", function (e) {
      if (root.hidden || e.key !== "Tab") return;
      var nodes = focusables();
      if (!nodes.length) return;
      e.preventDefault();
      nodes[0].focus();
    });

    function setActive(i) {
      if (!items.length) {
        active = 0;
        return;
      }
      active = (i + items.length) % items.length;
      list.querySelectorAll(".cmd-palette-item").forEach(function (el, idx) {
        el.setAttribute("aria-selected", idx === active ? "true" : "false");
      });
      var el = list.querySelectorAll(".cmd-palette-item")[active];
      if (el && el.scrollIntoView) {
        el.scrollIntoView({ block: "nearest" });
      }
    }

    function render(q) {
      var all = catalogList();
      var recent = readRecent();
      var recentSlugs = {};
      recent.forEach(function (r, idx) {
        if (r && r.slug) recentSlugs[r.slug] = idx;
      });
      var query = (q || "").trim().toLowerCase();
      var filtered = all.filter(function (t) {
        if (!t || !t.route) return false;
        if (!query) return true;
        var hay = (
          (t.name || "") +
          " " +
          (t.description || "") +
          " " +
          (t.slug || "") +
          " " +
          (t.category || "")
        ).toLowerCase();
        return hay.indexOf(query) !== -1;
      });
      if (!query) {
        filtered.sort(function (a, b) {
          var ra =
            recentSlugs[a.slug] !== undefined ? recentSlugs[a.slug] : 999;
          var rb =
            recentSlugs[b.slug] !== undefined ? recentSlugs[b.slug] : 999;
          return ra - rb;
        });
      }
      items = filtered.slice(0, 12);
      if (!items.length) {
        list.innerHTML =
          '<li class="cmd-palette-empty">没有匹配的工具</li>';
        return;
      }
      var html = "";
      items.forEach(function (t, idx) {
        html +=
          '<li class="cmd-palette-item" role="option" aria-selected="' +
          (idx === 0 ? "true" : "false") +
          '" data-idx="' +
          idx +
          '">' +
          '<span class="cmd-icon" aria-hidden="true">' +
          escapeHtml(t.icon || "🔧") +
          "</span>" +
          '<span class="cmd-body"><span class="cmd-name">' +
          escapeHtml(t.name || t.slug) +
          '</span><span class="cmd-desc">' +
          escapeHtml(t.description || t.slug || "") +
          "</span></span>" +
          (recentSlugs[t.slug] !== undefined
            ? '<span class="cmd-badge">最近</span>'
            : "") +
          "</li>";
      });
      list.innerHTML = html;
      active = 0;
    }

    function go(idx) {
      var t = items[idx];
      if (!t || !t.route) return;
      closePalette();
      location.href = appUrl(t.route);
    }

    input.addEventListener("input", function () {
      render(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive(active + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive(active - 1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        go(active);
      } else if (e.key === "Escape") {
        e.preventDefault();
        closePalette();
      }
    });
    list.addEventListener("click", function (e) {
      var li = e.target.closest(".cmd-palette-item");
      if (!li) return;
      go(parseInt(li.getAttribute("data-idx"), 10) || 0);
    });
    root.addEventListener("click", function (e) {
      if (e.target === root) closePalette();
    });

    paletteState = { root: root, input: input, render: render };
    return paletteState;
  }

  function openPalette() {
    var p = ensurePalette();
    p.prevFocus = document.activeElement;
    p.root.hidden = false;
    p.root.setAttribute("aria-hidden", "false");
    p.render("");
    p.input.value = "";
    setTimeout(function () {
      p.input.focus();
      p.input.select && p.input.select();
    }, 10);
  }

  function closePalette() {
    if (!paletteState) return;
    paletteState.root.hidden = true;
    paletteState.root.setAttribute("aria-hidden", "true");
    var prev = paletteState.prevFocus;
    paletteState.prevFocus = null;
    if (prev && typeof prev.focus === "function") {
      try {
        prev.focus();
      } catch (e) {}
    }
  }

  function togglePalette() {
    if (paletteState && !paletteState.root.hidden) closePalette();
    else openPalette();
  }

  /* —— Keyboard shortcuts —— */
  function isTypingTarget(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }

  function bindGlobalKeys() {
    document.addEventListener("keydown", function (e) {
      var mod = e.ctrlKey || e.metaKey;
      // Ctrl/Cmd+K — command palette
      if (mod && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        togglePalette();
        return;
      }
      // Escape — close palette, else clear result / status on tool pages
      if (e.key === "Escape") {
        if (paletteState && !paletteState.root.hidden) {
          e.preventDefault();
          closePalette();
          return;
        }
        var result = document.getElementById("result");
        if (result && result.classList.contains("show")) {
          hideResult(result);
          return;
        }
        var status = document.getElementById("status");
        if (status && status.textContent) {
          status.textContent = "";
          status.className = "";
        }
        return;
      }
      // Ctrl/Cmd+Enter — primary action (#btn)
      if (mod && e.key === "Enter") {
        if (paletteState && !paletteState.root.hidden) return;
        var btn = document.getElementById("btn");
        if (!btn || btn.disabled) return;
        // Allow from inputs (encode tools benefit); skip if button not visible
        if (btn.offsetParent === null) return;
        e.preventDefault();
        btn.click();
      }
    });
  }

  function bindPaletteButton() {
    var btn = document.getElementById("cmdPaletteBtn");
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener("click", function () {
        openPalette();
      });
      // Platform-ish kbd label
      var kbd = btn.querySelector(".topbar-kbd");
      if (kbd) {
        var isMac = /Mac|iPhone|iPad/.test(navigator.platform || "");
        kbd.textContent = isMac ? "⌘K" : "Ctrl+K";
      }
    }
  }

  /* —— Theme —— */
  function systemPrefersDark() {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e) {
      return false;
    }
  }

  function getTheme() {
    try {
      var t = localStorage.getItem(THEME_KEY);
      if (t === "light" || t === "dark") return t;
    } catch (e) {}
    return systemPrefersDark() ? "dark" : "light";
  }

  function applyTheme(theme, persist) {
    var t = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    if (persist) {
      try {
        localStorage.setItem(THEME_KEY, t);
      } catch (e) {}
    }
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", t === "dark" ? "#0b1220" : "#4f46e5");
    }
    var btn = document.getElementById("themeToggle");
    if (btn) {
      btn.setAttribute("aria-label", t === "dark" ? "切换为浅色" : "切换为深色");
      btn.title = t === "dark" ? "浅色模式" : "深色模式";
    }
  }

  function toggleTheme() {
    var next = getTheme() === "dark" ? "light" : "dark";
    applyTheme(next, true);
  }

  function bindThemeToggle() {
    var btn = document.getElementById("themeToggle");
    if (btn && !btn._bound) {
      btn._bound = true;
      btn.addEventListener("click", function () {
        toggleTheme();
      });
    }
    // Sync explicit attribute if only system preference is active
    if (!document.documentElement.getAttribute("data-theme")) {
      /* leave unset so CSS media query works; icon via tokens still ok */
    } else {
      applyTheme(document.documentElement.getAttribute("data-theme"), false);
    }
  }

  /* —— PWA service worker + update nudge —— */
  function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    // Only secure contexts (localhost / https)
    if (!window.isSecureContext) return;
    var swUrl = appUrl("/sw.js");
    var refreshing = false;
    window.addEventListener("load", function () {
      navigator.serviceWorker
        .register(swUrl, { scope: appUrl("/") || "/" })
        .then(function (reg) {
          // Already waiting worker from a previous visit
          if (reg.waiting) {
            toast("检测到界面更新，刷新页面即可使用新版本", "info", 5000);
          }
          reg.addEventListener("updatefound", function () {
            var nw = reg.installing;
            if (!nw) return;
            nw.addEventListener("statechange", function () {
              if (nw.state === "installed" && navigator.serviceWorker.controller) {
                toast("已下载新版本界面，刷新页面生效", "info", 5500);
              }
            });
          });
        })
        .catch(function () {
          /* ignore SW failures (file://, old browsers) */
        });
      navigator.serviceWorker.addEventListener("controllerchange", function () {
        if (refreshing) return;
        refreshing = true;
        // Soft nudge only — auto-reload can interrupt uploads
        toast("新版本已激活，建议刷新页面以加载最新样式", "info", 6000);
      });
    });
  }

  function renderRecentHome() {
    var host = document.getElementById("recent-tools");
    if (!host) return;
    var catalog = resolveCatalog();
    var recent = readRecent();
    var items = [];
    recent.forEach(function (r) {
      var c = catalog[r.slug] || r;
      if (!c || !c.route) return;
      items.push({
        slug: r.slug,
        name: c.name || r.name,
        route: c.route || r.route,
        icon: c.icon || r.icon || "🔧",
        description: c.description || r.description || "",
        accent: c.accent || r.accent || "indigo",
      });
    });

    var section = host.closest(".home-recent");
    if (!items.length) {
      if (section) section.hidden = true;
      host.innerHTML = "";
      return;
    }
    if (section) section.hidden = false;

    var html = "";
    items.forEach(function (t) {
      html +=
        '<a class="recent-card" href="' +
        escapeHtml(appUrl(t.route)) +
        '" data-accent="' +
        escapeHtml(t.accent) +
        '">' +
        '<span class="recent-icon" aria-hidden="true">' +
        escapeHtml(t.icon) +
        "</span>" +
        '<span class="recent-meta">' +
        '<span class="recent-name">' +
        escapeHtml(t.name) +
        "</span>" +
        (t.description
          ? '<span class="recent-desc">' + escapeHtml(t.description) + "</span>"
          : "") +
        "</span></a>";
    });
    host.innerHTML = html;

    var clearBtn = document.getElementById("recent-clear");
    if (clearBtn && !clearBtn._bound) {
      clearBtn._bound = true;
      clearBtn.addEventListener("click", function () {
        clearRecent();
        renderRecentHome();
      });
    }
  }

  function init() {
    autoRecordFromBody();
    renderRecentHome();
    bindGlobalKeys();
    bindPaletteButton();
    bindThemeToggle();
    registerServiceWorker();
    window.addEventListener("beforeunload", onBeforeUnload);
    renderJobsTray();
    // Resume polling for in-flight jobs after navigation
    refreshActiveJobs();
    setInterval(refreshActiveJobs, 4000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  global.ToolkitUX = {
    friendlyError: friendlyError,
    recordRecent: recordRecent,
    readRecent: readRecent,
    clearRecent: clearRecent,
    showResult: showResult,
    hideResult: hideResult,
    setStatusOrResult: setStatusOrResult,
    renderRecentHome: renderRecentHome,
    appUrl: appUrl,
    setBusy: setBusy,
    isBusy: isBusy,
    trackJob: trackJob,
    updateTrackedJob: updateTrackedJob,
    removeTrackedJob: removeTrackedJob,
    openPalette: openPalette,
    closePalette: closePalette,
    togglePalette: togglePalette,
    getTheme: getTheme,
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
    toast: toast,
    copyText: copyText,
    bindPasteFiles: bindPasteFiles,
  };
})(typeof window !== "undefined" ? window : globalThis);

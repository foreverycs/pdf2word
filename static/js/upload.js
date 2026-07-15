/**
 * Shared helpers for tool pages that upload files and download results.
 * Attach via: <script src="{{ static_url('/static/js/upload.js') }}"></script>
 */
(function (global) {
  "use strict";

  function fmtSize(n) {
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / (1024 * 1024)).toFixed(1) + " MB";
  }

  function downloadName(disposition, fallback) {
    if (!disposition) return fallback;
    var star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition);
    if (star) {
      try {
        return decodeURIComponent(star[1].replace(/\+/g, " "));
      } catch (e) {
        /* fall through */
      }
    }
    var ascii = /filename="?([^";]+)"?/i.exec(disposition);
    if (ascii) return ascii[1];
    return fallback;
  }

  function errDetail(detail) {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map(function (d) {
        return d.msg || JSON.stringify(d);
      }).join("; ");
    }
    return "请求失败";
  }

  /**
   * POST FormData with upload progress. Resolves with the XHR (responseType blob).
   *
   * @param {string} url
   * @param {FormData} formData
   * @param {{
   *   onProgress?: (pct:number, phase:'upload'|'done'|'process') => void,
   *   onStatus?: (text:string, cls?:string) => void,
   *   processHint?: string,
   *   longWaitSec?: number
   * }} [opts]
   * @returns {Promise<XMLHttpRequest>}
   */
  function xhrPost(url, formData, opts) {
    opts = opts || {};
    var processHint =
      opts.processHint ||
      "上传完成，正在转换… 大文件 / OCR 可能需要数分钟，请勿关闭页面";
    var longWaitSec = typeof opts.longWaitSec === "number" ? opts.longWaitSec : 20;
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      var processTimer = null;
      var tickTimer = null;
      var processStarted = 0;

      function clearTimers() {
        if (processTimer) {
          clearTimeout(processTimer);
          processTimer = null;
        }
        if (tickTimer) {
          clearInterval(tickTimer);
          tickTimer = null;
        }
      }

      function startProcessHints() {
        processStarted = Date.now();
        if (opts.onStatus) opts.onStatus(processHint, "info");
        if (opts.onProgress) opts.onProgress(78, "process");
        // Periodic “still working” hints so long conversions feel alive.
        tickTimer = setInterval(function () {
          var sec = Math.round((Date.now() - processStarted) / 1000);
          if (opts.onProgress) {
            // Creep slowly toward 95% while waiting (never claim 100% early).
            var pct = Math.min(95, 78 + Math.log10(1 + sec) * 8);
            opts.onProgress(pct, "process");
          }
          if (opts.onStatus && sec >= longWaitSec) {
            opts.onStatus(
              "仍在处理（已 " +
                sec +
                " 秒）… 复杂表格 / 扫描件 OCR 较慢，请继续等待",
              "info"
            );
          }
        }, 5000);
      }

      xhr.open("POST", url);
      xhr.responseType = "blob";
      // Client-side safety net; reverse proxies should still set ≥600s.
      xhr.timeout = typeof opts.timeoutMs === "number" ? opts.timeoutMs : 0;
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable && opts.onProgress) {
          opts.onProgress((e.loaded / e.total) * 70, "upload");
        }
        if (opts.onStatus && e.lengthComputable) {
          opts.onStatus(
            "上传中… " + Math.round((e.loaded / e.total) * 100) + "%",
            "info"
          );
        }
      };
      xhr.upload.onload = function () {
        if (opts.onProgress) opts.onProgress(75, "upload");
        startProcessHints();
      };
      xhr.onload = function () {
        clearTimers();
        if (opts.onProgress) opts.onProgress(100, "done");
        resolve(xhr);
      };
      xhr.onerror = function () {
        clearTimers();
        reject(new Error("网络错误"));
      };
      xhr.onabort = function () {
        clearTimers();
        reject(new Error("已取消"));
      };
      xhr.ontimeout = function () {
        clearTimers();
        reject(
          new Error(
            "请求超时。请检查反代 proxy_read_timeout（建议 ≥600s），或缩小页数 / 关闭 OCR 后重试"
          )
        );
      };
      xhr.send(formData);
    });
  }

  /**
   * Wire drag-and-drop + file input onto a drop zone.
   *
   * @param {{
   *   drop: HTMLElement,
   *   input: HTMLInputElement,
   *   onFiles: (FileList|File[]) => void,
   *   enabled?: () => boolean
   * }} cfg
   */
  function bindDropZone(cfg) {
    var drop = cfg.drop;
    var input = cfg.input;
    var onFiles = cfg.onFiles;
    var enabled = cfg.enabled || function () {
      return true;
    };

    input.addEventListener("change", function (e) {
      onFiles(e.target.files);
    });

    ["dragover", "dragenter"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault();
        if (enabled()) drop.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      drop.addEventListener(ev, function (e) {
        e.preventDefault();
        drop.classList.remove("drag");
      });
    });
    drop.addEventListener("drop", function (e) {
      if (!enabled()) return;
      onFiles(e.dataTransfer.files);
    });
  }

  /**
   * Trigger a browser download for a Blob.
   */
  function saveBlob(blob, filename) {
    var a = document.createElement("a");
    var href = URL.createObjectURL(blob);
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () {
      URL.revokeObjectURL(href);
    }, 2000);
  }

  /**
   * Parse error message from a failed XHR with blob response.
   * @returns {Promise<string>}
   */
  async function xhrErrorMessage(xhr) {
    var msg = "HTTP " + xhr.status;
    try {
      var text = await xhr.response.text();
      var err = JSON.parse(text);
      msg = errDetail(err.detail) || msg;
    } catch (e) {
      /* keep msg */
    }
    return msg;
  }

  /** Join app root path with an absolute path. */
  function appUrl(path) {
    var root = global.__ROOT__ || "";
    if (!path) return root || "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    return root ? root + path : path;
  }

  /**
   * POST FormData expecting a JSON body (e.g. 202 Accepted async job).
   *
   * @returns {Promise<{status:number, body:any, headers:Headers}>}
   */
  function xhrPostJson(url, formData, opts) {
    opts = opts || {};
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      xhr.responseType = "text";
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable && opts.onProgress) {
          opts.onProgress((e.loaded / e.total) * 70, "upload");
        }
        if (opts.onStatus && e.lengthComputable) {
          opts.onStatus(
            "上传中… " + Math.round((e.loaded / e.total) * 100) + "%",
            "info"
          );
        }
      };
      xhr.upload.onload = function () {
        if (opts.onProgress) opts.onProgress(72, "upload");
        if (opts.onStatus) {
          opts.onStatus(opts.processHint || "上传完成，任务已排队…", "info");
        }
      };
      xhr.onload = function () {
        var body = null;
        try {
          body = JSON.parse(xhr.responseText || "null");
        } catch (e) {
          body = { detail: xhr.responseText || "Invalid JSON" };
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(errDetail(body && body.detail) || "HTTP " + xhr.status));
          return;
        }
        resolve({ status: xhr.status, body: body, xhr: xhr });
      };
      xhr.onerror = function () {
        reject(new Error("网络错误"));
      };
      xhr.onabort = function () {
        reject(new Error("已取消"));
      };
      xhr.send(formData);
    });
  }

  /**
   * Poll ``/api/jobs/{id}`` until done/error or timeout.
   *
   * @param {string} pollUrl absolute or app-relative path
   * @param {{
   *   intervalMs?: number,
   *   timeoutMs?: number,
   *   onProgress?: (pct:number, phase:string) => void,
   *   onStatus?: (text:string, cls?:string) => void
   * }} [opts]
   * @returns {Promise<object>} final job JSON
   */
  function pollJob(pollUrl, opts) {
    opts = opts || {};
    var interval = opts.intervalMs || 1000;
    var timeout = typeof opts.timeoutMs === "number" ? opts.timeoutMs : 30 * 60 * 1000;
    var url = pollUrl.indexOf("http") === 0 ? pollUrl : appUrl(pollUrl);
    var started = Date.now();

    return new Promise(function (resolve, reject) {
      function tick() {
        if (Date.now() - started > timeout) {
          reject(new Error("转换超时。请缩小页数或关闭 OCR 后重试"));
          return;
        }
        fetch(url, { credentials: "same-origin" })
          .then(function (r) {
            if (!r.ok) {
              return r.json().then(
                function (j) {
                  throw new Error(errDetail(j.detail) || "HTTP " + r.status);
                },
                function () {
                  throw new Error("HTTP " + r.status);
                }
              );
            }
            return r.json();
          })
          .then(function (job) {
            var sec = Math.round((Date.now() - started) / 1000);
            var p = typeof job.progress === "number" ? job.progress : 0;
            // Map 0..1 server progress into 75..98 UI band after upload.
            if (opts.onProgress) {
              opts.onProgress(75 + Math.min(23, p * 23), "process");
            }
            if (opts.onStatus) {
              var label =
                job.status === "queued"
                  ? "排队中…"
                  : job.status === "running"
                    ? "转换中…"
                    : job.message || job.status;
              if (sec >= 15 && (job.status === "running" || job.status === "queued")) {
                label += "（已 " + sec + " 秒）";
              }
              opts.onStatus(label, "info");
            }
            if (job.status === "done") {
              resolve(job);
              return;
            }
            if (job.status === "error") {
              reject(new Error(job.error || "转换失败"));
              return;
            }
            setTimeout(tick, interval);
          })
          .catch(function (err) {
            reject(err instanceof Error ? err : new Error(String(err)));
          });
      }
      tick();
    });
  }

  /**
   * Download a completed job result and trigger browser save.
   *
   * @returns {Promise<{blob:Blob, filename:string, headers:Headers}>}
   */
  async function downloadJob(downloadUrl, fallbackName) {
    var url =
      downloadUrl.indexOf("http") === 0 ? downloadUrl : appUrl(downloadUrl);
    var res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) {
      var msg = "HTTP " + res.status;
      try {
        var j = await res.json();
        msg = errDetail(j.detail) || msg;
      } catch (e) {
        /* keep */
      }
      throw new Error(msg);
    }
    var blob = await res.blob();
    var name = downloadName(
      res.headers.get("content-disposition"),
      fallbackName || "download.bin"
    );
    saveBlob(blob, name);
    return { blob: blob, filename: name, headers: res.headers };
  }

  /**
   * Upload → async job → poll → download. Convenience for tool pages.
   *
   * @param {string} submitUrl
   * @param {FormData} formData
   * @param {object} [opts] same progress hooks as xhrPost + fallbackName
   * @returns {Promise<{job:object, filename:string, headers:Headers}>}
   */
  async function submitJobAndDownload(submitUrl, formData, opts) {
    opts = opts || {};
    var submitted = await xhrPostJson(submitUrl, formData, opts);
    var job = submitted.body || {};
    if (!job.id && !job.poll_url) {
      throw new Error("服务器未返回任务 ID");
    }
    if (opts.onStatus) {
      opts.onStatus("任务已创建，等待转换…", "info");
    }
    if (opts.onProgress) opts.onProgress(75, "process");
    var finished = await pollJob(job.poll_url || "/api/jobs/" + job.id, opts);
    if (opts.onProgress) opts.onProgress(98, "process");
    var dl =
      finished.download_url ||
      job.download_url ||
      "/api/jobs/" + (finished.id || job.id) + "/download";
    var fallback =
      opts.fallbackName ||
      finished.download_name ||
      job.download_name ||
      "download.bin";
    var got = await downloadJob(dl, fallback);
    if (opts.onProgress) opts.onProgress(100, "done");
    return { job: finished, filename: got.filename, headers: got.headers };
  }

  global.ToolkitUpload = {
    fmtSize: fmtSize,
    downloadName: downloadName,
    errDetail: errDetail,
    xhrPost: xhrPost,
    xhrPostJson: xhrPostJson,
    pollJob: pollJob,
    downloadJob: downloadJob,
    submitJobAndDownload: submitJobAndDownload,
    bindDropZone: bindDropZone,
    saveBlob: saveBlob,
    xhrErrorMessage: xhrErrorMessage,
    appUrl: appUrl,
  };
})(typeof window !== "undefined" ? window : globalThis);

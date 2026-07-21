/* Toolkit shell service worker — cache static chrome only; never cache API/uploads. */
/* Bump CACHE when shipping SW logic changes. */
var CACHE = "toolkit-shell-v3";
/* Paths relative to SW origin; versioned query strings still match via pathname checks. */
var PRECACHE = [
  "/static/css/tokens.css",
  "/static/css/layout.css",
  "/static/css/home.css",
  "/static/css/tool-page.css",
  "/static/js/toolkit-ux.js",
  "/static/js/upload.js",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches
      .open(CACHE)
      .then(function (cache) {
        return cache.addAll(
          PRECACHE.map(function (u) {
            return new Request(u, { cache: "reload" });
          })
        );
      })
      .then(function () {
        return self.skipWaiting();
      })
      .catch(function () {
        /* partial precache is fine */
      })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (k) {
              return k !== CACHE;
            })
            .map(function (k) {
              return caches.delete(k);
            })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

function isShellAsset(url) {
  try {
    var u = new URL(url);
    if (u.origin !== self.location.origin) return false;
    if (u.pathname.indexOf("/static/") === 0) return true;
    if (u.pathname === "/manifest.webmanifest") return true;
    return false;
  } catch (e) {
    return false;
  }
}

function isNonCacheable(url) {
  try {
    var u = new URL(url);
    var p = u.pathname;
    if (p.indexOf("/api/") === 0) return true;
    if (p.indexOf("/admin") === 0) return true;
    if (p.indexOf("/tools/") === 0 && u.search) return true;
    if (
      p.indexOf("/convert") !== -1 ||
      p.indexOf("/compress") !== -1 ||
      p.indexOf("/upload") !== -1
    ) {
      return true;
    }
    return false;
  } catch (e) {
    return true;
  }
}

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") return;
  if (isNonCacheable(req.url)) return;

  // Navigation: network-first, fall back to offline shell message
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then(function (res) {
          return res;
        })
        .catch(function () {
          return caches.match("/") .then(function (cached) {
            if (cached) return cached;
            return new Response(
              "<!DOCTYPE html><html lang=\"zh-CN\"><meta charset=\"utf-8\">" +
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
                "<title>离线 · 工具集</title>" +
                "<body style=\"font-family:system-ui;padding:48px 24px;text-align:center;background:#0b1220;color:#e8edf7\">" +
                "<h1 style=\"font-size:1.25rem\">当前离线</h1>" +
                "<p style=\"color:#94a3b8;line-height:1.6\">工具集需要连接服务端才能处理文件。<br>请恢复网络后刷新。</p>" +
                "<button onclick=\"location.reload()\" style=\"margin-top:16px;padding:10px 18px;border:0;border-radius:10px;background:#4f46e5;color:#fff;font-weight:700;cursor:pointer\">重试</button>" +
                "</body></html>",
              { headers: { "Content-Type": "text/html; charset=utf-8" } }
            );
          });
        })
    );
    return;
  }

  if (!isShellAsset(req.url)) return;

  // Static assets: stale-while-revalidate
  event.respondWith(
    caches.open(CACHE).then(function (cache) {
      return cache.match(req).then(function (cached) {
        var network = fetch(req)
          .then(function (res) {
            if (res && res.ok) {
              try {
                cache.put(req, res.clone());
              } catch (e) {}
            }
            return res;
          })
          .catch(function () {
            return cached;
          });
        return cached || network;
      });
    })
  );
});

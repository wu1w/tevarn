/**
 * Tevarn official download proxy.
 *
 * Serves installers at:
 *   https://dl.tevarn.com/v0.4.0/Tevarn-Setup-0.4.0-x64.exe
 *   https://dl.tevarn.com/v0.4.0/Tevarn-Mobile-0.4.0.apk
 *
 * Strategy:
 * 1) Prefer R2 binding (env.RELEASES) when bucket is enabled.
 * 2) Else proxy GitHub Releases from Cloudflare edge + Cache API
 *    so mainland users never talk to github.com directly.
 */

const ASSETS = {
  "v0.4.0/Tevarn-Setup-0.4.0-x64.exe": {
    gh: "https://github.com/wu1w/tevarn/releases/download/v0.4.0/Tevarn-Setup-0.4.0-x64.exe",
    type: "application/octet-stream",
  },
  // Canonical APK name (replaced 2026-08-09 engine-fix build)
  "v0.4.0/Tevarn-Mobile-0.4.0.apk": {
    gh: "https://github.com/wu1w/tevarn/releases/download/v0.4.0/Tevarn-Mobile-0.4.0.apk",
    type: "application/vnd.android.package-archive",
  },
  // Cache-bust alias — same build as Tevarn-Mobile-0.4.0.apk after engine fix
  "v0.4.0/Tevarn-Mobile-0.4.0-engine-fix.apk": {
    gh: "https://github.com/wu1w/tevarn/releases/download/v0.4.0/Tevarn-Mobile-0.4.0-engine-fix.apk",
    type: "application/vnd.android.package-archive",
  },
};

const CACHE_TTL = 60 * 60 * 24 * 30; // 30 days

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Max-Age": "86400",
  };
}

function notFound() {
  return new Response(
    JSON.stringify({
      error: "not_found",
      message: "Unknown asset. See https://tevarn.com/#download",
      assets: Object.keys(ASSETS),
    }),
    {
      status: 404,
      headers: { "content-type": "application/json; charset=utf-8", ...corsHeaders() },
    }
  );
}

function indexPage() {
  const lines = Object.keys(ASSETS)
    .map((k) => `  <li><a href="/${k}">/${k}</a></li>`)
    .join("\n");
  const html = `<!doctype html><html><head><meta charset="utf-8"/><title>Tevarn Downloads</title>
<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 16px;line-height:1.6}
a{color:#6D5DF6;font-weight:600}</style></head><body>
<h1>Tevarn downloads</h1>
<p>Official installers (Cloudflare edge). Mainland users: use these links, not GitHub.</p>
<ul>
${lines}
</ul>
<p><a href="https://tevarn.com/#download">← Back to tevarn.com</a></p>
</body></html>`;
  return new Response(html, {
    headers: { "content-type": "text/html; charset=utf-8", ...corsHeaders() },
  });
}

async function fromR2(env, key) {
  if (!env.RELEASES) return null;
  try {
    const obj = await env.RELEASES.get(key);
    if (!obj) return null;
    const meta = ASSETS[key] || {};
    const headers = new Headers();
    obj.writeHttpMetadata(headers);
    headers.set("etag", obj.httpEtag);
    headers.set("content-type", meta.type || "application/octet-stream");
    headers.set(
      "content-disposition",
      `attachment; filename="${key.split("/").pop()}"`
    );
    headers.set("cache-control", `public, max-age=${CACHE_TTL}, immutable`);
    for (const [k, v] of Object.entries(corsHeaders())) headers.set(k, v);
    return new Response(obj.body, { headers });
  } catch (e) {
    console.log("r2 miss/error", String(e));
    return null;
  }
}

async function fromGitHub(request, key, ctx) {
  const meta = ASSETS[key];
  if (!meta) return notFound();

  const cache = caches.default;
  const cacheUrl = new URL(request.url);
  cacheUrl.search = ""; // ignore query for cache key
  const cacheKey = new Request(cacheUrl.toString(), { method: "GET" });

  let cached = await cache.match(cacheKey);
  if (cached) {
    // HEAD support
    if (request.method === "HEAD") {
      return new Response(null, { status: 200, headers: cached.headers });
    }
    return cached;
  }

  const upstream = await fetch(meta.gh, {
    headers: {
      "User-Agent": "TevarnDownloadProxy/1.0 (+https://tevarn.com)",
      Accept: "*/*",
    },
    // Encourage edge cache of the origin fetch where possible
    cf: {
      cacheEverything: true,
      cacheTtl: CACHE_TTL,
    },
    redirect: "follow",
  });

  if (!upstream.ok) {
    return new Response(
      JSON.stringify({
        error: "upstream_failed",
        status: upstream.status,
        source: "github",
        key,
      }),
      {
        status: 502,
        headers: { "content-type": "application/json; charset=utf-8", ...corsHeaders() },
      }
    );
  }

  const headers = new Headers();
  headers.set("content-type", meta.type || "application/octet-stream");
  const cl = upstream.headers.get("content-length");
  if (cl) headers.set("content-length", cl);
  headers.set(
    "content-disposition",
    `attachment; filename="${key.split("/").pop()}"`
  );
  headers.set("cache-control", `public, max-age=${CACHE_TTL}, immutable`);
  headers.set("x-tevarn-source", "github-proxy");
  for (const [k, v] of Object.entries(corsHeaders())) headers.set(k, v);

  const body = request.method === "HEAD" ? null : upstream.body;
  const response = new Response(body, { status: 200, headers });

  // Only cache successful GET full responses
  if (request.method === "GET") {
    ctx.waitUntil(cache.put(cacheKey, response.clone()));
  }
  return response;
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    const url = new URL(request.url);
    let path = url.pathname.replace(/^\/+/, "");
    if (!path || path === "index.html") {
      return indexPage();
    }
    // allow /downloads/v0.4.0/... alias
    if (path.startsWith("downloads/")) path = path.slice("downloads/".length);

    if (!ASSETS[path]) return notFound();

    // Prefer R2 when bound
    const r2 = await fromR2(env, path);
    if (r2) return r2;

    return fromGitHub(request, path, ctx);
  },
};

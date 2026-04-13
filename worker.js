/**
 * QR-Code Raster Generator – Cloudflare Worker Reverse Proxy
 * Leitet alle Anfragen an den Manus-Backend-Server weiter.
 * Backend-URL wird als BACKEND_URL Environment-Variable gesetzt.
 */

const BACKEND_URL = "https://8000-izs2xiw57o1i9rrftbc5w-a1d98c48.us2.manus.computer";

export default {
  async fetch(request, env, ctx) {
    const backend = env.BACKEND_URL || BACKEND_URL;
    const url = new URL(request.url);

    // Ziel-URL zusammenbauen
    const targetUrl = backend + url.pathname + url.search;

    // Request weiterleiten mit Original-Headers (außer Host)
    const newHeaders = new Headers(request.headers);
    newHeaders.set("Host", new URL(backend).host);

    const proxyRequest = new Request(targetUrl, {
      method: request.method,
      headers: newHeaders,
      body: request.body,
      redirect: "follow",
    });

    try {
      const response = await fetch(proxyRequest);

      // CORS-Header hinzufügen
      const responseHeaders = new Headers(response.headers);
      responseHeaders.set("Access-Control-Allow-Origin", "*");
      responseHeaders.set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
      responseHeaders.set("Access-Control-Allow-Headers", "*");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      });
    } catch (err) {
      return new Response(JSON.stringify({ error: "Backend nicht erreichbar", detail: err.message }), {
        status: 502,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
      });
    }
  },
};

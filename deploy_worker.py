#!/usr/bin/env python3
"""
Deployt den Cloudflare Worker als Reverse-Proxy für die QR-API.
"""
import requests, json, os

CF_API_KEY   = "bd554c553be75e02c6772fb1b1d87159a8b62"
CF_EMAIL     = "td.production.landau@gmail.com"
ACCOUNT_ID   = "cef9820bd0c5291de8a97a3a5fbbe4ec"
ZONE_ID      = "ff2d2719e93759ab726f496335da8c90"
WORKER_NAME  = "qr-raster-api-proxy"
DOMAIN       = "qr-api.td-assistant.com"
BACKEND_URL  = "https://8000-izs2xiw57o1i9rrftbc5w-a1d98c48.us2.manus.computer"

HEADERS = {
    "X-Auth-Key":   CF_API_KEY,
    "X-Auth-Email": CF_EMAIL,
}

WORKER_CODE = open("/home/ubuntu/qr-api/worker.js").read()

def deploy_worker():
    """Worker-Script hochladen."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/workers/scripts/{WORKER_NAME}"

    # Multipart: Script + Metadata
    import io
    metadata = json.dumps({
        "main_module": "worker.js",
        "bindings": [
            {"type": "plain_text", "name": "BACKEND_URL", "text": BACKEND_URL}
        ],
        "compatibility_date": "2024-01-01",
    })

    files = {
        "metadata": (None, metadata, "application/json"),
        "worker.js": ("worker.js", WORKER_CODE, "application/javascript+module"),
    }

    r = requests.put(url, headers=HEADERS, files=files)
    data = r.json()
    if data.get("success"):
        print(f"[OK] Worker '{WORKER_NAME}' deployed")
        return True
    else:
        print(f"[FEHLER] Worker: {json.dumps(data.get('errors', []), indent=2)}")
        return False

def add_worker_route():
    """Worker-Route für die Domain setzen."""
    url = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/workers/routes"

    # Bestehende Routes prüfen
    r = requests.get(url, headers=HEADERS)
    routes = r.json().get("result", [])
    for route in routes:
        if DOMAIN in route.get("pattern", ""):
            print(f"[INFO] Route bereits vorhanden: {route['pattern']}")
            # Route aktualisieren
            rid = route["id"]
            r2 = requests.put(
                f"{url}/{rid}",
                headers={**HEADERS, "Content-Type": "application/json"},
                json={"pattern": f"{DOMAIN}/*", "script": WORKER_NAME}
            )
            d2 = r2.json()
            if d2.get("success"):
                print(f"[OK] Route aktualisiert: {DOMAIN}/*")
            else:
                print(f"[WARN] Route-Update: {d2.get('errors')}")
            return True

    # Neue Route erstellen
    payload = {"pattern": f"{DOMAIN}/*", "script": WORKER_NAME}
    r = requests.post(
        url,
        headers={**HEADERS, "Content-Type": "application/json"},
        json=payload
    )
    data = r.json()
    if data.get("success"):
        print(f"[OK] Route erstellt: {DOMAIN}/*")
        return True
    else:
        print(f"[FEHLER] Route: {json.dumps(data.get('errors', []), indent=2)}")
        return False

def update_cname_for_worker():
    """CNAME auf workers.dev setzen (für Worker-Route)."""
    url = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records"
    params = {"name": DOMAIN, "type": "CNAME"}
    r = requests.get(url, headers=HEADERS, params=params)
    records = r.json().get("result", [])

    # Worker-Route braucht einen A/AAAA oder CNAME Record – wir setzen proxied=True
    # damit Cloudflare den Traffic über den Worker leitet
    payload = {
        "type": "AAAA",
        "name": DOMAIN,
        "content": "100::",  # Dummy IPv6 – wird nie erreicht, Worker fängt ab
        "ttl": 1,
        "proxied": True,
    }

    if records:
        # Bestehenden CNAME löschen
        for rec in records:
            del_url = f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records/{rec['id']}"
            requests.delete(del_url, headers=HEADERS)
            print(f"[OK] Alter CNAME gelöscht: {rec['content']}")

    # AAAA Record erstellen (proxied, damit Worker greift)
    r = requests.post(
        f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records",
        headers={**HEADERS, "Content-Type": "application/json"},
        json=payload
    )
    data = r.json()
    if data.get("success"):
        print(f"[OK] AAAA Record erstellt (proxied) für {DOMAIN}")
        return True
    else:
        # Vielleicht schon vorhanden
        print(f"[WARN] AAAA: {data.get('errors')}")
        return False

def main():
    print("=" * 60)
    print("  Cloudflare Worker Deployment")
    print(f"  Worker: {WORKER_NAME}")
    print(f"  Domain: {DOMAIN}")
    print(f"  Backend: {BACKEND_URL}")
    print("=" * 60)

    print("\n[1] Worker deployen...")
    if not deploy_worker():
        return

    print("\n[2] DNS-Record setzen...")
    update_cname_for_worker()

    print("\n[3] Worker-Route setzen...")
    add_worker_route()

    print("\n" + "=" * 60)
    print("  ✓ Deployment abgeschlossen!")
    print(f"  API: https://{DOMAIN}/api/jobs")
    print(f"  Docs: https://{DOMAIN}/docs")
    print("=" * 60)

if __name__ == "__main__":
    main()

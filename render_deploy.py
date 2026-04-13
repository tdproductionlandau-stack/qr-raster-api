#!/usr/bin/env python3
"""
Render.com Deployment via API
"""
import requests, json, time

RENDER_API_KEY = "rnd_GNUYbG5vnsgiIjWfeMFYiCnGDYsy"
GITHUB_REPO    = "https://github.com/tdproductionlandau-stack/qr-raster-api"
SERVICE_NAME   = "qr-raster-api"
CUSTOM_DOMAIN  = "qr-api.das-geschenk.online"

HEADERS = {
    "Authorization": f"Bearer {RENDER_API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

BASE = "https://api.render.com/v1"

def get_owner_id():
    r = requests.get(f"{BASE}/owners?limit=1", headers=HEADERS)
    data = r.json()
    print("Owner response:", json.dumps(data, indent=2)[:300])
    if isinstance(data, list) and data:
        return data[0].get("owner", {}).get("id") or data[0].get("id")
    return None

def create_service(owner_id):
    payload = {
        "type": "web_service",
        "name": SERVICE_NAME,
        "ownerId": owner_id,
        "repo": GITHUB_REPO,
        "branch": "main",
        "autoDeploy": "yes",
        "serviceDetails": {
            "env": "python",
            "plan": "free",
            "region": "frankfurt",
            "envVars": [
                {"key": "PUBLIC_BASE_URL", "value": f"https://{CUSTOM_DOMAIN}"}
            ],
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "uvicorn main:app --host 0.0.0.0 --port $PORT",
            },
        },
    }
    r = requests.post(f"{BASE}/services", headers=HEADERS, json=payload)
    print(f"Create service status: {r.status_code}")
    data = r.json()
    print(json.dumps(data, indent=2)[:500])
    return data

def get_service_by_name():
    r = requests.get(f"{BASE}/services?name={SERVICE_NAME}&limit=10", headers=HEADERS)
    data = r.json()
    if isinstance(data, list):
        for item in data:
            svc = item.get("service", item)
            if svc.get("name") == SERVICE_NAME:
                return svc
    return None

def add_custom_domain(service_id):
    payload = {"name": CUSTOM_DOMAIN}
    r = requests.post(f"{BASE}/services/{service_id}/custom-domains", headers=HEADERS, json=payload)
    print(f"Custom domain status: {r.status_code}")
    data = r.json()
    print(json.dumps(data, indent=2)[:300])
    return data

def main():
    print("=" * 60)
    print(f"  Render.com Deployment: {SERVICE_NAME}")
    print("=" * 60)

    # Owner ID holen
    print("\n[1] Owner ID holen...")
    owner_id = get_owner_id()
    if not owner_id:
        print("FEHLER: Kein Owner gefunden!")
        return
    print(f"    Owner ID: {owner_id}")

    # Prüfen ob Service bereits existiert
    print("\n[2] Bestehenden Service prüfen...")
    existing = get_service_by_name()
    if existing:
        service_id = existing.get("id")
        service_url = existing.get("serviceDetails", {}).get("url") or existing.get("url", "")
        print(f"    Service bereits vorhanden: {service_id}")
        print(f"    URL: {service_url}")
    else:
        # Neuen Service erstellen
        print("\n[3] Service erstellen...")
        result = create_service(owner_id)
        svc = result.get("service", result)
        service_id = svc.get("id")
        service_url = svc.get("serviceDetails", {}).get("url") or svc.get("url", "")
        print(f"    Service ID: {service_id}")
        print(f"    URL: {service_url}")

    if not service_id:
        print("FEHLER: Service-ID nicht gefunden!")
        return

    # Custom Domain hinzufügen
    print(f"\n[4] Custom Domain hinzufügen: {CUSTOM_DOMAIN}...")
    domain_data = add_custom_domain(service_id)

    print("\n" + "=" * 60)
    print("  ✓ Deployment gestartet!")
    print(f"  Render URL:    https://{SERVICE_NAME}.onrender.com")
    print(f"  Custom Domain: https://{CUSTOM_DOMAIN}")
    print(f"  Service ID:    {service_id}")
    print("  → Build läuft, dauert ca. 2-3 Minuten")
    print("=" * 60)

if __name__ == "__main__":
    main()

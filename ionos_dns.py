#!/usr/bin/env python3
"""
IONOS DNS – CNAME-Records für qr-raster und qr-api anlegen
"""
import requests, json

IONOS_API_KEY = "d6c22a3384924c07b7ff9289bf8d8f57.M931x9up1TlC05XYCmW6hI3sd-jwN0IRSwQ5cZoT24lfhqZzr9FSbrBFdSVpbUltA3LcNcMFwS9rbK6OV2hSjw"
ZONE_ID       = "e3c33ab1-66e2-11f0-8966-0a5864440df5"
TARGET        = "cname.manus.space"

HEADERS = {
    "X-API-Key": IONOS_API_KEY,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

BASE_URL = "https://api.hosting.ionos.com/dns/v1"

SUBDOMAINS = [
    "qr-raster.tim-dittmann.de",
    "qr-api.tim-dittmann.de",
]

def get_existing_records():
    url = f"{BASE_URL}/zones/{ZONE_ID}"
    r = requests.get(url, headers=HEADERS)
    data = r.json()
    return data.get("records", [])

def delete_record(record_id):
    url = f"{BASE_URL}/zones/{ZONE_ID}/records/{record_id}"
    r = requests.delete(url, headers=HEADERS)
    return r.status_code in (200, 204)

def create_cname(fqdn):
    url = f"{BASE_URL}/zones/{ZONE_ID}/records"
    payload = [
        {
            "name": fqdn,
            "type": "CNAME",
            "content": TARGET,
            "ttl": 300,
            "prio": 0,
            "disabled": False,
        }
    ]
    r = requests.post(url, headers=HEADERS, json=payload)
    return r.status_code, r.json() if r.text else {}

def main():
    print("=" * 60)
    print("  IONOS DNS – CNAME Setup")
    print(f"  Ziel: {TARGET}")
    print("=" * 60)

    print("\n[1] Bestehende Records laden...")
    records = get_existing_records()
    existing = {rec["name"]: rec for rec in records if rec.get("type") == "CNAME"}
    print(f"    {len(records)} Records gefunden, {len(existing)} CNAMEs")

    for fqdn in SUBDOMAINS:
        print(f"\n[→] {fqdn}")

        if fqdn in existing:
            old = existing[fqdn]
            print(f"    Alter CNAME: {old['content']} – wird gelöscht...")
            ok = delete_record(old["id"])
            print(f"    {'Gelöscht ✓' if ok else 'Löschen fehlgeschlagen!'}")

        status, resp = create_cname(fqdn)
        if status in (200, 201):
            print(f"    CNAME angelegt ✓  {fqdn} → {TARGET}")
        else:
            print(f"    FEHLER ({status}): {json.dumps(resp, indent=2)}")

    print("\n" + "=" * 60)
    print("  ✓ DNS-Setup abgeschlossen!")
    print(f"  Frontend: https://qr-raster.tim-dittmann.de")
    print(f"  API:      https://qr-api.tim-dittmann.de")
    print("=" * 60)

if __name__ == "__main__":
    main()

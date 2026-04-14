"""
QR-Code Raster Generator – REST API
====================================
POST /api/jobs   → Job erstellen, PDF generieren, zurückgeben
GET  /api/jobs/{jobId} → Job-Status abfragen
GET  /api/pdfs/{filename} → PDF herunterladen
GET  /docs       → Swagger UI
"""

import os
import uuid
import time
import json
import threading
import traceback
import subprocess
import sys
from pathlib import Path
from typing import Optional

# zxing-cpp voraufladen (kein System-Paket nötig)
try:
    import zxingcpp as _zxing_test
except Exception:
    pass

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── Verzeichnisse ──
BASE_DIR   = Path(__file__).parent
JOBS_FILE  = BASE_DIR / "jobs.json"
PDFS_DIR   = BASE_DIR / "pdfs"
IMGS_DIR   = BASE_DIR / "uploads"
PDFS_DIR.mkdir(exist_ok=True)
IMGS_DIR.mkdir(exist_ok=True)

# ── Job-Speicher (JSON-Datei, thread-safe) ──
_lock = threading.Lock()

def load_jobs() -> dict:
    with _lock:
        if not JOBS_FILE.exists():
            return {}
        try:
            return json.loads(JOBS_FILE.read_text())
        except Exception:
            return {}

def save_jobs(jobs: dict):
    with _lock:
        JOBS_FILE.write_text(json.dumps(jobs, indent=2, ensure_ascii=False))

def next_job_id() -> str:
    jobs = load_jobs()
    if not jobs:
        return "1001"
    max_id = max(int(k) for k in jobs.keys() if k.isdigit())
    return str(max_id + 1)

# ── QR-Code-Erkennung & Auto-Crop ──
def extract_qr_from_image(image_path: str) -> str:
    """
    Erkennt den QR-Code im Bild mit zxing-cpp (kein System-Paket nötig), schneidet ihn aus.
    Gibt den Pfad zur ausgeschnittenen Datei zurück (oder original wenn kein QR gefunden).
    """
    from PIL import Image as PILImage
    import zxingcpp

    def try_detect(pil_img):
        results = zxingcpp.read_barcodes(pil_img)
        return [r for r in results if 'QR' in str(r.format).upper()]

    # Bild laden
    try:
        img = PILImage.open(image_path)
        if img.mode not in ('RGB', 'L', 'RGBA'):
            img = img.convert('RGB')
    except Exception:
        return image_path

    # Verschiedene Strategien
    codes = try_detect(img)
    if not codes:
        codes = try_detect(img.convert('L'))  # Graustufen
    if not codes:
        big = img.resize((img.width * 2, img.height * 2), PILImage.LANCZOS)
        codes = try_detect(big)
    if not codes:
        small = img.resize((img.width // 2, img.height // 2), PILImage.LANCZOS)
        codes = try_detect(small)

    if not codes:
        return image_path

    # Position aus zxing-cpp Position-String parsen: "x1 x2 x3 x4 y1 y2 y3 y4"
    pos = codes[0].position
    pos_str = str(pos)  # z.B. "483x836 723x836 723x1075 483x1076"
    try:
        pts = [tuple(int(v) for v in p.split('x')) for p in pos_str.split()]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
    except Exception:
        return image_path

    qr_w = x_max - x_min
    qr_h = y_max - y_min
    pad_side = max(15, int(qr_w * 0.08))   # 8% seitlich und oben
    pad_bottom = max(20, int(qr_h * 0.30)) # 30% unten – für Text unter dem QR-Code
    left   = max(0, x_min - pad_side)
    top    = max(0, y_min - pad_side)
    right  = min(img.width,  x_max + pad_side)
    bottom = min(img.height, y_max + pad_bottom)

    cropped = img.crop((left, top, right, bottom))
    out_path = image_path + '_qr_crop.png'
    cropped.save(out_path, 'PNG')
    return out_path

# ── Bild-Trimming: weiße Ränder + dunkle Balken entfernen ──
def trim_image(img):
    """Entfernt weiße Ränder + schwarze Balken-Zeilen (Auto-Trim)."""
    import numpy as np
    from PIL import Image as PILImage
    arr = np.array(img.convert('RGB')).copy()
    h, w = arr.shape[:2]

    # Schritt 1: ALLE schwarzen Balken-Zeilen im Bild weiß machen
    # Eine Zeile ist ein Balken wenn >25% der Pixel fast-schwarz sind
    # UND die Zeile kein QR-Code-Muster ist (QR-Codes haben auch schwarze Pixel,
    # aber nie eine komplett schwarze Zeile über die volle Breite)
    is_black_pixel = (arr[:,:,0] < 60) & (arr[:,:,1] < 60) & (arr[:,:,2] < 60)
    black_ratio_per_row = is_black_pixel.sum(axis=1) / w

    # Balken-Zeilen: >25% schwarz UND die schwarzen Pixel sind über die ganze Breite verteilt
    # (nicht geclustert wie bei einem QR-Code)
    for i in range(h):
        if black_ratio_per_row[i] > 0.25:
            # Prüfen ob es ein horizontaler Balken ist:
            # Schwarze Pixel müssen über mindestens 70% der Breite verteilt sein
            black_cols = np.where(is_black_pixel[i])[0]
            if len(black_cols) > 0:
                span = int(black_cols[-1]) - int(black_cols[0])
                if span > w * 0.5:  # Balken geht über >50% der Breite
                    arr[i, :] = [255, 255, 255]  # Zeile weiß machen

    # Schritt 2: Weiße Ränder trimmen
    is_white = (arr[:,:,0] > 240) & (arr[:,:,1] > 240) & (arr[:,:,2] > 240)
    content = ~is_white
    rows_with_content = np.any(content, axis=1)
    cols_with_content = np.any(content, axis=0)
    if not rows_with_content.any():
        return img
    top   = int(np.argmax(rows_with_content))
    bottom = int(h - np.argmax(rows_with_content[::-1]))
    left  = int(np.argmax(cols_with_content))
    right = int(w - np.argmax(cols_with_content[::-1]))

    pad = 4
    top    = max(0, top - pad)
    bottom = min(h, bottom + pad)
    left   = max(0, left - pad)
    right  = min(w, right + pad)

    if bottom <= top or right <= left:
        return img
    result = PILImage.fromarray(arr)
    return result.crop((left, top, right, bottom))

# ── PDF-Generierung ──
def generate_pdf(image_path: str, pdf_path: str,
                 cols: int = 5, rows: int = 8,
                 margin_mm: float = 10, spacing_mm: float = 3,
                 landscape: bool = False,
                 job_id: str = ""):
    """Generiert ein DIN A4 PDF mit cols×rows Kopien des Bildes."""
    from reportlab.lib.pagesizes import A4, landscape as RL_landscape
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from PIL import Image as PILImage

    if landscape:
        page_size = RL_landscape(A4)
    else:
        page_size = A4

    page_w, page_h = page_size  # in points (1 pt = 1/72 inch)

    # mm → points
    margin = margin_mm * mm
    spacing = spacing_mm * mm

    # Platz für Auftragsnummer oben reservieren (nur wenn job_id angegeben)
    label_h = 14 * mm if job_id else 0  # 14mm für den Label-Bereich

    usable_w = page_w - 2 * margin - (cols - 1) * spacing
    usable_h = page_h - 2 * margin - (rows - 1) * spacing - label_h
    qr_size = min(usable_w / cols, usable_h / rows)

    total_w = cols * qr_size + (cols - 1) * spacing
    total_h = rows * qr_size + (rows - 1) * spacing
    offset_x = (page_w - total_w) / 2
    offset_y = (page_h - total_h) / 2 + label_h  # nach unten verschieben wegen Label

    # Bild laden & ggf. konvertieren
    img = PILImage.open(image_path)
    if img.mode in ("RGBA", "P", "LA"):
        bg = PILImage.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Auto-Trim: dunkle Balken und überschüssige Ränder entfernen
    img = trim_image(img)

    tmp_jpg = str(image_path) + "_tmp.jpg"
    img.save(tmp_jpg, "JPEG", quality=95)

    c = canvas.Canvas(pdf_path, pagesize=page_size)

    # Auftragsnummer oben links
    if job_id:
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)  # grau
        label_y = page_h - margin - 7 * mm
        c.drawString(offset_x, label_y, f"Auftrag #{job_id}")
        # Dünne Trennlinie
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.setLineWidth(0.5)
        c.line(offset_x, label_y - 2 * mm, offset_x + total_w, label_y - 2 * mm)

    for row in range(rows):
        for col in range(cols):
            x = offset_x + col * (qr_size + spacing)
            # ReportLab: y=0 ist unten, wir rechnen von oben
            y = page_h - offset_y - (row + 1) * qr_size - row * spacing
            c.drawImage(tmp_jpg, x, y, width=qr_size, height=qr_size,
                        preserveAspectRatio=False, mask='auto')
    c.save()

    # Temp-Datei aufräumen
    try:
        os.remove(tmp_jpg)
    except Exception:
        pass

# ── FastAPI App ──
app = FastAPI(
    title="QR-Code Raster Generator API",
    description="Erstellt DIN A4 PDFs mit QR-Code-Rastern.\n\n"
                "**POST /api/jobs** – Job erstellen & PDF generieren\n\n"
                "**GET /api/jobs/{jobId}** – Job-Status abfragen\n\n"
                "**GET /api/pdfs/{filename}** – PDF herunterladen",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Statische PDF-Dateien ──
app.mount("/api/pdfs", StaticFiles(directory=str(PDFS_DIR)), name="pdfs")

# ── Öffentliche Basis-URL (wird beim Start gesetzt) ──
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def get_pdf_url(filename: str, request_base: str = "") -> str:
    base = PUBLIC_BASE_URL or request_base
    return f"{base}/api/pdfs/{filename}"


# ════════════════════════════════════════
#  POST /api/jobs
# ════════════════════════════════════════
@app.post("/api/jobs", summary="Job erstellen und PDF generieren")
async def create_job(
    image: UploadFile = File(..., description="Logo/Bild des Users (JPG, PNG, HEIC, …)"),
    phone: Optional[str] = Form(None, description="WhatsApp-Nummer, z.B. 4915756159553 (optional für Browser-Jobs)"),
    name: Optional[str] = Form(None, description="Name des Users (optional)"),
    cols: int = Form(5, description="Spalten (Standard: 5)"),
    rows: int = Form(8, description="Zeilen (Standard: 8)"),
    margin: float = Form(10.0, description="Seitenrand in mm (Standard: 10)"),
    spacing: float = Form(3.0, description="Abstand in mm (Standard: 3)"),
    landscape: bool = Form(False, description="Querformat (Standard: false)"),
):
    job_id = next_job_id()
    ts = int(time.time())

    # ── Bild speichern ──
    ext = Path(image.filename or "image.jpg").suffix.lower() or ".jpg"
    img_filename = f"job_{job_id}{ext}"
    img_path = IMGS_DIR / img_filename
    content = await image.read()
    img_path.write_bytes(content)

    # ── Job anlegen (status: processing) ──
    jobs = load_jobs()
    jobs[job_id] = {
        "jobId": job_id,
        "status": "processing",
        "phone": phone or "browser",
        "name": name or "",
        "imageFile": img_filename,
        "cols": cols,
        "rows": rows,
        "margin": margin,
        "spacing": spacing,
        "landscape": landscape,
        "documentUrl": None,
        "pdfFile": None,
        "createdAt": ts,
        "updatedAt": ts,
        "error": None,
    }
    save_jobs(jobs)

    # ── PDF synchron generieren (schnell genug für Direktantwort) ──
    pdf_filename = f"qr-raster-{job_id}.pdf"
    pdf_path = PDFS_DIR / pdf_filename
    error_msg = None
    try:
        # QR-Code automatisch erkennen und ausschneiden
        effective_image = extract_qr_from_image(str(img_path))
        generate_pdf(
            image_path=effective_image,
            pdf_path=str(pdf_path),
            cols=cols,
            rows=rows,
            margin_mm=margin,
            spacing_mm=spacing,
            landscape=landscape,
            job_id=job_id,
        )
    except Exception as e:
        error_msg = str(e)
        traceback.print_exc()

    # ── Job aktualisieren ──
    jobs = load_jobs()
    if error_msg:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = error_msg
        jobs[job_id]["updatedAt"] = int(time.time())
        save_jobs(jobs)
        return JSONResponse(
            status_code=500,
            content={"jobId": job_id, "status": "error", "documentUrl": None, "error": error_msg},
        )

    doc_url = get_pdf_url(pdf_filename)
    jobs[job_id]["status"] = "done"
    jobs[job_id]["pdfFile"] = pdf_filename
    jobs[job_id]["documentUrl"] = doc_url
    jobs[job_id]["updatedAt"] = int(time.time())
    save_jobs(jobs)

    return JSONResponse(
        status_code=200,
        content={
            "jobId": job_id,
            "status": "done",
            "documentUrl": doc_url,
        },
    )


# ════════════════════════════════════════
#  GET /api/jobs/{jobId}
# ════════════════════════════════════════
@app.get("/api/jobs/{job_id}", summary="Job-Status abfragen")
async def get_job(job_id: str):
    jobs = load_jobs()
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' nicht gefunden.")
    return JSONResponse(content={
        "jobId": job["jobId"],
        "status": job["status"],
        "documentUrl": job.get("documentUrl"),
    })


# ════════════════════════════════════════
#  GET /api/jobs  (alle Jobs – für Dashboard)
# ════════════════════════════════════════
@app.get("/api/jobs", summary="Alle Jobs auflisten")
async def list_jobs():
    jobs = load_jobs()
    result = []
    for job in sorted(jobs.values(), key=lambda j: j.get("createdAt", 0), reverse=True):
        result.append({
            "jobId": job["jobId"],
            "status": job["status"],
            "phone": job.get("phone", ""),
            "name": job.get("name", ""),
            "documentUrl": job.get("documentUrl"),
            "createdAt": job.get("createdAt"),
            "cols": job.get("cols", 5),
            "rows": job.get("rows", 8),
        })
    return JSONResponse(content=result)


# ════════════════════════════════════════
#  POST /api/detect-crop  → QR-Code erkennen und ausschneiden
# ════════════════════════════════════════
@app.post("/api/detect-crop", summary="QR-Code aus Bild ausschneiden")
async def detect_crop(image: UploadFile = File(...)):
    """Erkennt den QR-Code im Bild und gibt das ausgeschnittene Bild als PNG zurück."""
    import tempfile, os
    from fastapi.responses import FileResponse, JSONResponse
    suffix = Path(image.filename).suffix if image.filename else '.jpg'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await image.read())
        tmp_path = tmp.name
    try:
        result_path = extract_qr_from_image(tmp_path)
        if result_path == tmp_path:
            # Kein QR-Code gefunden – Original zurückgeben
            return JSONResponse(status_code=422, content={"error": "no_qr", "message": "Kein QR-Code erkannt"})
        return FileResponse(result_path, media_type="image/png", filename="qr_crop.png")
    finally:
        try: os.unlink(tmp_path)
        except: pass


# ════════════════════════════════════════
#  GET /api/next-job-id  → Nächste Job-ID
# ════════════════════════════════════════
@app.get("/api/next-job-id", summary="Nächste verfügbare Job-ID")
async def get_next_job_id():
    """Gibt die nächste verfügbare Job-ID zurück (für Browser-generierte Jobs)."""
    jobs = load_jobs()
    if not jobs:
        return {"nextId": "1001"}
    max_id = max((int(k) for k in jobs.keys() if k.isdigit()), default=1000)
    return {"nextId": str(max_id + 1)}


# ════════════════════════════════════════
#  GET /health
# ════════════════════════════════════════
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "jobs": len(load_jobs())}


# ════════════════════════════════════════
#  GET /  → Frontend (index.html)
# ════════════════════════════════════════
@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import HTMLResponse
    html = (BASE_DIR / "static_index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)

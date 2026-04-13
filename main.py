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
from pathlib import Path
from typing import Optional

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
    Erkennt den QR-Code im Bild, schneidet ihn aus und speichert ihn als neue Datei.
    Gibt den Pfad zur ausgeschnittenen Datei zurück (oder original wenn kein QR gefunden).
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        # Fallback: PIL laden und als PNG speichern
        from PIL import Image as PILImage
        pil_img = PILImage.open(image_path)
        if pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')
        tmp = image_path + '_converted.png'
        pil_img.save(tmp)
        img = cv2.imread(tmp)
        if img is None:
            return image_path

    detector = cv2.QRCodeDetector()

    # Verschiedene Strategien für robuste Erkennung
    strategies = [
        img,
        cv2.cvtColor(img, cv2.COLOR_BGR2GRAY),
        cv2.threshold(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],
        cv2.resize(img, None, fx=2, fy=2),
        cv2.resize(img, None, fx=0.5, fy=0.5),
    ]

    bbox = None
    scale = 1.0
    for i, proc in enumerate(strategies):
        if len(proc.shape) == 2:
            test = cv2.cvtColor(proc, cv2.COLOR_GRAY2BGR)
        else:
            test = proc
        _, b, _ = detector.detectAndDecode(test)
        if b is not None:
            bbox = b
            if i == 3:  # 2x upscale
                scale = 2.0
            elif i == 4:  # 0.5x downscale
                scale = 0.5
            break

    if bbox is None:
        # Kein QR-Code gefunden – original zurückgeben
        return image_path

    # Bounding Box berechnen (mit Scale-Korrektur)
    pts = (bbox[0] / scale).astype(int)
    x_min, y_min = pts[:, 0].min(), pts[:, 1].min()
    x_max, y_max = pts[:, 0].max(), pts[:, 1].max()

    # Padding
    pad = max(10, int((x_max - x_min) * 0.05))
    x_min = max(0, x_min - pad)
    y_min = max(0, y_min - pad)
    x_max = min(img.shape[1], x_max + pad)
    y_max = min(img.shape[0], y_max + pad)

    cropped = img[y_min:y_max, x_min:x_max]
    out_path = image_path + '_qr_crop.png'
    cv2.imwrite(out_path, cropped)
    return out_path

# ── PDF-Generierung ──
def generate_pdf(image_path: str, pdf_path: str,
                 cols: int = 5, rows: int = 8,
                 margin_mm: float = 10, spacing_mm: float = 3,
                 landscape: bool = False):
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

    usable_w = page_w - 2 * margin - (cols - 1) * spacing
    usable_h = page_h - 2 * margin - (rows - 1) * spacing
    qr_size = min(usable_w / cols, usable_h / rows)

    total_w = cols * qr_size + (cols - 1) * spacing
    total_h = rows * qr_size + (rows - 1) * spacing
    offset_x = (page_w - total_w) / 2
    offset_y = (page_h - total_h) / 2

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

    tmp_jpg = str(image_path) + "_tmp.jpg"
    img.save(tmp_jpg, "JPEG", quality=95)

    c = canvas.Canvas(pdf_path, pagesize=page_size)
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
    phone: str = Form(..., description="WhatsApp-Nummer, z.B. 4915756159553"),
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
        "phone": phone,
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

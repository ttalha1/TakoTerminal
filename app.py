import os
import sys
import time
import math
import uuid
import random
import zipfile
import asyncio
import shutil
import socket
import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, UploadFile,
    File, Form, HTTPException, BackgroundTasks, Request
)
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image

try:
    from pypdf import PdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        PdfReader = None

# ==============================================================================
# 1. KONFİGÜRASYON VE TARİFE
# ==============================================================================

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    ASSETS_DIR = BUNDLE_DIR / "assets"
    if not ASSETS_DIR.exists():
        ASSETS_DIR = BASE_DIR / "assets"
else:
    BASE_DIR = Path(__file__).resolve().parent
    ASSETS_DIR = BASE_DIR / "assets"

UPLOAD_DIR = BASE_DIR / "kiosk_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_TARIFF = {
    "bw_single": 1.50,     # Siyah-Beyaz Tek Yön sayfa başı
    "bw_duplex": 1.25,     # Siyah-Beyaz Çift Yön sayfa başı (%17 kağıt tasarruf indirimi)
    "color_single": 4.00,  # Renkli Tek Yön sayfa başı
    "color_duplex": 3.40   # Renkli Çift Yön sayfa başı (%15 indirim)
}

def load_tariff():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_TARIFF, **data.get("tariff", {})}
        except Exception as e:
            print(f"[CONFIG LOAD ERROR] {e}")
    return DEFAULT_TARIFF.copy()

TARIFF = load_tariff()

def save_tariff(new_tariff: dict):
    global TARIFF
    TARIFF.update(new_tariff)
    try:
        data = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data["tariff"] = TARIFF
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[CONFIG SAVE ERROR] {e}")

ORDERS: Dict[str, Dict[str, Any]] = {}
PIN_TO_ORDER: Dict[str, str] = {}
SERVER_STATE = {
    "tunnel_url": "",
    "local_ip": ""
}

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

SERVER_STATE["local_ip"] = get_local_ip()

# ==============================================================================
# 2. WEBSOCKET BAĞLANTI YÖNETİCİSİ
# ==============================================================================

class ConnectionManager:
    def __init__(self):
        self.kiosk_connections: List[WebSocket] = []
        self.mobile_connections: Dict[str, List[WebSocket]] = {}

    async def connect_kiosk(self, websocket: WebSocket):
        await websocket.accept()
        self.kiosk_connections.append(websocket)

    def disconnect_kiosk(self, websocket: WebSocket):
        if websocket in self.kiosk_connections:
            self.kiosk_connections.remove(websocket)

    async def broadcast_kiosk(self, message: dict):
        dead_conns = []
        for connection in list(self.kiosk_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_conns.append(connection)
        for dead in dead_conns:
            self.disconnect_kiosk(dead)

    async def connect_mobile(self, pin_code: str, websocket: WebSocket):
        await websocket.accept()
        if pin_code not in self.mobile_connections:
            self.mobile_connections[pin_code] = []
        self.mobile_connections[pin_code].append(websocket)

    def disconnect_mobile(self, pin_code: str, websocket: WebSocket):
        if pin_code in self.mobile_connections:
            if websocket in self.mobile_connections[pin_code]:
                self.mobile_connections[pin_code].remove(websocket)
            if not self.mobile_connections[pin_code]:
                del self.mobile_connections[pin_code]

    async def notify_mobile(self, pin_code: str, message: dict):
        if pin_code in self.mobile_connections:
            for conn in list(self.mobile_connections[pin_code]):
                try:
                    await conn.send_json(message)
                except Exception:
                    self.disconnect_mobile(pin_code, conn)

manager = ConnectionManager()

# ==============================================================================
# 3. SAYFA SAYMA, DÖNÜŞTÜRME VE KVKK İMHA MOTORU
# ==============================================================================

def secure_shred_file(file_path: Path):
    """Dosyayı rastgele baytlarla ezerek güvenli şekilde imha eder."""
    try:
        if file_path.exists():
            length = file_path.stat().st_size
            with open(file_path, "wb") as f:
                f.write(os.urandom(min(length, 1024 * 1024)))
            file_path.unlink(missing_ok=True)
    except Exception as e:
        print(f"[SHRED ERROR] {e}")

def secure_shred_directory(dir_path: Path):
    """Bir sipariş klasöründeki tüm dosyaları güvenle imha eder."""
    try:
        if dir_path.exists() and dir_path.is_dir():
            for p in dir_path.glob("*"):
                if p.is_file():
                    secure_shred_file(p)
            shutil.rmtree(dir_path, ignore_errors=True)
    except Exception as e:
        print(f"[SHRED DIR ERROR] {e}")

def extract_docx_page_count_from_xml(docx_path: Path) -> int:
    """DOCX dosyasının içindeki app.xml metadata'sından sayfa/kelime sayısını okur."""
    try:
        with zipfile.ZipFile(docx_path, 'r') as z:
            if 'docProps/app.xml' in z.namelist():
                xml_content = z.read('docProps/app.xml')
                root = ET.fromstring(xml_content)
                for elem in root.iter():
                    if elem.tag.endswith('Pages'):
                        val = int(elem.text)
                        if val > 0:
                            return val
                    if elem.tag.endswith('Words'):
                        words = int(elem.text)
                        return max(1, math.ceil(words / 250))
    except Exception as e:
        print(f"[DOCX METADATA ERROR] {e}")
    return 1

def convert_docx_to_pdf(input_docx: Path, output_pdf: Path) -> bool:
    """Word belgesini (.docx) arka planda Word motoruyla PDF'e dönüştürür."""
    try:
        import pythoncom
        from docx2pdf import convert
        pythoncom.CoInitialize()
        try:
            convert(str(input_docx), str(output_pdf))
            return output_pdf.exists()
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        print(f"[DOCX TO PDF CONVERT ERROR] {e}")
        return False

def convert_image_to_pdf(img_path: Path, output_pdf: Path) -> bool:
    """Görseli (JPG, PNG vb.) PDF'e dönüştürür."""
    try:
        img = Image.open(img_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(output_pdf, "PDF", resolution=100.0)
        return True
    except Exception as e:
        print(f"[IMAGE TO PDF ERROR] {e}")
        return False

def get_pdf_page_count(pdf_path: Path) -> int:
    """PDF dosyasının net sayfa sayısını pypdf ile hesaplar."""
    if PdfReader:
        try:
            reader = PdfReader(str(pdf_path))
            return max(1, len(reader.pages))
        except Exception as e:
            print(f"[PDF READ ERROR] {e}")
    return 1

def process_file_item(source_path: Path, format_action: str, order_folder: Path) -> Dict[str, Any]:
    """
    Dosyayı analiz eder, isteniyorsa PDF formatına dönüştürür ve kesin sayfa sayısını çıkarır.
    """
    suffix = source_path.suffix.lower()
    final_path = source_path
    final_name = source_path.name
    converted = False

    # 1. Word Dosyası (.docx, .doc)
    if suffix in [".docx", ".doc"]:
        if format_action == "pdf":
            pdf_name = f"{source_path.stem}.pdf"
            pdf_path = order_folder / pdf_name
            if convert_docx_to_pdf(source_path, pdf_path):
                final_path = pdf_path
                final_name = pdf_name
                converted = True
                pages = get_pdf_page_count(pdf_path)
            else:
                pages = extract_docx_page_count_from_xml(source_path)
        else:
            pages = extract_docx_page_count_from_xml(source_path)

    # 2. Resim Dosyaları (.jpg, .png, .jpeg, .webp, .bmp)
    elif suffix in [".jpg", ".jpeg", ".png", ".webp", ".bmp"]:
        if format_action == "pdf":
            pdf_name = f"{source_path.stem}.pdf"
            pdf_path = order_folder / pdf_name
            if convert_image_to_pdf(source_path, pdf_path):
                final_path = pdf_path
                final_name = pdf_name
                converted = True
        pages = 1

    # 3. PDF Dosyası
    elif suffix == ".pdf":
        pages = get_pdf_page_count(source_path)

    else:
        pages = 1

    file_size_mb = round(final_path.stat().st_size / (1024 * 1024), 2)
    return {
        "final_path": str(final_path),
        "final_name": final_name,
        "is_converted": converted,
        "page_count": pages,
        "file_size": file_size_mb
    }

# ==============================================================================
# 4. KVKK 15 DAKİKALIK OTOMATİK İMHA DÖNGÜSÜ
# ==============================================================================

async def auto_cleanup_loop():
    while True:
        try:
            await asyncio.sleep(15)
            now = datetime.now()
            expired_ids = []
            for oid, order in list(ORDERS.items()):
                if now >= order["expire_at"]:
                    expired_ids.append(oid)

            for oid in expired_ids:
                order = ORDERS.pop(oid, None)
                if order:
                    pin = order.get("pin_code")
                    if pin in PIN_TO_ORDER:
                        del PIN_TO_ORDER[pin]
                    
                    folder = UPLOAD_DIR / oid
                    secure_shred_directory(folder)

                    await manager.broadcast_kiosk({
                        "event": "ORDER_EXPIRED",
                        "order_id": oid,
                        "pin_code": pin
                    })
                    await manager.notify_mobile(pin, {
                        "event": "ORDER_EXPIRED",
                        "message": "Sipariş süresi (15 dk) doldu ve dosyalarınız güvenle imha edildi."
                    })
                    print(f"[KVKK İMHA] {oid} numaralı sipariş ve tüm dosyaları kalıcı olarak imha edildi.")
        except Exception as e:
            print(f"[CLEANUP ERROR] {e}")

# ==============================================================================
# 5. FASTAPI UYGULAMASI VE API ENDPOINT'LERİ
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    tunnel_file = BASE_DIR / "tunnel_url.txt"
    if tunnel_file.exists():
        try:
            SERVER_STATE["tunnel_url"] = tunnel_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    cleanup_task = asyncio.create_task(auto_cleanup_loop())
    yield
    cleanup_task.cancel()

app = FastAPI(title="TakoTerminal - QR Baskı ve Fotokopi Kiosk Sistemi", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ASSETS_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/assets/{filename}")
def get_asset(filename: str):
    asset_path = ASSETS_DIR / filename
    if not asset_path.exists():
        raise HTTPException(status_code=404, detail="Asset not found")
    if filename.endswith(".svg"):
        media_type = "image/svg+xml"
    elif filename.endswith((".jpg", ".jpeg")):
        media_type = "image/jpeg"
    elif filename.endswith(".png"):
        media_type = "image/png"
    elif filename.endswith(".ico"):
        media_type = "image/x-icon"
    else:
        media_type = "application/octet-stream"
    return FileResponse(asset_path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

@app.get("/favicon.ico")
def get_favicon():
    for candidate in ["logo.svg", "icon.svg", "logo.png"]:
        p = ASSETS_DIR / candidate
        if p.exists():
            media_type = "image/svg+xml" if candidate.endswith(".svg") else "image/png"
            return FileResponse(p, media_type=media_type)
    raise HTTPException(status_code=404, detail="Favicon not found")

@app.get("/")
def index():
    return HTMLResponse("""
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <link rel="icon" type="image/png" href="/assets/logo.png?v=2">
        <title>TakoTerminal</title>
        <style>
            body { font-family: system-ui, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #0f172a; color: white; }
            .card { background: #1e293b; padding: 2.5rem; border-radius: 1.5rem; text-align: center; max-width: 480px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); border: 1px solid #334155; }
            a { display: block; margin: 1rem 0; padding: 1rem 1.5rem; border-radius: 0.75rem; text-decoration: none; font-weight: bold; font-size: 1.1rem; transition: transform 0.2s; }
            a:hover { transform: translateY(-2px); }
            .btn-kiosk { background: #6366f1; color: white; }
            .btn-mobile { background: #10b981; color: white; }
            .logo-img { width: 130px; height: 130px; margin: 0 auto 1.25rem; display: block; border-radius: 2rem; box-shadow: 0 10px 25px rgba(99,102,241,0.3); object-fit: contain; }
        </style>
    </head>
    <body>
        <div class="card">
            <img src="/assets/logo.png?v=2" alt="TakoTerminal" class="logo-img">
            <h2 style="margin: 0; font-size: 1.75rem;">TakoTerminal</h2>
            <p style="color: #94a3b8; margin: 0.5rem 0 1.5rem; font-size: 0.9rem;">Akıllı Kırtasiye Baskı ve Fotokopi Terminali</p>
            <a href="/kiosk" class="btn-kiosk">🖥️ Ana PC Kiosk Ekranı</a>
            <a href="/mobile" class="btn-mobile">📱 Müşteri Telefon Ekranı</a>
        </div>
    </body>
    </html>
    """)

@app.get("/api/config")
def get_config():
    return {
        "local_ip": SERVER_STATE["local_ip"],
        "tunnel_url": SERVER_STATE["tunnel_url"],
        "tariff": TARIFF
    }

@app.post("/api/tunnel-url")
async def set_tunnel_url(data: dict):
    url = data.get("url", "").strip()
    SERVER_STATE["tunnel_url"] = url
    try:
        (BASE_DIR / "tunnel_url.txt").write_text(url, encoding="utf-8")
    except Exception:
        pass
    await manager.broadcast_kiosk({
        "event": "TUNNEL_UPDATED",
        "tunnel_url": url
    })
    return {"status": "ok", "tunnel_url": url}

@app.post("/api/tariff")
async def update_tariff(data: dict):
    new_tariff = {
        "bw_single": round(float(data.get("bw_single", TARIFF["bw_single"])), 2),
        "bw_duplex": round(float(data.get("bw_duplex", TARIFF["bw_duplex"])), 2),
        "color_single": round(float(data.get("color_single", TARIFF["color_single"])), 2),
        "color_duplex": round(float(data.get("color_duplex", TARIFF["color_duplex"])), 2)
    }
    save_tariff(new_tariff)
    await manager.broadcast_kiosk({
        "event": "TARIFF_UPDATED",
        "tariff": TARIFF
    })
    for pin in list(manager.mobile_connections.keys()):
        await manager.notify_mobile(pin, {
            "event": "TARIFF_UPDATED",
            "tariff": TARIFF
        })
    return {"status": "ok", "tariff": TARIFF}

# ÇOKLU DOSYA VE AYARLI GÖNDERİM ENDPOINT'İ
@app.post("/api/upload")
async def upload_multi_files(
    customer_name: str = Form(...),
    items_meta: str = Form(...), # JSON Array: [{"format_action": "pdf", "color_mode": "bw", "duplex": "duplex", "copies": 2, "note": "..."}, ...]
    files: List[UploadFile] = File(...)
):
    if not files:
        raise HTTPException(status_code=400, detail="Lütfen en az bir dosya seçiniz.")
    
    try:
        meta_list = json.loads(items_meta)
    except Exception:
        meta_list = [{}] * len(files)

    order_id = str(uuid.uuid4())[:8]
    order_folder = UPLOAD_DIR / order_id
    order_folder.mkdir(parents=True, exist_ok=True)

    while True:
        pin = str(random.randint(1000, 9999))
        if pin not in PIN_TO_ORDER:
            break

    processed_items = []
    grand_total = 0.0
    total_pages_all = 0
    total_sheets_all = 0

    for idx, f in enumerate(files):
        meta = meta_list[idx] if idx < len(meta_list) else {}
        format_action = meta.get("format_action", "pdf")
        color_mode = meta.get("color_mode", "bw")
        duplex = meta.get("duplex", "single")
        copies = max(1, int(meta.get("copies", 1)))
        note = meta.get("note", "").strip()

        # Orijinal dosyayı kaydet
        safe_name = f.filename.replace(" ", "_")
        temp_dest = order_folder / f"{idx}_{safe_name}"
        with open(temp_dest, "wb") as buffer:
            shutil.copyfileobj(f.file, buffer)

        # Analiz ve Dönüştürme
        result = process_file_item(temp_dest, format_action, order_folder)
        pages = result["page_count"]

        # Kağıt / Yaprak Hesabı (Tek yön vs Çift yön)
        if duplex == "duplex":
            sheets = math.ceil(pages / 2)
            tariff_key = f"{color_mode}_duplex"
        else:
            sheets = pages
            tariff_key = f"{color_mode}_single"

        price_per_page = TARIFF.get(tariff_key, 1.50)
        item_total = round(pages * copies * price_per_page, 2)
        grand_total += item_total
        total_pages_all += (pages * copies)
        total_sheets_all += (sheets * copies)

        item_id = str(uuid.uuid4())[:6]
        processed_items.append({
            "item_id": item_id,
            "original_name": f.filename,
            "final_name": result["final_name"],
            "file_path": result["final_path"],
            "file_size": result["file_size"],
            "is_converted": result["is_converted"],
            "page_count": pages,
            "sheet_count": sheets,
            "color_mode": color_mode,
            "duplex": duplex,
            "copies": copies,
            "note": note,
            "unit_price": price_per_page,
            "item_total": item_total
        })

    created_at = datetime.now()
    expire_at = created_at + timedelta(minutes=15)

    order_data = {
        "order_id": order_id,
        "pin_code": pin,
        "customer_name": customer_name.strip(),
        "items": processed_items,
        "items_count": len(processed_items),
        "total_pages": total_pages_all,
        "total_sheets": total_sheets_all,
        "grand_total": round(grand_total, 2),
        "status": "BEKLIYOR", # BEKLIYOR -> BASILIYOR -> TAMAMLANDI
        "created_at": created_at.isoformat(),
        "expire_at": expire_at,
        "expires_in_seconds": 900
    }

    ORDERS[order_id] = order_data
    PIN_TO_ORDER[pin] = order_id

    # Kiosk Paneline anlık WebSocket bildirimi gönder
    kiosk_payload = {k: v for k, v in order_data.items() if k != "expire_at"}
    kiosk_payload["expire_timestamp"] = int(expire_at.timestamp())
    
    await manager.broadcast_kiosk({
        "event": "NEW_ORDER",
        "order": kiosk_payload
    })

    return {
        "success": True,
        "order_id": order_id,
        "pin_code": pin,
        "items_count": len(processed_items),
        "total_pages": total_pages_all,
        "total_sheets": total_sheets_all,
        "grand_total": round(grand_total, 2),
        "expire_at": expire_at.isoformat()
    }

@app.get("/api/orders")
def list_orders():
    result = []
    now = datetime.now()
    for oid, o in ORDERS.items():
        rem = max(0, int((o["expire_at"] - now).total_seconds()))
        item = {k: v for k, v in o.items() if k != "expire_at"}
        item["remaining_seconds"] = rem
        result.append(item)
    return result

@app.get("/api/orders/{order_id}/items/{item_id}/file")
def get_item_file(order_id: str, item_id: str):
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı veya süresi doldu.")
    order = ORDERS[order_id]
    item = next((it for it in order["items"] if it["item_id"] == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Dosya bulunamadı.")
    path = Path(item["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Dosya imha edilmiş.")
    return FileResponse(
        path,
        filename=item["final_name"],
        headers={"Content-Disposition": f"inline; filename={item['final_name']}"}
    )

@app.get("/api/orders/{order_id}/zip")
def download_all_zip(order_id: str):
    """Siparişteki tüm dosyaları tek bir ZIP arşivi olarak indirir."""
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    order = ORDERS[order_id]
    zip_path = UPLOAD_DIR / order_id / f"Siparis_{order['pin_code']}_{order['customer_name']}.zip"
    with zipfile.ZipFile(zip_path, 'w') as z:
        for it in order["items"]:
            fpath = Path(it["file_path"])
            if fpath.exists():
                z.write(fpath, arcname=it["final_name"])
    return FileResponse(
        zip_path,
        filename=zip_path.name,
        headers={"Content-Disposition": f"attachment; filename={zip_path.name}"}
    )

@app.patch("/api/orders/{order_id}/status")
async def update_status(order_id: str, data: dict):
    if order_id not in ORDERS:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı.")
    new_status = data.get("status", "BEKLIYOR")
    ORDERS[order_id]["status"] = new_status
    pin = ORDERS[order_id]["pin_code"]

    await manager.broadcast_kiosk({
        "event": "STATUS_CHANGED",
        "order_id": order_id,
        "status": new_status
    })
    await manager.notify_mobile(pin, {
        "event": "STATUS_CHANGED",
        "status": new_status
    })
    return {"status": "ok", "new_status": new_status}

@app.delete("/api/orders/{order_id}")
async def delete_order(order_id: str):
    if order_id in ORDERS:
        order = ORDERS.pop(order_id)
        pin = order.get("pin_code")
        if pin in PIN_TO_ORDER:
            del PIN_TO_ORDER[pin]
        secure_shred_directory(UPLOAD_DIR / order_id)
        await manager.broadcast_kiosk({
            "event": "ORDER_DELETED",
            "order_id": order_id
        })
        await manager.notify_mobile(pin, {
            "event": "ORDER_DELETED",
            "message": "Siparişiniz kırtasiye tarafından tamamlandı ve silindi."
        })
    return {"status": "ok"}

# --- WEBSOCKETS ---

@app.websocket("/ws/kiosk")
async def ws_kiosk(websocket: WebSocket):
    await manager.connect_kiosk(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_kiosk(websocket)

@app.websocket("/ws/mobile/{pin_code}")
async def ws_mobile(websocket: WebSocket, pin_code: str):
    await manager.connect_mobile(pin_code, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_mobile(pin_code, websocket)

# ==============================================================================
# 6. KIOSK EKRANI (KIOSK HTML)
# ==============================================================================

@app.get("/kiosk", response_class=HTMLResponse)
def kiosk_page():
    return """
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link rel="icon" type="image/png" href="/assets/logo.png?v=2">
        <title>TakoTerminal | Kiosk Paneli</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
        <style>
            .pin-badge { font-family: monospace; letter-spacing: 2px; }
            ::-webkit-scrollbar { width: 6px; }
            ::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
        </style>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans select-none">
        <!-- ÜST BİLGİ ÇUBUĞU -->
        <header class="bg-slate-900/90 border-b border-slate-800 px-6 py-4 flex flex-wrap items-center justify-between gap-4 sticky top-0 z-50 backdrop-blur">
            <div class="flex items-center space-x-3.5">
                <img src="/assets/logo.png?v=2" class="w-12 h-12 rounded-2xl shadow-lg shadow-indigo-500/25 ring-1 ring-cyan-500/40 object-contain transition-transform hover:scale-105 duration-300" alt="TakoTerminal Logo">
                <div>
                    <h1 class="text-xl font-black bg-gradient-to-r from-indigo-300 via-sky-300 to-cyan-300 bg-clip-text text-transparent tracking-tight">TakoTerminal</h1>
                    <p class="text-xs text-slate-400">Canlı Kiosk & Çoklu Sipariş Takip Paneli</p>
                </div>
            </div>

            <div class="flex items-center space-x-3">
                <div class="flex items-center bg-slate-800 px-3 py-1.5 rounded-lg border border-slate-700 text-xs">
                    <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 mr-2 animate-ping"></span>
                    <span>Canlı Bağlantı</span>
                </div>
                <div class="bg-indigo-950/80 border border-indigo-700/60 px-3 py-1.5 rounded-lg text-xs font-medium text-indigo-300">
                    🛡️ KVKK 15 Dk İmha
                </div>
                <button id="tariff-btn" onclick="openTariffModal()" class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-3 py-1.5 rounded-lg font-semibold transition flex items-center space-x-1 cursor-pointer shadow">
                    <span>⚙️</span>
                    <span>Fiyat Tarifesi</span>
                </button>
                <button id="audio-enable-btn" onclick="toggleAudio()" class="bg-emerald-600 hover:bg-emerald-500 text-white text-xs px-3 py-1.5 rounded-lg font-semibold transition cursor-pointer shadow">
                    🔔 Zil Açık
                </button>
            </div>
        </header>

        <!-- ANA ALAN -->
        <div class="flex-1 flex flex-col lg:flex-row overflow-hidden">
            <!-- SOL PANEL: DİNAMİK QR KODU VE BAĞLANTI AYARI -->
            <div class="w-full lg:w-96 bg-slate-900 p-6 border-r border-slate-800 flex flex-col items-center justify-start space-y-6">
                <div class="w-full text-center flex flex-col items-center">
                    <div class="relative mb-3 group">
                        <div class="absolute -inset-1 bg-gradient-to-r from-indigo-500 to-cyan-500 rounded-3xl blur opacity-30 group-hover:opacity-60 transition duration-300"></div>
                        <img src="/assets/logo.png?v=2" class="relative w-28 h-28 rounded-3xl shadow-2xl object-contain border border-indigo-400/30 transition-transform group-hover:scale-105 duration-300" alt="TakoTerminal">
                    </div>
                    <span class="text-xs font-semibold text-indigo-400 uppercase tracking-wider">Müşteri Girişi İçin</span>
                    <h2 class="text-lg font-bold text-slate-200 mt-1">Kameranızla QR Kodu Okutun</h2>
                    <p class="text-xs text-slate-400 mt-1">Müşteriler 4G/5G mobil internetiyle bağlanabilir.</p>
                </div>

                <!-- QR KOD KUTUSU -->
                <div class="bg-white p-4 rounded-2xl shadow-2xl flex flex-col items-center border-4 border-indigo-500/30">
                    <div id="qrcode" class="w-48 h-48 flex items-center justify-center"></div>
                </div>

                <!-- HEDEF URL BİLGİSİ -->
                <div class="w-full bg-slate-950 p-4 rounded-xl border border-slate-800 text-xs space-y-2">
                    <div class="flex justify-between items-center text-slate-400">
                        <span>Aktif Mobil Link:</span>
                        <span id="link-type-badge" class="px-2 py-0.5 rounded text-[10px] bg-indigo-900 text-indigo-300">Tünel</span>
                    </div>
                    <div class="font-mono text-indigo-300 truncate font-semibold" id="current-mobile-url">Yükleniyor...</div>
                    <a id="mobile-direct-link" href="#" target="_blank" class="block text-center bg-slate-800 hover:bg-slate-700 py-1.5 rounded text-slate-300 font-medium">Linke Tıkla & Test Et ↗</a>
                </div>

                <!-- 4G/5G TÜNEL GİRİŞİ -->
                <div class="w-full bg-slate-800/60 p-4 rounded-xl border border-slate-700/60 text-xs space-y-2">
                    <label class="block font-semibold text-slate-300">🌐 Cloudflare 4G/5G Tünel URL:</label>
                    <input type="text" id="tunnel-input" placeholder="https://xxx.trycloudflare.com" class="w-full bg-slate-900 border border-slate-700 rounded px-3 py-1.5 text-slate-200 font-mono text-xs focus:outline-none focus:border-indigo-500">
                    <button onclick="saveTunnelUrl()" class="w-full bg-indigo-600 hover:bg-indigo-500 text-white py-1.5 rounded font-semibold transition">
                        QR Kodu Güncelle
                    </button>
                </div>

                <div class="mt-auto w-full text-center text-xs text-slate-500">
                    Bekleyen Sipariş: <span id="orders-count" class="font-bold text-indigo-400">0</span>
                </div>
            </div>

            <!-- SAĞ PANEL: SİPARİŞ LİSTESİ -->
            <div class="flex-1 bg-slate-950 p-6 overflow-y-auto">
                <div class="flex justify-between items-center mb-6">
                    <div>
                        <h2 class="text-xl font-bold text-slate-100">Gelen Baskı Talepleri</h2>
                        <p class="text-xs text-slate-400">Müşterinin gönderdiği tüm dosyalar ve detayları anında listelenir.</p>
                    </div>
                    <button onclick="loadOrders()" class="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs px-3 py-2 rounded-lg transition flex items-center space-x-1">
                        <span>🔄</span>
                        <span>Yenile</span>
                    </button>
                </div>

                <!-- SİPARİŞLER GRİDİ -->
                <div id="orders-grid" class="space-y-6">
                    <!-- Dinamik kartlar -->
                </div>

                <!-- BOŞ DURUM -->
                <div id="empty-state" class="hidden flex-col items-center justify-center py-24 text-center text-slate-500">
                    <span class="text-6xl mb-4">📭</span>
                    <h3 class="text-lg font-bold text-slate-300">Henüz Bekleyen Baskı Yok</h3>
                    <p class="text-xs text-slate-500 max-w-sm mt-1">Müşteriler QR kodu okutup dosya gönderdiğinde burada zil sesiyle görünecektir.</p>
                </div>
            </div>
        </div>

        <!-- FİYAT TARİFESİ MODALI -->
        <div id="tariff-modal" class="hidden fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
            <div class="bg-slate-900 border border-slate-700 rounded-3xl p-6 max-w-md w-full shadow-2xl space-y-5">
                <div class="flex justify-between items-center border-b border-slate-800 pb-3">
                    <div class="flex items-center space-x-2">
                        <span class="text-2xl">⚙️</span>
                        <div>
                            <h3 class="text-base font-bold text-slate-100">Baskı Fiyat Tarifesi</h3>
                            <p class="text-xs text-slate-400">Belirlediğiniz fiyatlar anında müşteri telefonuna yansır</p>
                        </div>
                    </div>
                    <button onclick="closeTariffModal()" class="text-slate-400 hover:text-white text-xl">✕</button>
                </div>

                <div class="space-y-4 text-xs">
                    <!-- Siyah-Beyaz Fiyatları -->
                    <div class="bg-slate-950 p-3.5 rounded-2xl border border-slate-800 space-y-2.5">
                        <span class="font-bold text-slate-200 block text-xs">📄 Siyah-Beyaz Baskı (TL / Sayfa)</span>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="text-[11px] text-slate-400 block mb-1">Tek Yön:</label>
                                <div class="flex items-center bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5">
                                    <span class="text-slate-400 mr-1.5 font-bold">₺</span>
                                    <input type="number" step="0.10" id="inp-bw-single" class="w-full bg-transparent text-slate-100 font-bold text-sm focus:outline-none">
                                </div>
                            </div>
                            <div>
                                <label class="text-[11px] text-slate-400 block mb-1">Çift Yön (Arkalı Önlü):</label>
                                <div class="flex items-center bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5">
                                    <span class="text-slate-400 mr-1.5 font-bold">₺</span>
                                    <input type="number" step="0.10" id="inp-bw-duplex" class="w-full bg-transparent text-slate-100 font-bold text-sm focus:outline-none">
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Renkli Fiyatları -->
                    <div class="bg-slate-950 p-3.5 rounded-2xl border border-slate-800 space-y-2.5">
                        <span class="font-bold text-pink-300 block text-xs">🎨 Renkli Baskı (TL / Sayfa)</span>
                        <div class="grid grid-cols-2 gap-3">
                            <div>
                                <label class="text-[11px] text-slate-400 block mb-1">Tek Yön:</label>
                                <div class="flex items-center bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5">
                                    <span class="text-slate-400 mr-1.5 font-bold">₺</span>
                                    <input type="number" step="0.10" id="inp-color-single" class="w-full bg-transparent text-slate-100 font-bold text-sm focus:outline-none">
                                </div>
                            </div>
                            <div>
                                <label class="text-[11px] text-slate-400 block mb-1">Çift Yön (Arkalı Önlü):</label>
                                <div class="flex items-center bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5">
                                    <span class="text-slate-400 mr-1.5 font-bold">₺</span>
                                    <input type="number" step="0.10" id="inp-color-duplex" class="w-full bg-transparent text-slate-100 font-bold text-sm focus:outline-none">
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="flex items-center justify-end space-x-2 pt-2 border-t border-slate-800">
                    <button onclick="closeTariffModal()" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs font-semibold">Vazgeç</button>
                    <button onclick="saveTariffSettings()" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-bold transition flex items-center space-x-1 shadow-lg">
                        <span>💾</span>
                        <span>Fiyatları Kaydet ve Uygula</span>
                    </button>
                </div>
            </div>
        </div>

        <script>
            let currentUrl = "";
            let audioEnabled = localStorage.getItem('kiosk_audio_enabled') !== 'false';
            let audioCtx = null;
            let ordersData = [];
            let currentTariff = { bw_single: 1.50, bw_duplex: 1.25, color_single: 4.00, color_duplex: 3.40 };

            function toggleAudio() {
                audioEnabled = !audioEnabled;
                localStorage.setItem('kiosk_audio_enabled', audioEnabled ? 'true' : 'false');
                updateAudioBtnUI();
                if (audioEnabled) {
                    if (!audioCtx) {
                        try {
                            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                        } catch(e) {}
                    }
                    playChime();
                }
            }

            function updateAudioBtnUI() {
                const btn = document.getElementById('audio-enable-btn');
                if (!btn) return;
                if (audioEnabled) {
                    btn.innerText = "🔔 Zil Açık";
                    btn.className = "bg-emerald-600 hover:bg-emerald-500 text-white text-xs px-3 py-1.5 rounded-lg font-semibold transition cursor-pointer shadow";
                } else {
                    btn.innerText = "🔕 Zil Kapalı (Sessiz)";
                    btn.className = "bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs px-3 py-1.5 rounded-lg font-semibold transition cursor-pointer shadow";
                }
            }

            function playChime() {
                if (!audioEnabled) return;
                if (!audioCtx) {
                    try {
                        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                    } catch(e) { return; }
                }
                try {
                    const now = audioCtx.currentTime;
                    const osc1 = audioCtx.createOscillator();
                    const gain1 = audioCtx.createGain();
                    osc1.frequency.setValueAtTime(587.33, now);
                    gain1.gain.setValueAtTime(0.3, now);
                    gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.5);
                    osc1.connect(gain1);
                    gain1.connect(audioCtx.destination);
                    osc1.start(now);
                    osc1.stop(now + 0.5);

                    const osc2 = audioCtx.createOscillator();
                    const gain2 = audioCtx.createGain();
                    osc2.frequency.setValueAtTime(880.00, now + 0.15);
                    gain2.gain.setValueAtTime(0.3, now + 0.15);
                    gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.8);
                    osc2.connect(gain2);
                    gain2.connect(audioCtx.destination);
                    osc2.start(now + 0.15);
                    osc2.stop(now + 0.8);
                } catch(e) {
                    console.error("Chime error", e);
                }
            }

            function openTariffModal() {
                document.getElementById('inp-bw-single').value = (currentTariff.bw_single || 1.50).toFixed(2);
                document.getElementById('inp-bw-duplex').value = (currentTariff.bw_duplex || 1.25).toFixed(2);
                document.getElementById('inp-color-single').value = (currentTariff.color_single || 4.00).toFixed(2);
                document.getElementById('inp-color-duplex').value = (currentTariff.color_duplex || 3.40).toFixed(2);
                document.getElementById('tariff-modal').classList.remove('hidden');
            }

            function closeTariffModal() {
                document.getElementById('tariff-modal').classList.add('hidden');
            }

            async function saveTariffSettings() {
                const payload = {
                    bw_single: parseFloat(document.getElementById('inp-bw-single').value) || 1.50,
                    bw_duplex: parseFloat(document.getElementById('inp-bw-duplex').value) || 1.25,
                    color_single: parseFloat(document.getElementById('inp-color-single').value) || 4.00,
                    color_duplex: parseFloat(document.getElementById('inp-color-duplex').value) || 3.40
                };

                try {
                    const res = await fetch('/api/tariff', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data.status === 'ok') {
                        currentTariff = data.tariff;
                        closeTariffModal();
                        alert("✅ Fiyat tarifesi başarıyla güncellendi! Müşteri ekranlarına canlı olarak yansıtıldı.");
                    }
                } catch(e) {
                    alert("Fiyatlar kaydedilemedi: " + e);
                }
            }

            let qrCodeObj = null;
            function updateQRCode(url) {
                currentUrl = url;
                document.getElementById('current-mobile-url').innerText = url;
                document.getElementById('mobile-direct-link').href = url;

                const qrContainer = document.getElementById('qrcode');
                qrContainer.innerHTML = "";
                qrCodeObj = new QRCode(qrContainer, {
                    text: url,
                    width: 192,
                    height: 192,
                    colorDark : "#0f172a",
                    colorLight : "#ffffff",
                    correctLevel : QRCode.CorrectLevel.M
                });
            }

            async function fetchConfig() {
                try {
                    const res = await fetch('/api/config');
                    const data = await res.json();
                    
                    if (data.tariff) {
                        currentTariff = data.tariff;
                    }

                    let target = "";
                    if (data.tunnel_url && data.tunnel_url.startsWith("http")) {
                        target = data.tunnel_url + "/mobile";
                        document.getElementById('tunnel-input').value = data.tunnel_url;
                        document.getElementById('link-type-badge').innerText = "Cloudflare 4G/5G";
                        document.getElementById('link-type-badge').className = "px-2 py-0.5 rounded text-[10px] bg-emerald-900 text-emerald-300";
                    } else {
                        target = "http://" + data.local_ip + ":8000/mobile";
                        document.getElementById('link-type-badge').innerText = "Yerel Wi-Fi";
                        document.getElementById('link-type-badge').className = "px-2 py-0.5 rounded text-[10px] bg-indigo-900 text-indigo-300";
                    }
                    updateQRCode(target);
                } catch(e) {
                    console.error("Config fetch error", e);
                }
            }

            async function saveTunnelUrl() {
                let url = document.getElementById('tunnel-input').value.trim();
                if (url.endsWith('/')) url = url.slice(0, -1);
                await fetch('/api/tunnel-url', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ url: url })
                });
                fetchConfig();
            }

            async function loadOrders() {
                try {
                    const res = await fetch('/api/orders');
                    ordersData = await res.json();
                    renderOrders();
                } catch(e) {
                    console.error("Load orders error", e);
                }
            }

            function renderOrders() {
                const grid = document.getElementById('orders-grid');
                const empty = document.getElementById('empty-state');
                document.getElementById('orders-count').innerText = ordersData.length;

                if (ordersData.length === 0) {
                    grid.innerHTML = "";
                    empty.classList.remove('hidden');
                    empty.classList.add('flex');
                    return;
                }

                empty.classList.add('hidden');
                empty.classList.remove('flex');

                grid.innerHTML = ordersData.map(o => {
                    const minutes = Math.floor(o.remaining_seconds / 60);
                    const seconds = (o.remaining_seconds % 60).toString().padStart(2, '0');

                    const itemsHtml = o.items.map((it, idx) => {
                        const isBw = it.color_mode === 'bw';
                        const colorBadge = isBw 
                            ? `<span class="bg-slate-800 text-slate-300 px-2 py-0.5 rounded text-[11px] font-semibold">Siyah-Beyaz</span>`
                            : `<span class="bg-pink-950 text-pink-300 border border-pink-800 px-2 py-0.5 rounded text-[11px] font-semibold">🎨 Renkli</span>`;
                        
                        const duplexBadge = it.duplex === 'duplex'
                            ? `<span class="bg-indigo-950 text-indigo-300 border border-indigo-800 px-2 py-0.5 rounded text-[11px] font-semibold">Arkalı-Önlü (${it.sheet_count} Yaprak)</span>`
                            : `<span class="bg-slate-800 text-slate-300 px-2 py-0.5 rounded text-[11px] font-semibold">Tek Yön (${it.sheet_count} Yaprak)</span>`;

                        const convertedBadge = it.is_converted
                            ? `<span class="bg-emerald-950 text-emerald-300 border border-emerald-800 px-1.5 py-0.5 rounded text-[10px]">PDF'e Çevrildi</span>`
                            : '';

                        return `
                        <div class="bg-slate-950 p-4 rounded-xl border border-slate-800 space-y-2">
                            <div class="flex flex-wrap items-center justify-between gap-2">
                                <div class="flex items-center space-x-2">
                                    <span class="text-base font-bold text-slate-400">#${idx + 1}</span>
                                    <span class="font-bold text-sm text-slate-100 truncate max-w-xs md:max-w-md" title="${it.final_name}">
                                        📄 ${it.final_name}
                                    </span>
                                    ${convertedBadge}
                                </div>
                                <div class="text-right">
                                    <span class="text-sm font-bold text-emerald-400">₺ ${it.item_total.toFixed(2)}</span>
                                </div>
                            </div>

                            <div class="flex flex-wrap items-center gap-2 pt-1">
                                <span class="bg-slate-800 text-slate-200 px-2 py-0.5 rounded text-[11px] font-bold">📑 ${it.page_count} Sayfa</span>
                                <span class="bg-slate-800 text-slate-200 px-2 py-0.5 rounded text-[11px] font-bold">✖️ ${it.copies} Kopya</span>
                                ${colorBadge}
                                ${duplexBadge}
                            </div>

                            ${it.note ? `
                            <div class="bg-amber-950/40 border border-amber-800/40 p-2 rounded-lg text-xs text-amber-200 mt-1">
                                <span class="font-bold">Özel Not:</span> "${it.note}"
                            </div>
                            ` : ''}

                            <div class="flex items-center justify-end space-x-2 pt-2 border-t border-slate-900">
                                <a href="/api/orders/${o.order_id}/items/${it.item_id}/file" target="_blank" class="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 rounded-lg text-xs font-bold transition flex items-center space-x-1">
                                    <span>👁️ Dosyayı Aç / Yazdır</span>
                                </a>
                            </div>
                        </div>
                        `;
                    }).join('');

                    return `
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-2xl relative space-y-5">
                        <!-- ÜST BAŞLIK -->
                        <div class="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
                            <div class="flex items-center space-x-4">
                                <div class="bg-indigo-600/20 border-2 border-indigo-500 px-4 py-1.5 rounded-2xl text-center">
                                    <span class="text-[10px] uppercase font-bold text-indigo-400 block tracking-wider">PIN KODU</span>
                                    <span class="text-3xl font-black text-indigo-300 pin-badge">${o.pin_code}</span>
                                </div>
                                <div>
                                    <h3 class="text-xl font-bold text-slate-100 flex items-center">
                                        <span class="mr-2">👤</span> ${o.customer_name}
                                    </h3>
                                    <div class="flex items-center space-x-3 text-xs text-slate-400 mt-0.5">
                                        <span>📦 Toplam ${o.items_count} Belge</span>
                                        <span>•</span>
                                        <span>📑 ${o.total_pages} Sayfa (${o.total_sheets} Yaprak Kağıt)</span>
                                    </div>
                                </div>
                            </div>

                            <div class="text-right">
                                <span class="text-2xl font-black text-emerald-400">₺ ${o.grand_total.toFixed(2)}</span>
                                <span class="block text-[11px] text-slate-400">Genel Toplam Tutar</span>
                                <span class="inline-block mt-1 font-mono text-xs font-bold text-amber-400 bg-amber-950/60 px-2 py-0.5 rounded border border-amber-800">
                                    ⏳ ${minutes}:${seconds} kaldı
                                </span>
                            </div>
                        </div>

                        <!-- DOSYA LİSTESİ -->
                        <div class="space-y-3">
                            ${itemsHtml}
                        </div>

                        <!-- ALT KONTROL PANELİ -->
                        <div class="flex flex-wrap items-center justify-between gap-3 pt-3 border-t border-slate-800 text-xs">
                            <div class="flex items-center space-x-2">
                                <span class="text-slate-400">Durum:</span>
                                <select onchange="updateOrderStatus('${o.order_id}', this.value)" class="bg-slate-950 border border-slate-700 rounded-lg px-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 font-semibold">
                                    <option value="BEKLIYOR" ${o.status === 'BEKLIYOR' ? 'selected' : ''}>⏳ Sıraya Alındı</option>
                                    <option value="BASILIYOR" ${o.status === 'BASILIYOR' ? 'selected' : ''}>🖨️ Yazdırılıyor</option>
                                    <option value="TAMAMLANDI" ${o.status === 'TAMAMLANDI' ? 'selected' : ''}>✅ Tamamlandı</option>
                                </select>
                            </div>

                            <div class="flex items-center space-x-3">
                                <a href="/api/orders/${o.order_id}/zip" class="bg-slate-800 hover:bg-slate-700 text-slate-200 px-3 py-2 rounded-lg font-semibold transition flex items-center space-x-1">
                                    <span>📦 Tümünü ZIP Olarak İndir</span>
                                </a>
                                <button onclick="deleteOrder('${o.order_id}')" class="bg-red-950/70 hover:bg-red-900 border border-red-800/60 text-red-200 px-4 py-2 rounded-lg font-semibold transition">
                                    🗑️ Siparişi İmha Et
                                </button>
                            </div>
                        </div>
                    </div>
                    `;
                }).join('');
            }

            async function updateOrderStatus(orderId, status) {
                await fetch(`/api/orders/${orderId}/status`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: status })
                });
            }

            async function deleteOrder(orderId) {
                if (!confirm("Bu siparişteki tüm dosyaları hemen imha etmek istiyor musunuz?")) return;
                await fetch(`/api/orders/${orderId}`, { method: 'DELETE' });
                loadOrders();
            }

            function setupWebSocket() {
                const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const ws = new WebSocket(`${proto}//${window.location.host}/ws/kiosk`);

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.event === "NEW_ORDER") {
                        playChime();
                        loadOrders();
                    } else if (data.event === "ORDER_EXPIRED" || data.event === "ORDER_DELETED" || data.event === "STATUS_CHANGED") {
                        loadOrders();
                    } else if (data.event === "TARIFF_UPDATED" && data.tariff) {
                        currentTariff = data.tariff;
                    }
                };

                ws.onclose = () => {
                    setTimeout(setupWebSocket, 3000);
                };
            }

            window.addEventListener('DOMContentLoaded', () => {
                updateAudioBtnUI();
                fetchConfig();
                loadOrders();
                setupWebSocket();
                setInterval(loadOrders, 10000);
            });
        </script>
    </body>
    </html>
    """

# ==============================================================================
# 7. MÜŞTERİ ÇOKLU DOSYA MOBİL ARAYÜZÜ (MOBILE HTML)
# ==============================================================================

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return """
    <!DOCTYPE html>
    <html lang="tr">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <link rel="icon" type="image/png" href="/assets/logo.png?v=2">
        <link rel="apple-touch-icon" href="/assets/logo.png?v=2">
        <title>TakoTerminal | Kolay Baskı</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            .pin-display { font-family: monospace; letter-spacing: 6px; }
            input[type="number"]::-webkit-inner-spin-button, 
            input[type="number"]::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
        </style>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col font-sans select-none antialiased">
        <!-- MOBİL ÜST BAR -->
        <header class="bg-slate-950/95 backdrop-blur-md border-b border-slate-800/80 px-4 py-3 sticky top-0 z-30 shadow-lg shadow-black/40">
            <div class="max-w-md mx-auto flex items-center justify-between">
                <div class="flex items-center space-x-3">
                    <img src="/assets/logo.png?v=2" class="w-11 h-11 rounded-xl shadow-md shadow-indigo-500/25 ring-1 ring-cyan-500/40 object-contain" alt="TakoTerminal">
                    <div>
                        <div class="flex items-center space-x-1.5">
                            <span class="text-base font-black tracking-tight bg-gradient-to-r from-indigo-300 via-sky-300 to-cyan-300 bg-clip-text text-transparent">TakoTerminal</span>
                            <span class="text-[9px] uppercase font-bold px-1.5 py-0.5 rounded-full bg-cyan-950 text-cyan-300 border border-cyan-800/60">Mobil</span>
                        </div>
                        <p class="text-[11px] text-slate-400 leading-tight">Temassız Akıllı Baskı Noktası</p>
                    </div>
                </div>
                <div class="flex items-center space-x-1.5 text-[11px] bg-slate-900 border border-slate-800 px-2.5 py-1 rounded-full text-emerald-400 font-medium">
                    <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                    <span>Canlı</span>
                </div>
            </div>
        </header>

        <div class="flex-1 p-4 max-w-md mx-auto w-full space-y-4">
            <!-- ADIM 1 & FORM KUTUSU -->
            <div id="form-container" class="space-y-4">
                <!-- MARKA / KARŞILAMA HERO KARTI -->
                <div class="bg-gradient-to-b from-slate-950 via-slate-900 to-slate-950 p-4 rounded-2xl border border-indigo-500/20 shadow-xl text-center flex flex-col items-center space-y-2">
                    <div class="relative group">
                        <div class="absolute -inset-1 bg-gradient-to-r from-indigo-500 to-cyan-500 rounded-2xl blur opacity-30"></div>
                        <img src="/assets/logo.png?v=2" class="relative w-24 h-24 rounded-2xl shadow-xl object-contain border border-indigo-400/30" alt="TakoTerminal Logo">
                    </div>
                    <div>
                        <h2 class="text-sm font-bold text-slate-100">Belgelerinizi Güvenle Yükleyin</h2>
                        <p class="text-xs text-slate-400 mt-0.5">Yükleyin, baskı ayarlarınızı yapın, PIN koduyla anında teslim alın.</p>
                    </div>
                    <div class="flex flex-wrap items-center justify-center gap-1.5 pt-1 text-[10px] text-indigo-300 font-medium">
                        <span class="bg-indigo-950/80 px-2 py-0.5 rounded-md border border-indigo-800/40">⚡ Hızlı Aktarım</span>
                        <span class="bg-cyan-950/80 px-2 py-0.5 rounded-md border border-cyan-800/40">🛡️ KVKK 15 Dk İmha</span>
                        <span class="bg-slate-800 px-2 py-0.5 rounded-md border border-slate-700">📑 Çoklu Belge</span>
                    </div>
                </div>

                <!-- MÜŞTERİ ADI -->
                <div class="bg-slate-800/90 p-4 rounded-2xl border border-slate-700/60 shadow-lg">
                    <label class="block text-xs font-bold text-slate-300 uppercase tracking-wider mb-1.5">Müşteri Adınız ve Soyadınız <span class="text-rose-400">*</span></label>
                    <input type="text" id="cust-name" placeholder="Örn: Ahmet Yılmaz" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-4 py-3 text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 text-sm">
                </div>

                <!-- DOSYALAR LİSTESİ BAŞLIĞI -->
                <div class="flex justify-between items-center px-1">
                    <h3 class="text-xs font-bold text-slate-300 uppercase tracking-wider">Yüklenecek Belgeler (<span id="file-counter">0</span>)</h3>
                    <button onclick="document.getElementById('file-input').click()" class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-3 py-1.5 rounded-xl transition flex items-center space-x-1 shadow">
                        <span>➕ Dosya Ekle</span>
                    </button>
                </div>

                <!-- GİZLİ INPUT -->
                <input type="file" id="file-input" multiple accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.webp,.bmp,.txt" class="hidden" onchange="addFiles(this.files)">

                <!-- BOŞ DOSYA DURUMU / YÜKLEME KUTUSU -->
                <div id="upload-placeholder" onclick="document.getElementById('file-input').click()" class="border-2 border-dashed border-indigo-500/40 hover:border-indigo-400 bg-slate-800/40 rounded-2xl p-8 text-center cursor-pointer transition">
                    <span class="text-4xl block mb-2">📁</span>
                    <span class="text-sm font-bold text-indigo-300 block">Belge veya Fotoğraf Yükleyin</span>
                    <span class="text-xs text-slate-400 mt-1 block">PDF, Word (DOCX), JPG, PNG</span>
                    <span class="inline-block mt-3 bg-indigo-950/80 border border-indigo-700/60 text-indigo-300 text-xs px-3 py-1 rounded-lg font-medium">Tek seferde birden fazla dosya ekleyebilirsiniz</span>
                </div>

                <!-- DOSYA KARTLARI LİSTESİ -->
                <div id="file-cards-list" class="space-y-4">
                    <!-- Dinamik kartlar buraya eklenecek -->
                </div>

                <!-- DAHA FAZLA DOSYA EKLE BUTONU (LİSTE DOLUYSA) -->
                <div id="add-more-container" class="hidden">
                    <button onclick="document.getElementById('file-input').click()" class="w-full border-2 border-dashed border-slate-700 hover:border-slate-500 py-3 rounded-2xl text-xs text-slate-300 font-bold transition flex items-center justify-center space-x-1">
                        <span>➕ Başka Bir Belge Daha Ekle</span>
                    </button>
                </div>

                <!-- GENEL SİPARİŞ ÖZETİ -->
                <div id="summary-box" class="hidden bg-indigo-950/60 border border-indigo-500/40 p-4 rounded-2xl space-y-2">
                    <div class="flex justify-between items-center text-xs text-indigo-200">
                        <span id="summary-items-text">0 Belge</span>
                        <span id="summary-sheets-text" class="text-indigo-300 font-semibold">0 Yaprak Kağıt</span>
                    </div>
                    <div class="flex justify-between items-center pt-2 border-t border-indigo-900/80">
                        <div>
                            <span class="text-xs text-slate-400 block">Genel Toplam Tutar</span>
                            <span class="text-[10px] text-emerald-400">Çift yön baskılarda yaprak tasarrufu indirimi dahildir</span>
                        </div>
                        <div class="text-right">
                            <span id="grand-total-price" class="text-2xl font-black text-emerald-400">₺ 0.00</span>
                        </div>
                    </div>
                </div>

                <!-- GÖNDER BUTONU -->
                <button id="submit-btn" onclick="submitMultiOrder()" class="hidden w-full bg-gradient-to-r from-indigo-500 to-indigo-600 hover:from-indigo-600 hover:to-indigo-700 text-white font-bold py-4 rounded-2xl shadow-xl transition transform active:scale-95 flex items-center justify-center space-x-2 text-base">
                    <span>🚀</span>
                    <span>Tümünü Baskıya Gönder</span>
                </button>
            </div>

            <!-- ADIM 2: BAŞARI VE PIN KODU EKRANI -->
            <div id="success-container" class="hidden space-y-5 text-center pt-2">
                <div class="relative inline-block mx-auto">
                    <div class="absolute -inset-1 bg-gradient-to-r from-emerald-500 to-cyan-500 rounded-3xl blur opacity-40"></div>
                    <img src="/assets/logo.png?v=2" class="relative w-24 h-24 rounded-3xl shadow-2xl object-contain border-2 border-emerald-400/40" alt="TakoTerminal">
                    <div class="absolute -bottom-1 -right-1 bg-emerald-500 text-slate-950 rounded-full w-7 h-7 flex items-center justify-center font-bold text-sm shadow-lg ring-2 ring-slate-900">
                        ✓
                    </div>
                </div>

                <div>
                    <h2 class="text-xl font-bold text-slate-100">Baskı Talebiniz İletildi!</h2>
                    <p class="text-xs text-slate-400 mt-1">Lütfen aşağıdaki 4 haneli PIN kodunu kırtasiye görevlisine söyleyiniz.</p>
                </div>

                <!-- PIN KODU KUTUSU -->
                <div class="bg-slate-800 p-6 rounded-3xl border-2 border-indigo-500 shadow-2xl">
                    <span class="text-xs uppercase font-bold text-indigo-400 tracking-widest block mb-1">GÜVENLİK PIN KODUNUZ</span>
                    <span id="success-pin" class="text-5xl font-black text-indigo-300 pin-display tracking-widest block py-2">----</span>
                    <span class="text-[11px] text-slate-400 block mt-2">Bu kod tüm gönderdiğiniz belgeler için geçerlidir.</span>
                </div>

                <!-- GERİ SAYIM SAYACI -->
                <div class="bg-slate-800/80 p-3 rounded-2xl border border-slate-700 flex items-center justify-between text-xs px-4">
                    <span class="text-slate-400">KVKK Kalan Süre:</span>
                    <span id="countdown-timer" class="font-bold text-amber-400 font-mono text-sm">15:00</span>
                </div>

                <!-- CANLI DURUM ÇUBUĞU -->
                <div class="bg-slate-800/60 p-4 rounded-2xl border border-slate-700/60 text-left space-y-2">
                    <span class="text-xs font-bold text-slate-300 block">Canlı Durum:</span>
                    <div class="flex items-center space-x-3">
                        <span id="status-icon" class="text-xl">⏳</span>
                        <div>
                            <div id="status-text" class="text-xs font-bold text-amber-300">Sıraya Alındı</div>
                            <div class="text-[10px] text-slate-400">Kırtasiye görevlisi belgelerinizi hazırlıyor.</div>
                        </div>
                    </div>
                </div>

                <button onclick="resetApp()" class="text-xs text-slate-400 underline pt-2">Yeni Baskı Gönder</button>
            </div>
        </div>

        <script>
            // Çoklu dosya veri havuzu
            let fileEntries = [];
            let activePin = null;
            let timerInterval = null;
            let remainingSeconds = 900;

            let TARIFF = {
                bw_single: 1.50,
                bw_duplex: 1.25,
                color_single: 4.00,
                color_duplex: 3.40
            };

            async function fetchMobileConfig() {
                try {
                    const res = await fetch('/api/config');
                    const data = await res.json();
                    if (data.tariff) {
                        TARIFF = data.tariff;
                        renderFileCards();
                    }
                } catch(e) {}
            }
            window.addEventListener('DOMContentLoaded', fetchMobileConfig);

            function addFiles(files) {
                if (!files || files.length === 0) return;
                
                for (let f of files) {
                    const id = 'f_' + Math.random().toString(36).substr(2, 9);
                    const ext = f.name.split('.').pop().toLowerCase();
                    const isDocx = (ext === 'docx' || ext === 'doc');
                    const isImg = ['jpg', 'jpeg', 'png', 'webp', 'bmp'].includes(ext);

                    fileEntries.push({
                        id: id,
                        file: f,
                        ext: ext,
                        isDocx: isDocx,
                        isImg: isImg,
                        format_action: (isDocx || isImg) ? 'pdf' : 'keep',
                        color_mode: 'bw',
                        duplex: 'single',
                        copies: 1,
                        note: '',
                        est_pages: 1 // Sunucuda netleşir
                    });
                }
                
                // Formu temizle ki aynı dosya tekrar seçilebilsin
                document.getElementById('file-input').value = '';
                renderFileCards();
            }

            function removeFile(id) {
                fileEntries = fileEntries.filter(e => e.id !== id);
                renderFileCards();
            }

            function updateFileOption(id, key, val) {
                const entry = fileEntries.find(e => e.id === id);
                if (entry) {
                    entry[key] = val;
                    renderFileCards();
                }
            }

            function changeCopies(id, delta) {
                const entry = fileEntries.find(e => e.id === id);
                if (entry) {
                    entry.copies = Math.max(1, Math.min(999, entry.copies + delta));
                    const inp = document.getElementById(`copy-input-${id}`);
                    if (inp) inp.value = entry.copies;
                    updateLiveTotals();
                }
            }

            function setCopiesDirectly(id, val) {
                const entry = fileEntries.find(e => e.id === id);
                if (entry) {
                    let num = parseInt(val);
                    if (isNaN(num) || num < 1) num = 1;
                    if (num > 999) num = 999;
                    entry.copies = num;
                    updateLiveTotals();
                }
            }

            function updateLiveTotals() {
                let grandTotal = 0.0;
                let totalSheets = 0;
                for (let e of fileEntries) {
                    const priceKey = `${e.color_mode}_${e.duplex}`;
                    const unitPrice = TARIFF[priceKey] || 1.50;
                    const sheetsPerCopy = e.duplex === 'duplex' ? Math.ceil(e.est_pages / 2) : e.est_pages;
                    const itemSheets = sheetsPerCopy * e.copies;
                    const itemTotal = (e.est_pages * e.copies * unitPrice);
                    grandTotal += itemTotal;
                    totalSheets += itemSheets;
                    
                    const priceEl = document.getElementById(`price-val-${e.id}`);
                    if (priceEl) priceEl.innerText = `Tahmini: ₺ ${itemTotal.toFixed(2)}`;
                }
                const summarySheets = document.getElementById('summary-sheets-text');
                const grandTotalEl = document.getElementById('grand-total-price');
                if (summarySheets) summarySheets.innerText = `Tahmini ${totalSheets} Yaprak Kağıt`;
                if (grandTotalEl) grandTotalEl.innerText = `₺ ${grandTotal.toFixed(2)}`;
            }

            function renderFileCards() {
                const list = document.getElementById('file-cards-list');
                const placeholder = document.getElementById('upload-placeholder');
                const addMore = document.getElementById('add-more-container');
                const summaryBox = document.getElementById('summary-box');
                const submitBtn = document.getElementById('submit-btn');
                document.getElementById('file-counter').innerText = fileEntries.length;

                if (fileEntries.length === 0) {
                    list.innerHTML = '';
                    placeholder.classList.remove('hidden');
                    addMore.classList.add('hidden');
                    summaryBox.classList.add('hidden');
                    submitBtn.classList.add('hidden');
                    return;
                }

                placeholder.classList.add('hidden');
                addMore.classList.remove('hidden');
                summaryBox.classList.remove('hidden');
                submitBtn.classList.remove('hidden');

                let grandTotal = 0.0;
                let totalSheets = 0;

                list.innerHTML = fileEntries.map((e, index) => {
                    const priceKey = `${e.color_mode}_${e.duplex}`;
                    const unitPrice = TARIFF[priceKey] || 1.50;
                    
                    // Tahmini yaprak hesabı: çift yönde yaprak = ceil(pages / 2)
                    const sheetsPerCopy = e.duplex === 'duplex' ? Math.ceil(e.est_pages / 2) : e.est_pages;
                    const itemSheets = sheetsPerCopy * e.copies;
                    const itemTotal = (e.est_pages * e.copies * unitPrice);

                    grandTotal += itemTotal;
                    totalSheets += itemSheets;

                    return `
                    <div class="bg-slate-800/95 p-4 rounded-2xl border border-slate-700 shadow-xl space-y-3 relative">
                        <!-- ÜST BAŞLIK & SİL -->
                        <div class="flex items-start justify-between">
                            <div class="flex items-center space-x-2">
                                <span class="bg-indigo-600/30 text-indigo-300 font-bold text-xs px-2 py-0.5 rounded-lg">#${index + 1}</span>
                                <div>
                                    <h4 class="text-xs font-bold text-slate-100 truncate max-w-[210px]" title="${e.file.name}">
                                        ${e.file.name}
                                    </h4>
                                    <span class="text-[10px] text-slate-400">${(e.file.size / (1024*1024)).toFixed(2)} MB</span>
                                </div>
                            </div>
                            <button onclick="removeFile('${e.id}')" class="text-slate-400 hover:text-rose-400 text-sm p-1 transition" title="Kaldır">
                                ✖
                            </button>
                        </div>

                        <!-- FORMAT DÖNÜŞTÜRME SEÇENEĞİ (WORD VEYA RESİMLER İÇİN) -->
                        ${(e.isDocx || e.isImg) ? `
                        <div class="bg-slate-900/80 p-2.5 rounded-xl border border-slate-800 text-xs">
                            <span class="text-[11px] font-semibold text-slate-300 block mb-1">🔄 Format Seçimi:</span>
                            <div class="grid grid-cols-2 gap-1.5">
                                <button type="button" onclick="updateFileOption('${e.id}', 'format_action', 'pdf')" class="py-1.5 px-2 rounded-lg text-[11px] font-bold transition ${e.format_action === 'pdf' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400'}">
                                    📄 PDF'e Dönüştür
                                </button>
                                <button type="button" onclick="updateFileOption('${e.id}', 'format_action', 'keep')" class="py-1.5 px-2 rounded-lg text-[11px] font-bold transition ${e.format_action === 'keep' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400'}">
                                    📁 Orijinal Bırak
                                </button>
                            </div>
                        </div>
                        ` : ''}

                        <!-- RENK SEÇİMİ -->
                        <div>
                            <span class="text-[11px] text-slate-400 block mb-1 font-semibold">Renk Tercihi:</span>
                            <div class="grid grid-cols-2 gap-2">
                                <button type="button" onclick="updateFileOption('${e.id}', 'color_mode', 'bw')" class="py-2 rounded-xl text-xs font-bold border transition ${e.color_mode === 'bw' ? 'bg-slate-700 text-white border-indigo-500' : 'bg-slate-900 text-slate-400 border-slate-700'}">
                                    Siyah-Beyaz (₺${(e.duplex === 'duplex' ? TARIFF.bw_duplex : TARIFF.bw_single).toFixed(2)})
                                </button>
                                <button type="button" onclick="updateFileOption('${e.id}', 'color_mode', 'color')" class="py-2 rounded-xl text-xs font-bold border transition ${e.color_mode === 'color' ? 'bg-pink-950 text-pink-200 border-pink-500' : 'bg-slate-900 text-slate-400 border-slate-700'}">
                                    🎨 Renkli (₺${(e.duplex === 'duplex' ? TARIFF.color_duplex : TARIFF.color_single).toFixed(2)})
                                </button>
                            </div>
                        </div>

                        <!-- YÖN VE KOPYA -->
                        <div class="grid grid-cols-2 gap-2">
                            <div>
                                <span class="text-[11px] text-slate-400 block mb-1 font-semibold">Baskı Yönü:</span>
                                <select onchange="updateFileOption('${e.id}', 'duplex', this.value)" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-2.5 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500">
                                    <option value="single" ${e.duplex === 'single' ? 'selected' : ''}>Tek Yön (₺${(e.color_mode === 'color' ? TARIFF.color_single : TARIFF.bw_single).toFixed(2)}/syf)</option>
                                    <option value="duplex" ${e.duplex === 'duplex' ? 'selected' : ''}>Çift Yön / Arkalı Önlü (₺${(e.color_mode === 'color' ? TARIFF.color_duplex : TARIFF.bw_duplex).toFixed(2)}/syf)</option>
                                </select>
                            </div>

                            <div>
                                <span class="text-[11px] text-slate-400 block mb-1 font-semibold">Kopya:</span>
                                <div class="flex items-center bg-slate-900 border border-slate-700 rounded-xl overflow-hidden focus-within:border-indigo-500">
                                    <button type="button" onclick="changeCopies('${e.id}', -1)" class="w-8 py-1.5 text-slate-400 font-bold hover:bg-slate-800 text-sm select-none">-</button>
                                    <input type="number" id="copy-input-${e.id}" min="1" max="999" value="${e.copies}" oninput="setCopiesDirectly('${e.id}', this.value)" class="w-full bg-transparent text-center text-xs font-bold text-slate-100 focus:outline-none py-1.5 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none">
                                    <button type="button" onclick="changeCopies('${e.id}', 1)" class="w-8 py-1.5 text-slate-400 font-bold hover:bg-slate-800 text-sm select-none">+</button>
                                </div>
                            </div>
                        </div>

                        <!-- BU BELGEYE ÖZEL NOT -->
                        <div>
                            <input type="text" value="${e.note}" oninput="fileEntries.find(it => it.id === '${e.id}').note = this.value" placeholder="Bu belge için özel not (Opsiyonel)" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-slate-200 placeholder-slate-500 text-xs focus:outline-none focus:border-indigo-500">
                        </div>

                        <!-- BU KARTIN HESAPLANAN ÖZETİ -->
                        <div class="flex justify-between items-center pt-2 border-t border-slate-700/60 text-xs">
                            <span class="text-slate-400 text-[11px]">
                                ${e.duplex === 'duplex' ? 'Arkalı Önlü Yaprak Hesabı' : 'Tek Yön Kağıt Hesabı'}
                            </span>
                            <span id="price-val-${e.id}" class="font-bold text-emerald-400">
                                Tahmini: ₺ ${itemTotal.toFixed(2)}
                            </span>
                        </div>
                    </div>
                    `;
                }).join('');

                document.getElementById('summary-items-text').innerText = `${fileEntries.length} Belge Seçildi`;
                document.getElementById('summary-sheets-text').innerText = `Tahmini ${totalSheets} Yaprak Kağıt`;
                document.getElementById('grand-total-price').innerText = `₺ ${grandTotal.toFixed(2)}`;
            }

            async function submitMultiOrder() {
                const name = document.getElementById('cust-name').value.trim();
                if (!name) {
                    alert("Lütfen adınızı ve soyadınızı yazınız.");
                    document.getElementById('cust-name').focus();
                    return;
                }
                if (fileEntries.length === 0) {
                    alert("Lütfen en az bir belge seçiniz.");
                    return;
                }

                const btn = document.getElementById('submit-btn');
                btn.disabled = true;
                btn.innerHTML = `<span class="animate-spin mr-2">🔄</span> Belgeler İşleniyor ve Yükleniyor...`;

                const formData = new FormData();
                formData.append('customer_name', name);

                const metaPayload = fileEntries.map(e => ({
                    format_action: e.format_action,
                    color_mode: e.color_mode,
                    duplex: e.duplex,
                    copies: e.copies,
                    note: e.note
                }));

                formData.append('items_meta', JSON.stringify(metaPayload));

                for (let e of fileEntries) {
                    formData.append('files', e.file);
                }

                try {
                    const res = await fetch('/api/upload', {
                        method: 'POST',
                        body: formData
                    });
                    const data = await res.json();
                    if (data.success) {
                        activePin = data.pin_code;
                        showSuccess(data);
                    } else {
                        alert("Hata: " + (data.detail || "Dosyalar yüklenemedi."));
                        btn.disabled = false;
                        btn.innerHTML = `<span>🚀</span> <span>Tümünü Baskıya Gönder</span>`;
                    }
                } catch(e) {
                    alert("Sunucuya bağlanılamadı. Lütfen bağlantınızı kontrol edin.");
                    btn.disabled = false;
                    btn.innerHTML = `<span>🚀</span> <span>Tümünü Baskıya Gönder</span>`;
                }
            }

            function showSuccess(data) {
                document.getElementById('form-container').classList.add('hidden');
                document.getElementById('success-container').classList.remove('hidden');
                document.getElementById('success-pin').innerText = data.pin_code;

                remainingSeconds = 900;
                startCountdown();
                connectStatusWs(data.pin_code);
            }

            function startCountdown() {
                clearInterval(timerInterval);
                timerInterval = setInterval(() => {
                    remainingSeconds--;
                    if (remainingSeconds <= 0) {
                        clearInterval(timerInterval);
                        document.getElementById('countdown-timer').innerText = "SÜRE DOLDU";
                        return;
                    }
                    const m = Math.floor(remainingSeconds / 60).toString().padStart(2, '0');
                    const s = (remainingSeconds % 60).toString().padStart(2, '0');
                    document.getElementById('countdown-timer').innerText = `${m}:${s}`;
                }, 1000);
            }

            function connectStatusWs(pin) {
                const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const ws = new WebSocket(`${proto}//${window.location.host}/ws/mobile/${pin}`);

                ws.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.event === "STATUS_CHANGED") {
                        if (data.status === "BASILIYOR") {
                            document.getElementById('status-icon').innerText = "🖨️";
                            document.getElementById('status-text').innerText = "Yazdırılıyor...";
                            document.getElementById('status-text').className = "text-xs font-bold text-cyan-300";
                        } else if (data.status === "TAMAMLANDI") {
                            document.getElementById('status-icon').innerText = "🎉";
                            document.getElementById('status-text').innerText = "Baskı Hazır! Teslim Alabilirsiniz.";
                            document.getElementById('status-text').className = "text-xs font-bold text-emerald-300";
                        }
                    } else if (data.event === "TARIFF_UPDATED" && data.tariff) {
                        TARIFF = data.tariff;
                        renderFileCards();
                    } else if (data.event === "ORDER_DELETED" || data.event === "ORDER_EXPIRED") {
                        document.getElementById('status-icon').innerText = "🗑️";
                        document.getElementById('status-text').innerText = "Belgeleriniz Güvenle İmha Edildi.";
                        document.getElementById('status-text').className = "text-xs font-bold text-slate-400";
                        clearInterval(timerInterval);
                    }
                };
            }

            function resetApp() {
                window.location.reload();
            }
        </script>
    </body>
    </html>
    """

# ==============================================================================
# 8. ÇALIŞTIRMA NOKTASI
# ==============================================================================

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*60)
    print("🚀 Kırtasiye Çoklu Baskı Kiosk Sunucusu Başlatılıyor...")
    print(f"👉 Yerel Ağ (Wi-Fi) Adresi: http://{SERVER_STATE['local_ip']}:8000/kiosk")
    print(f"👉 Müşteri Mobil Arayüzü:   http://{SERVER_STATE['local_ip']}:8000/mobile")
    if SERVER_STATE["tunnel_url"]:
        print(f"👉 4G/5G Tünel Adresi:      {SERVER_STATE['tunnel_url']}/mobile")
    print("="*60 + "\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)

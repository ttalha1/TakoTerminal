import os
import sys
import time
import re
import socket
import shutil
import tempfile
import subprocess
import threading
import webbrowser
from pathlib import Path

# PyInstaller --windowed / --noconsole modunda stdout/stderr koruması
if sys.stdout is None:
    try:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass
if sys.stderr is None:
    try:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    except Exception:
        pass

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

TEMP_DIR = Path(tempfile.gettempdir())
TUNNEL_TEMP_FILE = TEMP_DIR / "takoterminal_tunnel.txt"
CF_LOG_FILE = TEMP_DIR / "takoterminal_cloudflared.log"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def cleanup_zombies_and_port():
    """Önceki oturumlardan kalan cloudflared ve port 8000 süreçlerini temizler."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        try:
            current_pid = os.getpid()
            cmd = f'powershell -WindowStyle Hidden -Command "Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object {{ $_ -ne {current_pid} }} | ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}"'
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

def get_cloudflared_executable() -> Path:
    """Paketlenmiş veya sistemdeki cloudflared.exe yolunu döndürür."""
    # 1. PyInstaller _MEIPASS içinde gömülüyse doğrudan oradan çalıştır (Masaüstüne kopyalamadan)
    if hasattr(sys, "_MEIPASS"):
        meipass_cf = Path(sys._MEIPASS) / "cloudflared.exe"
        if meipass_cf.exists():
            return meipass_cf

    # 2. Programın çalıştığı klasörde varsa
    base_cf = BASE_DIR / "cloudflared.exe"
    if base_cf.exists():
        return base_cf

    # 3. Temp / AppData klasöründe varsa
    temp_cf = TEMP_DIR / "takoterminal_cloudflared.exe"
    if temp_cf.exists():
        return temp_cf

    # 4. Gerekirse sessizce Temp klasörüne indir
    try:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        import urllib.request
        urllib.request.urlretrieve(url, str(temp_cf))
        if temp_cf.exists():
            return temp_cf
    except Exception:
        pass

    return base_cf

def watch_cloudflared_log(proc, log_file: Path):
    """cloudflared logunu dinleyerek trycloudflare.com URL'ini yakalar ve Uvicorn'a iletir."""
    pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    found_url = None
    
    for _ in range(50):
        time.sleep(0.5)
        if not log_file.exists():
            continue
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                matches = pattern.findall(content)
                if matches:
                    found_url = matches[-1]
                    break
        except Exception:
            pass

    if found_url:
        # Tünel URL'sini hem Temp hem BASE_DIR dosyalarına güvenle yaz
        try:
            TUNNEL_TEMP_FILE.write_text(found_url, encoding="utf-8")
        except Exception:
            pass
        try:
            (BASE_DIR / "tunnel_url.txt").write_text(found_url, encoding="utf-8")
        except Exception:
            pass

        # FastAPI uvicorn ayağa kalkana kadar döngüde bildirmeyi dene (en fazla 30 saniye)
        import urllib.request
        import json
        payload = json.dumps({"url": found_url}).encode("utf-8")

        for _ in range(30):
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:8000/api/tunnel-url",
                    data=payload,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                time.sleep(1.0)

def main():
    # 0. Eski kalıntıları temizle
    cleanup_zombies_and_port()
    if CF_LOG_FILE.exists():
        try:
            CF_LOG_FILE.unlink()
        except Exception:
            pass
    if TUNNEL_TEMP_FILE.exists():
        try:
            TUNNEL_TEMP_FILE.unlink()
        except Exception:
            pass
    base_tunnel = BASE_DIR / "tunnel_url.txt"
    if base_tunnel.exists():
        try:
            base_tunnel.unlink()
        except Exception:
            pass

    # 1. Cloudflare Tünel Sürecini Başlat
    cf_exe = get_cloudflared_executable()
    cf_proc = None
    if cf_exe.exists():
        cf_cmd = [str(cf_exe), "tunnel", "--url", "http://127.0.0.1:8000", "--logfile", str(CF_LOG_FILE)]
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            cf_proc = subprocess.Popen(
                cf_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )
            watcher_thread = threading.Thread(target=watch_cloudflared_log, args=(cf_proc, CF_LOG_FILE), daemon=True)
            watcher_thread.start()
        except Exception:
            pass

    # 2. Tarayıcıda Kiosk Ekranını Otomatik Aç (1.2 saniye sonra)
    def open_browser():
        time.sleep(1.2)
        webbrowser.open("http://localhost:8000/kiosk")

    threading.Thread(target=open_browser, daemon=True).start()

    # 3. Uvicorn Sunucusunu Başlat
    try:
        import uvicorn
        from app import app
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    except KeyboardInterrupt:
        pass
    finally:
        if cf_proc:
            try:
                cf_proc.terminate()
            except Exception:
                pass
        cleanup_zombies_and_port()

if __name__ == "__main__":
    main()


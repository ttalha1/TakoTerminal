import os
import sys
import time
import re
import socket
import shutil
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

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def ensure_cloudflared(cloudflared_exe: Path):
    """cloudflared.exe kontrol edilir; paket içine gömülmüşse anında çıkarılır, yoksa sessizce indirilir."""
    if cloudflared_exe.exists():
        return
    # 1. PyInstaller _MEIPASS içine gömüldüyse oradan anında kopyala
    if hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "cloudflared.exe"
        if bundled.exists():
            try:
                shutil.copy2(bundled, cloudflared_exe)
                return
            except Exception:
                pass
    # 2. Sessiz indirme yedeği
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(cloudflared_exe))
    except Exception:
        pass

def watch_cloudflared_log(proc, log_file: Path):
    """cloudflared.log dosyasını dinleyerek trycloudflare.com URL'ini yakalar."""
    pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    found_url = None
    
    for _ in range(35):
        time.sleep(1)
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
        try:
            (BASE_DIR / "tunnel_url.txt").write_text(found_url, encoding="utf-8")
            import urllib.request
            import json
            req = urllib.request.Request(
                "http://127.0.0.1:8000/api/tunnel-url",
                data=json.dumps({"url": found_url}).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass

def main():
    local_ip = get_local_ip()

    cloudflared_exe = BASE_DIR / "cloudflared.exe"
    ensure_cloudflared(cloudflared_exe)

    log_file = BASE_DIR / "cloudflared.log"
    if log_file.exists():
        try:
            log_file.unlink()
        except Exception:
            pass

    # 1. Cloudflare Tünel Sürecini Başlat (Penceresiz, sessiz)
    cf_proc = None
    if cloudflared_exe.exists():
        cf_cmd = [str(cloudflared_exe), "tunnel", "--url", "http://127.0.0.1:8000", "--logfile", str(log_file)]
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        cf_proc = subprocess.Popen(
            cf_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags
        )
        watcher_thread = threading.Thread(target=watch_cloudflared_log, args=(cf_proc, log_file), daemon=True)
        watcher_thread.start()

    # 2. Tarayıcıda Kiosk Ekranını Otomatik Aç (1 saniye sonra)
    def open_browser():
        time.sleep(1.0)
        webbrowser.open("http://localhost:8000/kiosk")

    threading.Thread(target=open_browser, daemon=True).start()


    # 3. Uvicorn Sunucusunu Başlat
    try:
        import uvicorn
        from app import app
        uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
    except KeyboardInterrupt:
        print("\n[BİLGİ] TakoTerminal kapatılıyor...")
    finally:
        if cf_proc:
            try:
                cf_proc.terminate()
            except Exception:
                pass
        print("[TAMAMLANDI] TakoTerminal güvenle durduruldu.")

if __name__ == "__main__":
    main()

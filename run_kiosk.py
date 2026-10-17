import os
import sys
import time
import re
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path

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
    """cloudflared.exe yoksa GitHub üzerinden otomatik indirir."""
    if not cloudflared_exe.exists():
        print("[TAKOTERMINAL] 4G/5G tünel motoru (cloudflared.exe) bulunamadı.")
        print("[TAKOTERMINAL] Resmi kaynaktan otomatik indiriliyor, lütfen bekleyiniz...")
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        try:
            import urllib.request
            urllib.request.urlretrieve(url, str(cloudflared_exe))
            print("[TAKOTERMINAL] cloudflared.exe başarıyla indirildi ve hazırlandı.")
        except Exception as e:
            print(f"[UYARI] cloudflared otomatik indirilemedi ({e}). Sistem yerel ağ üzerinden çalışacak.")

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
        print(f"\n[CLOUDFLARE 4G/5G TÜNEL HAZIR] 👉 Mobil İnternet Linki: {found_url}/mobile\n")
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
        except Exception as e:
            pass
    else:
        print("[BİLGİ] Dış internet tüneli bekleniyor. Yerel Wi-Fi modu devrede.")

def main():
    local_ip = get_local_ip()
    print("\n" + "="*70)
    print("           🖨️  TAKOTERMINAL - AKILLI BASKI KİOSK SİSTEMİ")
    print("="*70)
    print(f"🏠 Yerel Wi-Fi IP Adresi:     http://{local_ip}:8000/mobile")
    print("🖥️  Ana Kiosk Ekranı:          http://localhost:8000/kiosk")
    print("🌐 Cloudflare 4G/5G Tüneli:   Açılıyor (Ücretsiz & Sıfır Maliyet)...")
    print("="*70 + "\n")

    cloudflared_exe = BASE_DIR / "cloudflared.exe"
    ensure_cloudflared(cloudflared_exe)

    log_file = BASE_DIR / "cloudflared.log"
    if log_file.exists():
        try:
            log_file.unlink()
        except Exception:
            pass

    # 1. Cloudflare Tünel Sürecini Başlat
    cf_proc = None
    if cloudflared_exe.exists():
        cf_cmd = [str(cloudflared_exe), "tunnel", "--url", "http://127.0.0.1:8000", "--logfile", str(log_file)]
        cf_proc = subprocess.Popen(cf_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        watcher_thread = threading.Thread(target=watch_cloudflared_log, args=(cf_proc, log_file), daemon=True)
        watcher_thread.start()

    # 2. Tarayıcıda Kiosk Ekranını Otomatik Aç (1.5 saniye sonra)
    def open_browser():
        time.sleep(1.5)
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

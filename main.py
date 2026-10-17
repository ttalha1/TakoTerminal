import os
import sys

# Windows konsolunda UTF-8 desteği
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from crewai import Agent, Crew, Process, Task
from dotenv import load_dotenv

load_dotenv()
os.environ["GEMINI_API_KEY"] = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("MODEL", "gemini/gemini-3.5-flash")

# 1. Ajanları Tanımlama
architect = Agent(
    role="Kıdemli Sistem Mimarı ve Siber Güvenlik Uzmanı",
    goal="Kırtasiye QR Baskı ve Dosya Aktarım Kiosk Sisteminin uçtan uca mimarisini, veri modellerini, güvenlik ve KVKK protokollerini tasarlamak.",
    backstory="Büyük ölçekli web kiosk sistemleri, gerçek zamanlı WebSocket altyapıları ve dosya güvenliği (anti-malware, veri imhası) konusunda 15 yıllık deneyime sahip bir yazılım mimarısın.",
    verbose=True,
    llm=MODEL
)

developer = Agent(
    role="Kıdemli Full-Stack Python ve Web Geliştiricisi",
    goal="Kırtasiye QR Baskı Kiosk uygulamasının tüm backend (FastAPI), mobil müşteri web arayüzü, PC kiosk paneli, format dönüştürme ve Cloudflare tünel entegrasyonu kodlarını eksiksiz üretmek.",
    backstory="FastAPI, WebSockets, modern mobil duyarlı HTML5/CSS/JavaScript ve dosya işleme motorları (PDF, Word, Resim) konusunda uzman, üretime hazır ve hatasız kodlar yazan usta bir yazılımcısın.",
    verbose=True,
    llm=MODEL
)

# 2. Görevleri Tanımlama
task1 = Task(
    description="""
    Kırtasiye QR Baskı Kiosk Sisteminin teknik mimarisini ve gereksinimlerini detaylandır:
    1. İki Taraflı Sistem Mimarisi:
       - Müşteri Tarafı (Mobil Web): Müşteri Adı Soyadı girişi, Güvenli dosya yükleme, Format dönüştürücü (Çoklu resim -> Tek PDF, Word -> PDF), Baskı tercihleri (Renkli/SB, Tek/Çift yüz, Kopya sayısı), Müşteri notu, Anlık sayfa ve fiyat hesaplama, 4 haneli PIN ve canlı durum takibi.
       - Kırtasiyeci Tarafı (PC Kiosk Paneli): Dinamik QR Kod (Mobil veriyle uyumlu, Cloudflare Tunnel), Gerçek zamanlı WebSocket sipariş kuyruğu, Sesli zil uyarısı (Ding!), Müşteri adı, PIN, dosya önizleme, tek tıkla yazdırma ve 15 dk sonra otomatik dosya imhası (KVKK/güvenlik).
    2. Güvenlik Protokolleri: İzin verilen dosya uzantıları, dosya boyutu sınırı, otomatik disk temizleme.
    3. API ve WebSocket Rota Mimarisi.
    """,
    expected_output="Uçtan uca sistem mimarisi, veri akış şeması, API ve WebSocket endpoint tanımları.",
    agent=architect,
)

task2 = Task(
    description="""
    Mimarinin raporuna dayanarak sistemi çalıştırılabilir, eksiksiz bir Python FastAPI projesi olarak kodla.
    Uygulama tek bir ana dosya (app.py) veya modüler yapıda olmalı ve şunları içermelidir:
    1. FastAPI Backend + WebSocket: Siparişlerin kırtasiye paneline anlık canlı düşmesi için WebSocket bağlantısı.
    2. Müşteri Mobil Web Arayüzü (HTML/CSS/JS): Telefon ekranında modern, şık bir arayüz. İsim, dosya seçimi, baskı tercihleri (Renkli/SB, Çift Yüz, Kopya), fiyat hesaplama, sipariş notu ve gönderim sonrası 4 haneli PIN ekranı.
    3. Kırtasiyeci Kiosk Web Paneli: Masaüstü ekranında tam ekran çalışabilen, ortasında büyük QR kod olan, gelen siparişleri canlı gösteren, zil çalan ve yazdır/sil butonları olan profesyonel kontrol paneli.
    4. Format Dönüştürme Motoru: Çoklu fotoğrafları tek PDF yapma (Pillow) ve belge işleme mantığı.
    5. Güvenlik: 15 dakika sonra dosyaları diskten otomatik temizleyen arka plan görevi (background cleanup task).
    6. Kodları eksiksiz, importları tam ve çalıştırılabilir şekilde sun.
    """,
    expected_output="Çalıştırılabilir, eksiksiz ve üretime hazır Python ve Web kodları.",
    agent=developer,
)

# 3. Ekip Oluşturma
kiosk_crew = Crew(
    agents=[architect, developer],
    tasks=[task1, task2],
    process=Process.sequential,
    verbose=True,
)

print("### Kırtasiye QR Baskı Kiosk Sistemi İçin Otonom Ekip Çalışmaya Başlıyor... ###")
result = kiosk_crew.kickoff()
print("\n\n### Ekip Görevini Tamamladı! Sonuç: ###\n")
print(result)

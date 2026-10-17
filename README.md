# 🖨️ TakoTerminal

**Temassız, Akıllı QR Baskı ve Fotokopi Kiosk Sistemi**  
*TakoTerminal, kırtasiye ve fotokopi merkezlerinde müşteri ile bilgisayar arasındaki kablo, WhatsApp ve flash bellek karmaşasını tamamen ortadan kaldıran; sıfır maliyetli, güvenli ve temassız bir baskı kiosk platformudur.*

---

## 🌟 Neden TakoTerminal?

Geleneksel kırtasiyelerde müşterilerin belge yazdırması için WhatsApp'tan dosya göndermesi, e-posta atması veya USB bellek takması gerekir. Bu durum hem virüs bulaşma riski oluşturur hem de KVKK / kişisel gizlilik açısından ciddi güvenlik açıkları barındırır.

**TakoTerminal ile:**
1. Müşteri ekrandaki **QR kodu** telefonunun kamerasıyla okutur.
2. Açılan şık mobil web arayüzünden belgelerini seçer, her belge için bağımsız ayarlarını yapar (Siyah-Beyaz / Renkli, Tek Yön / Çift Yön, Kopya sayısı, Özel not).
3. Ekranda otomatik üretilen **4 haneli PIN kodunu** kırtasiyeciye söyler.
4. Kırtasiyecinin ekranına **"Ding-Dong!"** zil sesiyle sipariş düşer, tek tıkla yazdırılır.
5. Sipariş tamamlandığında veya 15 dakika dolduğunda tüm dosyalar diskten **kalıcı olarak güvenle imha edilir.**

---

## 🚀 Öne Çıkan Özellikler

- **📱 Sıfır Kurulum Mobil Arayüz:** Müşterinin herhangi bir uygulama indirmesine veya Wi-Fi şifresi girmesine gerek yoktur. 4G/5G mobil internetiyle saniyeler içinde bağlanır.
- **📑 Çoklu Belge & Bağımsız Ayarlar:** Tek bir siparişte birden fazla dosya yüklenebilir. Her dosyanın rengi, yönü, kopya sayısı ve kırtasiyeciye özel notu bağımsız ayarlanabilir.
- **🔄 Otomatik Format Dönüştürme:**
  - Word (`.docx`, `.doc`) belgelerini orijinal biçimlendirmesini koruyarak **yazdırmaya hazır PDF'e dönüştürür**.
  - Görselleri (`.jpg`, `.png`, `.webp`, `.bmp`) PDF formatına çevirir.
- **📄 Akıllı Sayfa & Yaprak Hesabı:**
  - Word ve PDF dosyalarının sayfa sayılarını otomatik hesaplar.
  - **Tek Yön vs Çift Yön:** Örneğin 10 sayfalık bir belge çift yön seçildiğinde otomatik olarak `5 Yaprak Kağıt` olarak hesaplanır ve kağıt tasarrufu indirimi yansıtılır.
- **⚙️ Kırtasiyeci Fiyat Kontrol Paneli:** Kiosk ekranından Siyah-Beyaz ve Renkli (Tek/Çift yön) sayfa başı fiyatlar kolayca değiştirilebilir. Fiyatlar `config.json` dosyasına kaydedilir ve müşteri telefonlarına **anında canlı olarak yansır**.
- **🔔 Açılıp Kapanabilir Zil Sesi:** Yeni sipariş geldiğinde Web Audio API ile net bir iki tonlu zil ("Ding-Dong!") çalar. Tek tıkla sessize alınabilir.
- **🛡️ KVKK 15 Dakika Otomatik İmha:** Gizlilik gereği, sipariş dosyaları 15 dakika sonra rastgele baytlarla ezilerek (`secure_shred`) diskten kalıcı olarak silinir.
- **🌐 Sıfır Maliyetli 4G/5G Tüneli:** Dahili Cloudflare Quick Tunnel entegrasyonu sayesinde hiçbir alan adı, sabit IP veya sunucu kiralamaya gerek kalmadan ücretsiz HTTPS tüneli açılır.
- **🔒 Tam Bilgisayar İzolasyonu:** Her bilgisayar kendi bağımsız tünelini ve QR kodunu üretir. Farklı bilgisayarlar veya şubeler birbirlerinin verilerini asla göremez.

---

## 🛠️ Mimari ve Teknolojiler

- **Backend:** Python 3.10+, [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/)
- **Gerçek Zamanlı İletişim:** WebSockets (Çift yönlü canlı sipariş, durum ve tarife akışı)
- **Frontend:** Responsive HTML5, Modern CSS ([TailwindCSS](https://tailwindcss.com/)), Vanilla JavaScript
- **Dosya İşleme:** `pypdf`, `Pillow (PIL)`, `docx2pdf`, `python-docx`
- **Dış Ağ Tünelleme:** [Cloudflare Quick Tunnel (`cloudflared`)](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- **Ses Motoru:** HTML5 Web Audio API Synthesizer (Harici MP3 gerektirmez)

---

## 📦 Kurulum ve Çalıştırma

### Gereksinimler
- Windows 10 veya Windows 11
- Python 3.10 veya üzeri (Kurarken *"Add Python to PATH"* seçeneğini işaretleyiniz)
- *(İsteğe bağlı)* Word belgelerinin kusursuz PDF dönüşümü için Microsoft Word

### Adım Adım Kurulum

1. **Projeyi Klonlayın:**
   ```bash
   git clone https://github.com/kullanici-adiniz/TakoTerminal.git
   cd TakoTerminal
   ```

2. **Python Sanal Ortamını (venv) Oluşturun ve Aktive Edin:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Gerekli Paketleri Yükleyin:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Sistemi Başlatın:**
   ```bash
   python run_kiosk.py
   ```
   *Veya doğrudan **`TakoTerminal_Baslat.bat`** dosyasına çift tıklayabilirsiniz!*

Sistem başlatıldığında:
- 4G/5G tünel motoru (`cloudflared.exe`) yoksa ilk çalıştırmada otomatik olarak indirilir.
- Sunucu açılır ve varsayılan tarayıcınızda **Ana PC Kiosk Ekranı** (`http://localhost:8000/kiosk`) otomatik olarak başlar.
- Ekranda oluşan QR kodu telefonunuzla okutarak test baskınızı hemen gönderebilirsiniz!

---

## 📁 Proje Dizin Yapısı

```text
TakoTerminal/
├── app.py                     # Ana FastAPI sunucusu, API endpoint'leri ve Web UI'ları
├── run_kiosk.py               # Cloudflare tünel süpervizörü ve otomatik başlatıcı
├── TakoTerminal_Baslat.bat    # Tek tıkla Windows başlatıcı kısayolu
├── config.example.json        # Örnek fiyatlandırma şablonu
├── requirements.txt           # Python bağımlılıkları listesi
├── .gitignore                 # Gizli anahtarlar ve geçici dosyalar için git yoksayma
└── README.md                  # Proje dokümantasyonu
```

---

## 📄 Lisans

Bu proje **MIT Lisansı** ile lisanslanmıştır. Detaylar için `LICENSE` dosyasına göz atabilirsiniz.

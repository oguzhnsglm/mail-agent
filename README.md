# Mail Agent - Otomatik Haber Bülteni Sistemi

Herhangi bir konuda internetten en güncel haberleri otomatik olarak tarayan, yapay zeka ile işleyip özetleyen ve e-posta ile gönderen bir sistemdir.

Konu olarak ne yazarsanız (savunma sanayi, kripto, yapay zeka, bir şirket adı vb.) o konuda Google ve DuckDuckGo üzerinden son 24 saatin haberlerini bulur, haber sitelerine girip içeriğini çeker, LLM ile düzenler ve mail atar.



## Nasil Calisir?

```
 Kullanıcı konu girer (ör: "FNSS", "yapay zeka")
          │
          ▼
 ┌─────────────────────────────────┐
 │  1. ARAMA (Google / DuckDuckGo) │  ← Headless browser ile arama motorlarını tarar
 │     Son 24 saatin haberlerini   │
 │     bulur, URL'leri toplar      │
 └────────────┬────────────────────┘
              │
              ▼
 ┌─────────────────────────────────┐
 │  2. DERİN TARAMA (Crawl4AI)    │  ← Her URL'ye girer
 │     • Kategori sayfası mı?     │
 │       → Tüm haber linklerine   │
 │         tek tek dalıp içerik,  │
 │         başlık ve tarih çeker  │
 │     • Tekil haber mi?          │
 │       → Doğrudan içeriği alır  │
 └────────────┬────────────────────┘
              │
              ▼
 ┌─────────────────────────────────┐
 │  3. YAPAY ZEKA İŞLEME          │  ← OpenRouter API (GPT-4 Turbo vb.)
 │     Ham metin → Temiz Türkçe   │
 │     haber formatına dönüştürme │
 │     (Halüsinasyon korumalı)    │
 └────────────┬────────────────────┘
              │
              ▼
 ┌─────────────────────────────────┐
 │  4. E-POSTA GÖNDERİM           │  ← Gmail SMTP
 │     HTML formatlı, responsive  │
 │     haber bülteni maili        │
 └─────────────────────────────────┘
```

## Proje Yapisi

```
newsletter-agent/
├── api/
│   └── fastapi_server.py      # Web arayüzü ve REST API
├── agents/
│   ├── llm_client.py          # OpenRouter API istemcisi
│   └── newsletter_agents.py   # LangGraph tabanlı haber işleme
├── crawlers/
│   └── web_crawler.py         # Crawl4AI ile web tarama motoru
├── email_service/
│   └── gmail_client.py        # Gmail SMTP ile mail gönderimi
├── scheduler/
│   └── newsletter_scheduler.py # Zamanlama ve orkestrasyon
├── utils/
│   └── logger.py              # Loglama
├── config.py                  # Ortam değişkenleri yönetimi
├── main.py                    # CLI giriş noktası
└── requirements.txt
```

## Kurulum

### Gereksinimler

- Python 3.11+
- OpenRouter API anahtarı
- Gmail hesabı + App Password

### Adimlar

**1. Repoyu klonlayın**

```bash
git clone https://github.com/oguzhnsglm/mail-agent.git
cd mail-agent
```

**2. Bağımlılıkları yükleyin**

```bash
pip install -r requirements.txt
```

**3. Ortam değişkenlerini ayarlayın**

```bash
cp .env.example .env
```

`.env` dosyasını açıp aşağıdaki değerleri doldurun:

```env
# OpenRouter (LLM için)
OPENROUTER_API_KEY=your_key_here

# Gmail (mail göndermek için)
SENDER_EMAIL=your_email@gmail.com
GMAIL_APP_PASSWORD=your_app_password

# Alıcı
RECIPIENT_EMAILS=alici1@gmail.com,alici2@gmail.com

# Konular (virgülle ayırın)
TOPICS=FNSS,yapay zeka,Türkiye ekonomi

# Zamanlama
SCHEDULE_TIMES=09:00
TIMEZONE=Europe/Istanbul
```

### Gmail App Password Nasil Alinir?

1. [Google Hesap Ayarları](https://myaccount.google.com/security) > Güvenlik
2. 2 Adımlı Doğrulama aktif olmalı
3. "Uygulama Şifreleri" bölümünden yeni bir şifre oluşturun
4. Oluşturulan 16 haneli şifreyi `GMAIL_APP_PASSWORD` olarak `.env`'ye yazın

## Calistirma

### Web Arayüzü (Tavsiye Edilen)

```bash
python -m uvicorn api.fastapi_server:app --host 127.0.0.1 --port 8000
```

Tarayıcıdan `http://127.0.0.1:8000` adresine girin. Arayüzden:

- **Hemen Gönder**: E-posta ve konu girip anında bülten oluşturup gönderin
- **Job Ekle**: Belirli saatlerde otomatik gönderim için zamanlama yapın
- **Job Listesi**: Mevcut zamanlamaları görüntüleyin ve yönetin

### Komut Satiri (CLI)

```bash
# Tek seferlik çalıştırma
python main.py --mode once

# Zamanlayıcı ile sürekli çalıştırma
python main.py --mode schedule

# Konfigürasyon kontrolü
python main.py --config-check
```

## API Endpointleri

| Endpoint | Metod | Aciklama |
|----------|-------|----------|
| `/` | GET | Web arayüzü (dashboard) |
| `/send-now` | POST | Anında bülten oluştur ve gönder |
| `/jobs/add` | POST | Zamanlanmış job ekle |
| `/jobs/{id}/delete` | POST | Job sil |
| `/api/generate` | POST | API ile asenkron bülten oluştur |
| `/api/generate-sync` | POST | API ile senkron bülten oluştur |
| `/health` | GET | Sağlık kontrolü |

## Teknik Detaylar

### Web Tarama Stratejisi

- **Google Search**: Türkçe, son 24 saat filtreleriyle haber araması
- **DuckDuckGo**: Google yeterli sonuç vermezse yedek kaynak
- **Derinlemesine Tarama**: Kategori sayfaları tespit edilirse (ör: `fnss.com.tr/haberler`), sayfadaki tüm haber linklerine tek tek girip tam içerik, başlık ve yayın tarihi çekilir
- **Bot Koruması**: Crawl4AI'ın `magic=True` modu ile bypass

### Yapay Zeka Isleme

- **OpenRouter API** üzerinden herhangi bir LLM modeli kullanılabilir (varsayılan: GPT-4 Turbo)
- **Düşük sıcaklık** (0.1) ile halüsinasyon minimuma indirilir
- LLM sadece formatlama yapar, yeni bilgi üretmez

### Tarih ve Baslik Cikarimi

Haber sayfalarından gerçek yayın tarihi 4 farklı stratejiyle çıkarılır:
1. `<meta property="article:published_time">`
2. `<time datetime="...">` etiketi
3. JSON-LD yapısal veri (`datePublished`)
4. CSS class'ında `date`/`tarih` geçen elementler

Başlıklar da benzer şekilde `<h1>` → `og:title` → `<title>` sırasıyla alınır.

## Kullanilan Teknolojiler

- **[Crawl4AI](https://github.com/unclecode/crawl4ai)** - Headless browser ile web tarama
- **[LangGraph](https://github.com/langchain-ai/langgraph)** - Agent iş akışı yönetimi
- **[FastAPI](https://fastapi.tiangolo.com/)** - Web arayüzü ve API
- **[OpenRouter](https://openrouter.ai/)** - LLM API erişimi
- **[BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)** - HTML ayrıştırma
- **Gmail SMTP** - E-posta gönderimi

## Lisans

MIT

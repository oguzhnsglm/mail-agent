"""
Ortak Tarih Çıkarma ve Doğrulama Modülü
Tüm crawler'lar tarafından kullanılır.
Sosyal medya ve web haberlerinden doğru tarih çıkarımı sağlar.
"""

import re
from datetime import datetime, timedelta
from typing import Optional

import dateutil.parser
from bs4 import BeautifulSoup

from utils.logger import setup_logger

logger = setup_logger(__name__)

# Geçerli kabul edilen maksimum yaş (gün)
MAX_AGE_DAYS = 365
# Gelecek tarih toleransı (saat) — timezone farklarından dolayı küçük sapma olabilir
FUTURE_TOLERANCE_HOURS = 48


def validate_date(dt: datetime) -> bool:
    """Çıkarılan tarihin mantıklı olup olmadığını kontrol eder.
    - Gelecekte 48 saatten fazla olamaz
    - 1 yıldan eski olamaz
    """
    now = datetime.now()
    if dt > now + timedelta(hours=FUTURE_TOLERANCE_HOURS):
        return False
    if dt < now - timedelta(days=MAX_AGE_DAYS):
        return False
    return True


def format_date(dt: datetime) -> str:
    """Datetime nesnesini standart YYYY-MM-DD formatına çevirir."""
    return dt.strftime("%Y-%m-%d")


def safe_parse_date(text: str) -> Optional[datetime]:
    """Verilen metni güvenli şekilde tarihe çevirmeye çalışır.
    Geçersiz veya mantıksız tarihler için None döner.
    """
    if not text or len(text.strip()) < 4:
        return None
    try:
        dt = dateutil.parser.parse(text.strip(), fuzzy=True)
        if dt.tzinfo:
            dt = dt.replace(tzinfo=None)
        if validate_date(dt):
            return dt
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
#  HTML Tabanlı Tarih Çıkarma
# ---------------------------------------------------------------------------

def extract_date_from_meta_tags(soup: BeautifulSoup) -> Optional[str]:
    """HTML meta etiketlerinden tarih çıkarır (article:published_time, og:updated_time vb.)"""
    meta_properties = [
        'article:published_time',
        'og:article:published_time',
        'datePublished',
        'og:updated_time',
        'article:modified_time',
        'date',
        'pubdate',
        'publish_date',
        'sailthru.date',
    ]
    for prop in meta_properties:
        meta = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
        if meta and meta.get('content'):
            dt = safe_parse_date(meta['content'])
            if dt:
                return format_date(dt)
    return None


def extract_date_from_time_tags(soup: BeautifulSoup) -> Optional[str]:
    """HTML <time> etiketlerinden tarih çıkarır."""
    # datetime attribute'u olan time etiketleri
    for time_tag in soup.find_all('time', datetime=True):
        dt = safe_parse_date(time_tag['datetime'])
        if dt:
            return format_date(dt)

    # datetime attribute'u olmayan ama metin içeriği olan time etiketleri
    for time_tag in soup.find_all('time'):
        text = time_tag.get_text(strip=True)
        if text and len(text) < 60:
            dt = safe_parse_date(text)
            if dt:
                return format_date(dt)
    return None


def extract_date_from_json_ld(soup: BeautifulSoup) -> Optional[str]:
    """JSON-LD yapısal verilerinden tarih çıkarır."""
    import json
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            ld = json.loads(script.string)
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ['datePublished', 'dateCreated', 'dateModified', 'uploadDate']:
                    if key in item:
                        dt = safe_parse_date(str(item[key]))
                        if dt:
                            return format_date(dt)
                # @graph içinde de olabilir
                if '@graph' in item and isinstance(item['@graph'], list):
                    for graph_item in item['@graph']:
                        if isinstance(graph_item, dict):
                            for key in ['datePublished', 'dateCreated', 'dateModified']:
                                if key in graph_item:
                                    dt = safe_parse_date(str(graph_item[key]))
                                    if dt:
                                        return format_date(dt)
        except Exception:
            pass
    return None


def extract_date_from_css_classes(soup: BeautifulSoup) -> Optional[str]:
    """CSS class adında tarih ifadesi geçen elementlerden tarih çıkarır."""
    date_selectors = [
        {'class_': re.compile(r'date|tarih|time|publish|yayın|zamani', re.I)},
        {'class_': re.compile(r'article.*(date|meta)', re.I)},
        {'class_': re.compile(r'(post|entry).*(date|time|meta)', re.I)},
    ]
    for selector in date_selectors:
        for el in soup.find_all(['span', 'div', 'p', 'time', 'small', 'li'], **selector):
            text = el.get_text(strip=True)
            if text and len(text) < 60:
                dt = safe_parse_date(text)
                if dt:
                    return format_date(dt)
    return None


def extract_date_from_html(html: str) -> str:
    """HTML içeriğinden en güvenilir tarih çıkarma. Tüm stratejileri sırayla dener.
    Bulamazsa boş string döner.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, 'html.parser')

    # Strateji 1: Meta etiketleri (en güvenilir)
    result = extract_date_from_meta_tags(soup)
    if result:
        return result

    # Strateji 2: <time> etiketleri
    result = extract_date_from_time_tags(soup)
    if result:
        return result

    # Strateji 3: JSON-LD yapısal veri
    result = extract_date_from_json_ld(soup)
    if result:
        return result

    # Strateji 4: CSS class tabanlı
    result = extract_date_from_css_classes(soup)
    if result:
        return result

    return ""


# ---------------------------------------------------------------------------
#  Sosyal Medya Platform-Spesifik Tarih Çıkarma
# ---------------------------------------------------------------------------

def extract_date_from_linkedin_id(url: str) -> Optional[str]:
    """LinkedIn activity ID'sinden tarih çıkarır (Snowflake benzeri bit-shift)."""
    activity_match = re.search(r'activity[- :](\d{15,22})', url)
    if not activity_match:
        # URL'de urn:li:activity: formatı da olabilir
        activity_match = re.search(r'urn:li:activity:(\d{15,22})', url)
    if activity_match:
        try:
            activity_id = int(activity_match.group(1))
            linkedin_epoch_ms = 1288834974657
            timestamp_ms = (activity_id >> 22) + linkedin_epoch_ms
            dt = datetime.fromtimestamp(timestamp_ms / 1000)
            if validate_date(dt):
                return format_date(dt)
        except Exception:
            pass
    return None


def extract_date_from_twitter_id(url: str) -> Optional[str]:
    """Twitter/X status ID'sinden tarih çıkarır (Twitter Snowflake)."""
    status_match = re.search(r'/status/(\d{15,22})', url)
    if status_match:
        try:
            status_id = int(status_match.group(1))
            twitter_epoch_ms = 1288834974657
            timestamp_ms = (status_id >> 22) + twitter_epoch_ms
            dt = datetime.fromtimestamp(timestamp_ms / 1000)
            if validate_date(dt):
                return format_date(dt)
        except Exception:
            pass
    return None


def extract_date_from_relative_text(html: str) -> Optional[str]:
    """Sayfadaki 'X gün/hafta/saat önce' gibi görece ifadelerden tarih hesaplar."""
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, 'html.parser')
        page_text = soup.get_text()

        # Türkçe ve İngilizce görece ifadeler
        rel_match = re.search(
            r'(\d+)\s*'
            r'(saniye|second|sn|sec|'
            r'dakika|minute|dk|min|'
            r'saat|hour|sa|hr|h|'
            r'gün|gun|day|'
            r'hafta|week|'
            r'ay|month)s?'
            r'\s*(önce|once|ago)',
            page_text, re.I
        )
        if rel_match:
            num = int(rel_match.group(1))
            unit = rel_match.group(2).lower()

            if unit in ('saniye', 'second', 'sn', 'sec'):
                dt = datetime.now() - timedelta(seconds=num)
            elif unit in ('dakika', 'minute', 'dk', 'min'):
                dt = datetime.now() - timedelta(minutes=num)
            elif unit in ('saat', 'hour', 'sa', 'hr', 'h'):
                dt = datetime.now() - timedelta(hours=num)
            elif unit in ('gün', 'gun', 'day'):
                dt = datetime.now() - timedelta(days=num)
            elif unit in ('hafta', 'week'):
                dt = datetime.now() - timedelta(weeks=num)
            elif unit in ('ay', 'month'):
                dt = datetime.now() - timedelta(days=num * 30)
            else:
                return None

            if validate_date(dt):
                return format_date(dt)
    except Exception:
        pass
    return None


def extract_date_from_google_snippet(snippet_html: str) -> Optional[str]:
    """Google arama sonucu snippet'inden tarih çıkarır.
    Google genellikle tarihi snippet'in başında gösterir: '3 Mar 2025 — ...' veya '2 gün önce — ...'
    """
    if not snippet_html:
        return None

    text = snippet_html if isinstance(snippet_html, str) else ""
    # HTML tag temizle (basit)
    text = re.sub(r'<[^>]+>', ' ', text).strip()

    # Pattern 1: "3 Mar 2025" veya "15 Şub 2025" gibi tarihler
    date_match = re.search(
        r'(\d{1,2})\s+'
        r'(Oca|Şub|Mar|Nis|May|Haz|Tem|Ağu|Eyl|Eki|Kas|Ara|'
        r'Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-zıüöçşğ]*'
        r'\s+(\d{4})',
        text, re.I
    )
    if date_match:
        dt = safe_parse_date(date_match.group(0))
        if dt:
            return format_date(dt)

    # Pattern 2: "2025-03-15" ISO format
    iso_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
    if iso_match:
        dt = safe_parse_date(iso_match.group(1))
        if dt:
            return format_date(dt)

    # Pattern 3: Görece tarih ("2 gün önce", "5 hours ago")
    rel_match = re.search(
        r'(\d+)\s*'
        r'(saniye|second|sn|sec|dakika|minute|dk|min|saat|hour|sa|hr|h|gün|gun|day|hafta|week|ay|month)s?'
        r'\s*(önce|once|ago)',
        text, re.I
    )
    if rel_match:
        num = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        if unit in ('saniye', 'second', 'sn', 'sec'):
            dt = datetime.now() - timedelta(seconds=num)
        elif unit in ('dakika', 'minute', 'dk', 'min'):
            dt = datetime.now() - timedelta(minutes=num)
        elif unit in ('saat', 'hour', 'sa', 'hr', 'h'):
            dt = datetime.now() - timedelta(hours=num)
        elif unit in ('gün', 'gun', 'day'):
            dt = datetime.now() - timedelta(days=num)
        elif unit in ('hafta', 'week'):
            dt = datetime.now() - timedelta(weeks=num)
        elif unit in ('ay', 'month'):
            dt = datetime.now() - timedelta(days=num * 30)
        else:
            return None
        if validate_date(dt):
            return format_date(dt)

    return None


def extract_social_date(html: str, url: str) -> str:
    """Sosyal medya paylaşımının tarihini çıkarır. Tüm stratejileri dener.
    Sıralama: Platform ID > HTML meta > Time tag > JSON-LD > Relative text > Google snippet
    Bulamazsa boş string döner (bugünün tarihini YAZMAZ — çağıran taraf karar verir).
    """
    # 1) LinkedIn Snowflake ID
    if 'linkedin.com' in url:
        result = extract_date_from_linkedin_id(url)
        if result:
            logger.debug(f"[DateUtils] LinkedIn ID'den tarih: {result} ({url[:60]})")
            return result

    # 2) Twitter/X Snowflake ID
    if 'twitter.com' in url or 'x.com' in url:
        result = extract_date_from_twitter_id(url)
        if result:
            logger.debug(f"[DateUtils] Twitter ID'den tarih: {result} ({url[:60]})")
            return result

    # 3) HTML meta etiketleri ve yapısal veri
    if html:
        result = extract_date_from_html(html)
        if result:
            logger.debug(f"[DateUtils] HTML'den tarih: {result} ({url[:60]})")
            return result

    # 4) Görece zaman ifadeleri ("X gün önce")
    if html:
        result = extract_date_from_relative_text(html)
        if result:
            logger.debug(f"[DateUtils] Görece ifadeden tarih: {result} ({url[:60]})")
            return result

    return ""


def extract_date_from_search_result(a_tag, parent_element=None) -> str:
    """Arama motoru sonuç sayfasındaki bir link elemanından tarih çıkarır.
    Google, DuckDuckGo, Bing snippet'lerindeki tarih bilgisini yakalar.
    """
    # DuckDuckGo result__date class'ı
    if parent_element:
        date_el = parent_element.select_one('.result__date')
        if date_el:
            text = date_el.get_text(strip=True)
            dt = safe_parse_date(text)
            if dt:
                return format_date(dt)

        # Google arama sonuçlarında tarih genelde snippet'in başında
        snippet_el = parent_element.select_one('.VwiC3b, .st, .IsZvec, .result__snippet')
        if snippet_el:
            snippet_text = snippet_el.get_text(strip=True)
            result = extract_date_from_google_snippet(snippet_text)
            if result:
                return result

    # Parent'a erişemiyorsak, çevreleyen elementlere bak
    if hasattr(a_tag, 'find_next_sibling'):
        for sibling in a_tag.find_next_siblings(limit=3):
            text = sibling.get_text(strip=True)
            if text and len(text) < 100:
                result = extract_date_from_google_snippet(text)
                if result:
                    return result

    return ""


def mark_unknown_date() -> str:
    """Tarih belirlenemediğinde kullanılacak işaretçi.
    Bugünün tarihini yazmak yerine bunu kullanırız.
    """
    return ""


# ---------------------------------------------------------------------------
#  Sosyal Medya Güncellik Filtreleri
# ---------------------------------------------------------------------------

def is_post_url(url: str) -> bool:
    """URL'nin gerçek bir paylaşım mı yoksa profil/sayfa mı olduğunu kontrol eder.
    Profil sayfaları tarih içermez ve içerik çekmek zaman kaybıdır.
    """
    if not url:
        return False

    # Twitter/X: /status/ID olan URL'ler gerçek tweet
    if 'twitter.com' in url or 'x.com' in url:
        return bool(re.search(r'/status/\d+', url))

    # LinkedIn: /posts/, /pulse/, /feed/update/ olan URL'ler gerçek paylaşım
    if 'linkedin.com' in url:
        return bool(re.search(r'/(posts|pulse|feed/update)/', url))

    return True  # Bilinmeyen platformlar için varsayılan olarak kabul et


def extract_date_from_url_id(url: str) -> Optional[str]:
    """URL'deki platform ID'sinden tarih çıkarır (Snowflake).
    Sayfa içeriğine bakmadan, sadece URL'den tarih belirlemeye çalışır.
    """
    if 'twitter.com' in url or 'x.com' in url:
        return extract_date_from_twitter_id(url)
    if 'linkedin.com' in url:
        return extract_date_from_linkedin_id(url)
    return None


def is_recent_url(url: str, max_days: int = 7) -> Optional[bool]:
    """URL'deki Snowflake ID'den tarihi çıkarıp güncel olup olmadığını kontrol eder.
    Returns:
        True  — URL güncel (max_days içinde)
        False — URL eski
        None  — Tarih belirlenemedi (karar verilemez)
    """
    date_str = extract_date_from_url_id(url)
    if not date_str:
        return None  # Tarih çıkarılamadı, karar verilemiyor

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        age_days = (datetime.now() - dt).days
        if age_days <= max_days:
            return True
        else:
            logger.info(f"[DateUtils] Eski post atlanıyor ({age_days} gün): {url[:80]}")
            return False
    except Exception:
        return None

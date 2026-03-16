"""
Browser Helper — Anti-bot koruması için merkezi browser ve crawler yapılandırması.
Google ve DuckDuckGo aramalarında bot algılamadan kaçınmak için:
- Gerçekçi User-Agent
- Google CONSENT cookie (cookie consent bypass)
- navigator.webdriver override
- Stealth Chromium argümanları
- httpx tabanlı Google arama (tarayıcı parmak izi olmadan)
"""

import random
import urllib.parse
import asyncio
from typing import List, Dict, Optional
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from crawl4ai import BrowserConfig, CrawlerRunConfig, CacheMode

from utils.logger import setup_logger

logger = setup_logger(__name__)

# Gerçekçi User-Agent havuzu (güncel Chrome/Edge)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# Stealth Chromium argümanları
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-popup-blocking",
    "--disable-background-networking",
]

# Google cookie consent bypass
GOOGLE_COOKIES = [
    {"name": "CONSENT", "value": "PENDING+987", "domain": ".google.com", "path": "/"},
    {"name": "CONSENT", "value": "PENDING+987", "domain": ".google.com.tr", "path": "/"},
    {"name": "SOCS", "value": "CAISHAgBEhJnd3NfMjAyNDAzMTAtMF9SQzIaAmVuIAEaBgiA_LCzBg", "domain": ".google.com", "path": "/"},
    {"name": "SOCS", "value": "CAISHAgBEhJnd3NfMjAyNDAzMTAtMF9SQzIaAmVuIAEaBgiA_LCzBg", "domain": ".google.com.tr", "path": "/"},
]


def get_browser_config(headless: bool = True) -> BrowserConfig:
    """Anti-bot korumalı BrowserConfig döndürür."""
    return BrowserConfig(
        headless=headless,
        user_agent=random.choice(USER_AGENTS),
        viewport_width=1920,
        viewport_height=1080,
        extra_args=STEALTH_ARGS,
        cookies=GOOGLE_COOKIES,
        headers={
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
        },
    )


def get_crawler_config(page_timeout: int = 30000) -> CrawlerRunConfig:
    """Standart CrawlerRunConfig döndürür."""
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=page_timeout,
        magic=True,
        simulate_user=True,
        override_navigator=True,
        delay_before_return_html=1.0,
    )


# ---------------------------------------------------------------------------
#  httpx ile Google Arama — tarayıcı parmak izi olmadan bot algılamayı aşar
# ---------------------------------------------------------------------------

def _get_httpx_headers() -> dict:
    """Google araması için gerçekçi HTTP headers döndürür."""
    ua = random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    }


def _get_google_cookies() -> dict:
    """Google CONSENT bypass cookie'leri (httpx cookie jar formatında)."""
    return {
        "CONSENT": "PENDING+987",
        "SOCS": "CAISHAgBEhJnd3NfMjAyNDAzMTAtMF9SQzIaAmVuIAEaBgiA_LCzBg",
    }


async def google_search_httpx(
    query: str,
    max_results: int = 10,
    lang: str = "tr",
    country: str = "TR",
    date_filter: str = "",
) -> List[Dict]:
    """
    Startpage.com üzerinden Google araması yapar — bot koruması yok.
    Startpage, Google sonuçlarını gösteren privacy-focused bir arama motoru.
    httpx ile POST request yaparak sonuçları çeker.

    Args:
        date_filter: Startpage zaman filtresi — "d" (son 1 gün), "w" (son 1 hafta),
                     "m" (son 1 ay), "" (filtre yok)

    Returns:
        [{"url": ..., "title": ..., "snippet": ...}, ...]
    """
    logger.info(f"[SearchHTTPX] Arama: '{query}' (date_filter={date_filter or 'yok'})")

    results = []

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            # Startpage POST request ile arama yapar
            post_data = {"query": query, "cat": "web", "language": "turkish"}
            if date_filter:
                post_data["with_date"] = date_filter

            response = await client.post(
                "https://www.startpage.com/sp/search",
                data=post_data,
                headers=_get_httpx_headers(),
            )

            if response.status_code != 200:
                logger.warning(f"[SearchHTTPX] HTTP {response.status_code}")
                return results

            html = response.text
            html_size = len(html)
            logger.info(f"[SearchHTTPX] HTML boyutu: {html_size} karakter")

            if html_size < 5000:
                logger.warning(f"[SearchHTTPX] Muhtemelen bloklandı ({html_size})")
                return results

            soup = BeautifulSoup(html, "html.parser")

            # Startpage sonuç linkleri: a[data-testid] veya .result içindeki linkler
            seen_urls = set()
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]

                if not href.startswith("http"):
                    continue
                # Startpage iç linklerini atla
                if "startpage.com" in href or "ixquick.com" in href:
                    continue

                title = a_tag.get_text(strip=True)
                if len(title) < 10:
                    continue

                # URL temizleme — bazen URL ve title aynı a tag'de
                # "https://example.comSayfa Başlığı" şeklinde geliyor
                # URL kısmını title'dan çıkar
                if title.startswith("http"):
                    continue  # Bu sadece URL gösteren link, başlık değil

                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Snippet'i bul
                snippet = ""
                parent = a_tag.find_parent(class_="result") or a_tag.find_parent(class_="w-gl__result")
                if parent:
                    for p_tag in parent.find_all(["p", "span"]):
                        text = p_tag.get_text(strip=True)
                        if len(text) > 40 and text != title and not text.startswith("http"):
                            snippet = text
                            break

                results.append({
                    "url": href,
                    "title": title,
                    "snippet": snippet,
                })

                if len(results) >= max_results:
                    break

            logger.info(f"[SearchHTTPX] {len(results)} sonuç bulundu")

    except httpx.TimeoutException:
        logger.warning("[SearchHTTPX] Zaman aşımı")
    except Exception as e:
        logger.error(f"[SearchHTTPX] Hata: {e}")

    return results


async def google_search_social_httpx(
    topic: str,
    platform: str = "linkedin",
    max_results: int = 5,
    recency_days: int = 7,
) -> List[Dict]:
    """
    Sosyal medya için httpx ile Google araması (Startpage üzerinden).
    - Startpage date filter ile sadece son N gün
    - Sadece gerçek paylaşım URL'leri (profil sayfaları filtrelenir)
    - Snowflake ID ile tarih pre-check (eski postlar atlanır)
    """
    from crawlers.date_utils import is_post_url, is_recent_url

    today_date = datetime.now().strftime("%m.%d.%Y")
    yesterday_date = (datetime.now() - timedelta(days=1)).strftime("%m.%d.%Y")

    # İki sorgu: bugün + dün
    queries = [
        f"{topic} {today_date} {platform}",
        f"{topic} {yesterday_date} {platform}",
    ]

    all_results = []
    seen_urls = set()

    import re
    # Platform URL pattern'leri — sadece gerçek paylaşım URL'leri
    platform_patterns = {
        "linkedin": re.compile(r"linkedin\.com/(posts|pulse|feed/update)/"),
        "twitter": re.compile(r"(twitter\.com|x\.com)/\w+/status/\d+"),
    }
    exclude_patterns = {
        "linkedin": re.compile(r"/(?:login|signup|jobs|company$|learning|salary)", re.I),
        "twitter": re.compile(r"/(?:login|signup|explore|search|settings|tos|privacy|help|i/)", re.I),
    }

    pattern = platform_patterns.get(platform)
    exclude = exclude_patterns.get(platform)

    for query in queries:
        if len(all_results) >= max_results:
            break

        # Startpage date filter: with_date=w (son 1 hafta)
        results = await google_search_httpx(
            query, max_results=10, date_filter="w",
        )

        for item in results:
            if len(all_results) >= max_results:
                break

            url = item["url"]

            # Platform URL filtresi — sadece gerçek paylaşımlar
            if pattern and not pattern.search(url):
                continue
            if exclude and exclude.search(url):
                continue
            if url in seen_urls:
                continue

            # Snowflake ID tarih pre-check — eski postları atla
            recency = is_recent_url(url, max_days=recency_days)
            if recency is False:
                continue  # Kesin eski, atla

            seen_urls.add(url)
            all_results.append({
                "url": url,
                "title": item["title"],
                "snippet": item.get("snippet", ""),
                "platform": platform,
                "source_type": "social_media",
                "search_source": "Startpage (Google)",
            })

    logger.info(f"[GoogleHTTPX] {platform}: {len(all_results)} sosyal medya sonucu (recency={recency_days}d)")
    return all_results

"""
Social Media Crawler Module
LinkedIn ve Twitter/X üzerinden güncel haberler/paylaşımlar çeker.
Google arama motoru üzerinden site: filtresi ile arama yapar,
böylece herhangi bir API key'e ihtiyaç duymadan çalışır.
"""

import sys
import asyncio

if sys.platform == 'win32':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

import urllib.parse
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from bs4 import BeautifulSoup

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig, CacheMode
from crawlers.browser_helper import (
    get_browser_config,
    get_crawler_config,
    google_search_social_httpx,
)

from utils.logger import setup_logger
from config import config
from crawlers.date_utils import (
    extract_social_date,
    extract_date_from_search_result,
    extract_date_from_google_snippet,
    mark_unknown_date,
    is_post_url,
    is_recent_url,
)

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
#  Desteklenen Sosyal Medya Platformları
# ---------------------------------------------------------------------------
SOCIAL_PLATFORMS = {
    "twitter": {
        "name": "Twitter/X",
        "site_filter": "site:twitter.com OR site:x.com",
        "icon": "🐦",
        # Twitter linklerinden gerçek tweet URL'lerini yakalamak için pattern
        "url_patterns": [
            re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/\d+'),
            re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+'),
        ],
        "exclude_patterns": [
            re.compile(r'/(?:login|signup|explore|search|settings|tos|privacy|help)', re.I),
        ],
    },
    "linkedin": {
        "name": "LinkedIn",
        "site_filter": "site:linkedin.com/posts OR site:linkedin.com/pulse",
        "icon": "💼",
        "url_patterns": [
            re.compile(r'https?://(?:www\.)?linkedin\.com/posts/[^\s]+'),
            re.compile(r'https?://(?:www\.)?linkedin\.com/pulse/[^\s]+'),
            re.compile(r'https?://(?:www\.)?linkedin\.com/feed/update/[^\s]+'),
        ],
        "exclude_patterns": [
            re.compile(r'/(?:login|signup|jobs|company$|in$|school)', re.I),
        ],
    },
}


class SocialCrawler:
    """
    Google arama motorunu kullanarak LinkedIn ve Twitter/X üzerinde
    belirli bir konuyla ilgili son 1-2 günlük paylaşımları bulur ve içeriklerini çeker.
    """

    def __init__(self):
        self.max_results_per_platform = config.SOCIAL_MAX_RESULTS_PER_PLATFORM
        self.timeout = config.CRAWL_TIMEOUT
        self.recency_days = config.SOCIAL_SEARCH_RECENCY_DAYS
        self.enabled_platforms = config.SOCIAL_PLATFORMS

    # ------------------------------------------------------------------
    #  ANA METOT: Tüm platformlardan sonuçları topla
    # ------------------------------------------------------------------
    async def fetch_social_data(self, topic: str) -> List[Dict]:
        """Tüm aktif platformlarda arama yapıp sonuçları birleştirir."""
        if not config.SOCIAL_SEARCH_ENABLED:
            logger.info("[SocialCrawler] Sosyal medya araması devre dışı (SOCIAL_SEARCH_ENABLED=false)")
            return []

        logger.info(f"[SocialCrawler] Sosyal medya araması başlıyor: {topic}")
        logger.info(f"[SocialCrawler] Aktif platformlar: {', '.join(self.enabled_platforms)}")

        all_results: List[Dict] = []

        for platform_key in SOCIAL_PLATFORMS:
            # Sadece config'de etkin olan platformlarda ara
            if platform_key not in self.enabled_platforms:
                logger.info(f"[SocialCrawler] {platform_key} atlandı (config'de etkin değil)")
                continue
            try:
                results = await self._search_platform(topic, platform_key)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"[SocialCrawler] {platform_key} aramasında hata: {e}")

        logger.info(f"[SocialCrawler] Toplam {len(all_results)} sosyal medya sonucu bulundu")
        return all_results

    # ------------------------------------------------------------------
    #  Platform bazlı arama (Google site: filtresi ile)
    # ------------------------------------------------------------------
    async def _search_platform(self, topic: str, platform_key: str) -> List[Dict]:
        """Belirli bir platform için DuckDuckGo + Google httpx ile arama yapar.
        1. DuckDuckGo (headless browser) — birincil kaynak
        2. Google httpx (sade HTTP) — ek kaynak, bot algılamayı aşar
        Bulunan URL'leri doğrudan açıp içerik çeker.
        """
        platform = SOCIAL_PLATFORMS[platform_key]
        results: List[Dict] = []
        seen_urls: set = set()

        today_date = datetime.now().strftime("%m.%d.%Y")
        # DuckDuckGo için basit sorgu: konu + tarih + site filtresi
        site_filter = platform["site_filter"]
        raw_query = f"{topic} {today_date} {site_filter}"
        encoded_query = urllib.parse.quote_plus(raw_query)
        ddg_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        logger.info(f"[SocialCrawler] {platform['name']} DuckDuckGo araması: {raw_query}")

        try:
            browser_config = get_browser_config()
            crawler_config = get_crawler_config()

            async with AsyncWebCrawler(config=browser_config) as crawler:
                # --- Kaynak 1: DuckDuckGo ---
                try:
                    g_result = await crawler.arun(url=ddg_url, config=crawler_config)
                    if g_result.success and hasattr(g_result, 'html') and g_result.html:
                        soup = BeautifulSoup(g_result.html, 'html.parser')
                        for a_tag in soup.find_all('a', href=True):
                            href = a_tag['href']
                            if 'uddg=' in href:
                                href = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])
                            if not self._is_valid_platform_url(href, platform):
                                continue
                            # Profil sayfalarını atla (sadece gerçek paylaşımlar)
                            if not is_post_url(href):
                                logger.info(f"[SocialCrawler] Profil sayfası atlandı: {href[:80]}")
                                continue
                            # Snowflake ID ile güncellik kontrolü
                            recency = is_recent_url(href, max_days=self.recency_days)
                            if recency is False:
                                continue  # Kesin eski post
                            if href in seen_urls:
                                continue
                            title = a_tag.get_text(strip=True)
                            if len(title) < 15:
                                continue
                            parent_el = a_tag.find_parent(class_='result') or a_tag.find_parent(class_='web-result')
                            snippet_date = extract_date_from_search_result(a_tag, parent_el)
                            if not snippet_date:
                                snippet_date = extract_social_date("", href)
                            seen_urls.add(href)
                            results.append({
                                "url": href, "title": title,
                                "date": snippet_date, "platform": platform_key,
                            })
                            if len(results) >= self.max_results_per_platform:
                                break
                        logger.info(f"[SocialCrawler] DuckDuckGo: {len(results)} sonuç")
                    else:
                        logger.warning(f"[SocialCrawler] DuckDuckGo başarısız: {platform['name']}")
                except Exception as e:
                    logger.warning(f"[SocialCrawler] DuckDuckGo hatası: {e}")

                # --- Kaynak 2: Google httpx (bot korumasını aşar) ---
                if len(results) < self.max_results_per_platform:
                    try:
                        google_results = await google_search_social_httpx(
                            topic, platform=platform_key,
                            max_results=self.max_results_per_platform - len(results),
                            recency_days=self.recency_days,
                        )
                        for g_item in google_results:
                            url = g_item["url"]
                            if not self._is_valid_platform_url(url, platform):
                                continue
                            if url in seen_urls:
                                continue
                            seen_urls.add(url)
                            results.append({
                                "url": url, "title": g_item["title"],
                                "date": "", "platform": platform_key,
                            })
                            if len(results) >= self.max_results_per_platform:
                                break
                        logger.info(f"[SocialCrawler] Google httpx ek: toplam {len(results)} sonuç")
                    except Exception as e:
                        logger.warning(f"[SocialCrawler] Google httpx hatası: {e}")

                if not results:
                    logger.info(f"[SocialCrawler] {platform['name']}: Hiç sonuç bulunamadı")
                    return []

                # Bulunan URL'leri doğrudan açıp içerik çek
                detailed_articles = []
                for item in results:
                    try:
                        article = await self._extract_social_content(
                            crawler, crawler_config, item, platform, topic
                        )
                        if article:
                            detailed_articles.append(article)
                    except Exception as e:
                        logger.error(f"[SocialCrawler] İçerik çekme hatası ({item['url']}): {e}")
                        detailed_articles.append(self._create_fallback_article(item, platform, topic))

                logger.info(
                    f"[SocialCrawler] {platform['name']}: {len(detailed_articles)} sonuç çekildi"
                )
                return detailed_articles

        except Exception as e:
            logger.error(f"[SocialCrawler] {platform['name']} arama hatası: {e}")
            return results

    # ------------------------------------------------------------------
    #  Sosyal medya URL'sinden içerik çekme
    # ------------------------------------------------------------------
    async def _extract_social_content(
        self,
        crawler,
        crawler_config: CrawlerRunConfig,
        item: Dict,
        platform: Dict,
        topic: str,
    ) -> Optional[Dict]:
        """Tek bir sosyal medya linkinden detaylı içerik çeker."""
        url = item["url"]
        logger.info(f"[SocialCrawler] İçerik çekiliyor: {url}")

        try:
            result = await crawler.arun(url=url, config=crawler_config)

            if not result.success:
                logger.warning(f"[SocialCrawler] Sayfa yüklenemedi: {url}")
                return self._create_fallback_article(item, platform, topic)

            content = ""
            title = item.get("title", "")

            # Markdown veya HTML'den içerik çıkar
            if hasattr(result, 'markdown') and result.markdown:
                content = result.markdown
            elif hasattr(result, 'html') and result.html:
                soup = BeautifulSoup(result.html, 'html.parser')
                # Script, style ve navigasyon elementlerini temizle
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                content = soup.get_text(separator='\n', strip=True)

            if not content or len(content) < 50:
                return self._create_fallback_article(item, platform, topic)

            # İçeriği sınırla (sosyal medya paylaşımları genelde kısa)
            content = content[:5000]

            # Daha iyi bir başlık çıkarmayı dene
            if hasattr(result, 'html') and result.html:
                extracted_title = self._extract_social_title(result.html, platform)
                if extracted_title and len(extracted_title) > len(title):
                    title = extracted_title

            # Tarih çıkarmayı dene — date_utils ile kapsamlı çıkarım
            page_html = result.html if hasattr(result, 'html') else ""
            extracted_date = extract_social_date(page_html, url)
            # Öncelik: sayfa içinden çıkarılan > arama snippet'inden gelen > bilinmiyor
            pub_date = extracted_date or item.get("date", "") or mark_unknown_date()
            if pub_date:
                logger.info(f"[SocialCrawler] Tarih bulundu: {pub_date} ({url[:60]})")
            else:
                logger.warning(f"[SocialCrawler] Tarih belirlenemedi: {url[:60]}")

            return {
                "title": f"{platform['icon']} {title}",
                "summary": content,
                "url": url,
                "published_date": pub_date,
                "content": content,
                "topic": topic,
                "source_type": "social_media",
                "platform": item.get("platform", "unknown"),
            }

        except Exception as e:
            logger.error(f"[SocialCrawler] İçerik çekme hatası ({url}): {e}")
            return self._create_fallback_article(item, platform, topic)

    # ------------------------------------------------------------------
    #  Yardımcı Metotlar
    # ------------------------------------------------------------------
    def _is_valid_platform_url(self, url: str, platform: Dict) -> bool:
        """URL'nin belirtilen platforma ait geçerli bir link olup olmadığını kontrol eder."""
        if not url.startswith('http'):
            return False

        # Exclude pattern'lerini kontrol et
        for pattern in platform.get("exclude_patterns", []):
            if pattern.search(url):
                return False

        # En az bir URL pattern'ine uymalı
        for pattern in platform["url_patterns"]:
            if pattern.search(url):
                return True

        return False

    def _extract_social_title(self, html: str, platform: Dict) -> str:
        """Sosyal medya sayfasından başlık çıkarır."""
        soup = BeautifulSoup(html, 'html.parser')

        # og:title meta etiketi (sosyal medya platformları genelde bunu iyi doldurur)
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

        # twitter:title
        tw_title = soup.find('meta', attrs={'name': 'twitter:title'})
        if tw_title and tw_title.get('content'):
            return tw_title['content'].strip()

        # Sayfa title
        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            # Platform adını title'dan temizle
            for suffix in ['| LinkedIn', '| Twitter', '/ X', '/ Twitter', '- LinkedIn']:
                title_text = title_text.replace(suffix, '').strip()
            if len(title_text) > 10:
                return title_text

        return ""

    def _extract_date_from_social(self, html: str, url: str = "") -> str:
        """Sosyal medya sayfasından tarih çıkarır. date_utils modülüne yönlendirir."""
        return extract_social_date(html, url)

    def _create_fallback_article(self, item: Dict, platform: Dict, topic: str) -> Dict:
        """Detaylı içerik çekilemediğinde, arama sonucundan basit bir article oluşturur."""
        # URL'den platform ID ile tarih çıkarmayı dene
        url = item.get("url", "")
        fallback_date = item.get("date", "") or extract_social_date("", url) or mark_unknown_date()
        return {
            "title": f"{platform['icon']} {item.get('title', topic)}",
            "summary": f"{platform['name']} paylaşımı: {item.get('title', '')}",
            "url": url,
            "published_date": fallback_date,
            "content": f"{platform['name']} paylaşımı: {item.get('title', '')}",
            "topic": topic,
            "source_type": "social_media",
            "platform": item.get("platform", "unknown"),
        }

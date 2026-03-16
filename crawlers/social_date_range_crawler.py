"""
Social Media Date Range Crawler
================================
Mevcut sosyal medya taramasına EK olarak çalışan modül.
Belirli bir tarih aralığında (ör: son 7 gün) sosyal medya paylaşımlarını
Google'ın tarih aralığı filtresi (cdr) ile arar.

Mevcut SocialCrawler ve WebCrawler yapısını DEĞİŞTİRMEZ.
fetch_live_data pipeline'ına ek sonuçlar olarak eklenir.

Google tbs parametresi:
  cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY
  → Belirtilen tarih aralığındaki sonuçları döndürür.
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
    google_search_httpx,
    google_search_social_httpx,
)

from utils.logger import setup_logger
from config import config
from crawlers.date_utils import (
    extract_social_date,
    extract_date_from_search_result,
    extract_date_from_google_snippet,
    mark_unknown_date,
    safe_parse_date,
    format_date,
    validate_date,
    is_post_url,
    is_recent_url,
)

logger = setup_logger(__name__)

# Platform tanımları
PLATFORMS = {
    "twitter": {
        "name": "Twitter/X",
        "site_filter": "site:x.com OR site:twitter.com",
        "icon": "🐦",
        "post_pattern": re.compile(r'(twitter\.com|x\.com)/\w+/status/\d+'),
        "any_pattern": re.compile(r'(twitter\.com|x\.com)/'),
        "exclude_pattern": re.compile(r'/(?:login|signup|explore|search|settings|tos|privacy|help)', re.I),
    },
    "linkedin": {
        "name": "LinkedIn",
        "site_filter": "site:linkedin.com/posts OR site:linkedin.com/pulse OR site:linkedin.com/feed",
        "icon": "💼",
        "post_pattern": re.compile(r'linkedin\.com/(posts|pulse|feed/update)/'),
        "any_pattern": re.compile(r'linkedin\.com/'),
        "exclude_pattern": re.compile(r'/(?:login|signup|jobs|company$|in$|school)', re.I),
    },
}


class SocialDateRangeCrawler:
    """
    Belirli bir tarih aralığında sosyal medya paylaşımlarını arayan ek modül.
    Mevcut yapıdan bağımsız çalışır, sonuçlar merge edilir.
    """

    def __init__(self):
        self.max_results_per_platform = getattr(config, 'DATE_RANGE_MAX_PER_PLATFORM', 5)
        self.timeout = config.CRAWL_TIMEOUT
        self.enabled_platforms = config.SOCIAL_PLATFORMS
        self.range_days = getattr(config, 'DATE_RANGE_DAYS', 7)

    async def fetch_date_range_social(
        self,
        topic: str,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[Dict]:
        """
        Ana metot: Belirtilen tarih aralığında sosyal medya araması yapar.

        Args:
            topic: Aranacak konu
            date_from: Başlangıç tarihi (varsayılan: bugünden DATE_RANGE_DAYS gün önce)
            date_to: Bitiş tarihi (varsayılan: bugün)

        Returns:
            Bulunan sosyal medya makalelerinin listesi
        """
        if not getattr(config, 'DATE_RANGE_SEARCH_ENABLED', True):
            logger.info("[DateRange] Tarih aralığı araması devre dışı")
            return []

        if not config.SOCIAL_SEARCH_ENABLED:
            logger.info("[DateRange] Sosyal medya araması devre dışı")
            return []

        # Varsayılan tarih aralığı
        if date_to is None:
            date_to = datetime.now()
        if date_from is None:
            date_from = date_to - timedelta(days=self.range_days)

        date_from_str = date_from.strftime("%m/%d/%Y")
        date_to_str = date_to.strftime("%m/%d/%Y")

        logger.info(f"[DateRange] Sosyal medya tarih araligi aramasi: {topic}")
        logger.info(f"[DateRange] Tarih araligi: {date_from.strftime('%Y-%m-%d')} - {date_to.strftime('%Y-%m-%d')}")

        all_results: List[Dict] = []

        for platform_key, platform in PLATFORMS.items():
            if platform_key not in self.enabled_platforms:
                continue
            try:
                results = await self._search_platform_date_range(
                    topic, platform_key, platform, date_from_str, date_to_str
                )
                all_results.extend(results)
            except Exception as e:
                logger.error(f"[DateRange] {platform['name']} hata: {e}")

        logger.info(f"[DateRange] Toplam {len(all_results)} ek sosyal medya sonucu bulundu")
        return all_results

    async def _search_platform_date_range(
        self,
        topic: str,
        platform_key: str,
        platform: Dict,
        date_from: str,
        date_to: str,
    ) -> List[Dict]:
        """Belirli bir platform için Google httpx ile arama yapar.
        httpx kullanarak bot algılamayı aşar, bulunan URL'leri crawl4ai ile açar.
        """
        results: List[Dict] = []

        logger.info(f"[DateRange] {platform['name']} Google httpx araması: {topic}")

        try:
            # Google httpx ile URL'leri bul (tarih filtreli)
            google_results = await google_search_social_httpx(
                topic, platform=platform_key,
                max_results=self.max_results_per_platform,
                recency_days=self.range_days,
            )

            if not google_results:
                logger.info(f"[DateRange] {platform['name']}: Google httpx sonuç bulunamadı")
                return results

            logger.info(f"[DateRange] {platform['name']}: {len(google_results)} URL bulundu, içerik çekiliyor...")

            # Bulunan URL'leri crawl4ai ile doğrudan aç ve içerik çek
            browser_config = get_browser_config()
            crawler_config = get_crawler_config()

            async with AsyncWebCrawler(config=browser_config) as crawler:
                for g_item in google_results:
                    item = {
                        "url": g_item["url"],
                        "title": g_item["title"],
                        "date": "",
                        "platform": platform_key,
                    }
                    try:
                        article = await self._extract_content(
                            crawler, crawler_config, item, platform, topic
                        )
                        if article:
                            article["search_source"] = "Startpage (Google Tarih Aralığı)"
                            results.append(article)
                    except Exception as e:
                        logger.error(f"[DateRange] İçerik çekme hatası ({item['url']}): {e}")
                        results.append(self._create_fallback(item, platform, topic))

            logger.info(f"[DateRange] {platform['name']}: {len(results)} sonuç tamamlandı")

        except Exception as e:
            logger.error(f"[DateRange] {platform['name']} genel hata: {e}")

        return results

    async def _extract_content(
        self,
        crawler,
        crawler_config: CrawlerRunConfig,
        item: Dict,
        platform: Dict,
        topic: str,
    ) -> Optional[Dict]:
        """Sosyal medya URL'sinden içerik ve doğru tarih çeker."""
        url = item["url"]
        logger.info(f"[DateRange] İçerik çekiliyor: {url}")

        try:
            result = await crawler.arun(url=url, config=crawler_config)

            if not result.success:
                logger.warning(f"[DateRange] Sayfa yüklenemedi: {url}")
                return self._create_fallback(item, platform, topic)

            content = ""
            title = item.get("title", "")

            # İçerik çıkarma: önce meta description, sonra markdown, sonra HTML
            page_html = result.html if hasattr(result, 'html') else ""

            # Meta tag'lerden içerik (LinkedIn/Twitter login olmadan bile verir)
            if page_html:
                meta_content = self._extract_meta_content(page_html)
                if meta_content and len(meta_content) > 30:
                    content = meta_content

            # Markdown fallback
            if not content and hasattr(result, 'markdown') and result.markdown:
                content = result.markdown[:5000]

            # HTML fallback
            if not content and page_html:
                soup = BeautifulSoup(page_html, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                content = soup.get_text(separator='\n', strip=True)[:5000]

            if not content or len(content) < 30:
                return self._create_fallback(item, platform, topic)

            # Başlık iyileştirme
            if page_html:
                extracted_title = self._extract_title(page_html)
                if extracted_title and len(extracted_title) > len(title):
                    title = extracted_title

            # Tarih çıkarma — kapsamlı (date_utils)
            extracted_date = extract_social_date(page_html, url)
            # Öncelik: sayfa HTML > arama snippet > URL ID > bilinmiyor
            pub_date = extracted_date or item.get("date", "") or mark_unknown_date()

            if pub_date:
                logger.info(f"[DateRange] Tarih: {pub_date} | {title[:50]}")
            else:
                logger.warning(f"[DateRange] Tarih belirlenemedi: {url[:60]}")

            return {
                "title": f"{platform['icon']} {title}",
                "summary": content,
                "url": url,
                "published_date": pub_date,
                "content": content,
                "topic": topic,
                "source_type": "social_media",
                "platform": item.get("platform", "unknown"),
                "search_source": "Google (Tarih Aralığı)",
            }

        except Exception as e:
            logger.error(f"[DateRange] İçerik çekme hatası ({url}): {e}")
            return self._create_fallback(item, platform, topic)

    def _extract_meta_content(self, html: str) -> str:
        """HTML'den og:description, twitter:description veya description meta tag'ini çeker."""
        soup = BeautifulSoup(html, 'html.parser')
        for prop in ['og:description', 'twitter:description', 'description']:
            meta = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if meta and meta.get('content') and len(meta['content'].strip()) > 30:
                return meta['content'].strip()[:5000]
        return ""

    def _extract_title(self, html: str) -> str:
        """Sayfa başlığını çıkarır."""
        soup = BeautifulSoup(html, 'html.parser')

        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

        tw_title = soup.find('meta', attrs={'name': 'twitter:title'})
        if tw_title and tw_title.get('content'):
            return tw_title['content'].strip()

        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text(strip=True)
            for suffix in ['| LinkedIn', '| Twitter', '/ X', '/ Twitter', '- LinkedIn']:
                title_text = title_text.replace(suffix, '').strip()
            if len(title_text) > 10:
                return title_text

        return ""

    def _create_fallback(self, item: Dict, platform: Dict, topic: str) -> Dict:
        """Detaylı içerik çekilemediğinde fallback article oluşturur."""
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
            "search_source": "Google (Tarih Aralığı)",
        }

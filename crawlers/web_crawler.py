import sys
import asyncio

# Windows Playwright asyncio ProactorEventLoop fix
if sys.platform == 'win32':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

import httpx
from typing import List, Dict, Optional
from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, LLMConfig, BrowserConfig, CacheMode
from crawl4ai import LLMExtractionStrategy
import feedparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import urllib.parse
import re
import dateutil.parser

from utils.logger import setup_logger
from config import config
from crawlers.date_utils import (
    extract_social_date,
    extract_date_from_html as date_utils_extract_date_from_html,
    extract_date_from_search_result,
    extract_date_from_google_snippet,
    mark_unknown_date,
    safe_parse_date,
    format_date,
    validate_date,
)
from crawlers.social_date_range_crawler import SocialDateRangeCrawler
from crawlers.browser_helper import (
    get_browser_config,
    get_crawler_config,
    google_search_httpx,
    google_search_social_httpx,
)

logger = setup_logger(__name__)

# ---------------------------------------------------------------------------
#  Google News RSS  –  ücretsiz, API key yok, gerçek zamanlı haberler
#  Bing News RSS    –  ek kaynak, yine ücretsiz
# ---------------------------------------------------------------------------
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
BING_NEWS_RSS   = "https://www.bing.com/news/search"

class WebCrawler:
    def __init__(self):
        self.timeout = config.CRAWL_TIMEOUT
        self.max_articles = config.MAX_ARTICLES_PER_TOPIC
        self.date_range_crawler = SocialDateRangeCrawler()
        
    def _extract_date_from_html(self, html: str) -> str:
        """Makale sayfasından yayın tarihini çıkar. Birden fazla strateji dener."""
        soup = BeautifulSoup(html, 'html.parser')

        # Strateji 1: <meta property="article:published_time">
        for prop in ['article:published_time', 'og:article:published_time', 'datePublished', 'og:updated_time']:
            meta = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if meta and meta.get('content'):
                try:
                    dt = dateutil.parser.parse(meta['content'], fuzzy=True)
                    return dt.strftime("%Y-%m-%d")
                except Exception:
                    pass

        # Strateji 2: <time datetime="..."> etiketi
        time_tag = soup.find('time', datetime=True)
        if time_tag:
            try:
                dt = dateutil.parser.parse(time_tag['datetime'], fuzzy=True)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass
        # <time> etiketi datetime attribute'suz ama metin içeriğiyle
        time_tag_text = soup.find('time')
        if time_tag_text and time_tag_text.get_text(strip=True):
            try:
                dt = dateutil.parser.parse(time_tag_text.get_text(strip=True), fuzzy=True)
                return dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # Strateji 3: JSON-LD içinde datePublished
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                import json
                ld = json.loads(script.string)
                # Tek obje veya liste olabilir
                items = ld if isinstance(ld, list) else [ld]
                for item in items:
                    for key in ['datePublished', 'dateCreated', 'dateModified']:
                        if key in item:
                            dt = dateutil.parser.parse(str(item[key]), fuzzy=True)
                            return dt.strftime("%Y-%m-%d")
            except Exception:
                pass

        # Strateji 4: class adında 'date', 'tarih', 'time', 'publish' geçen elementler
        date_selectors = [
            {'class_': re.compile(r'date|tarih|time|publish|yayın|zamani', re.I)},
            {'class_': re.compile(r'article.*(date|meta)', re.I)},
        ]
        for selector in date_selectors:
            el = soup.find(['span', 'div', 'p', 'time', 'small'], **selector)
            if el:
                text = el.get_text(strip=True)
                if len(text) < 60:  # Tarih çok uzun olmamalı
                    try:
                        dt = dateutil.parser.parse(text, fuzzy=True)
                        # Mantıklı bir tarih mi kontrol et (son 1 yıl içinde)
                        if abs((datetime.now() - dt).days) < 365:
                            return dt.strftime("%Y-%m-%d")
                    except Exception:
                        pass

        return ""

    def _extract_title_from_html(self, html: str) -> str:
        """Derin crawl yapılan sayfanın gerçek başlığını çıkar (h1 > og:title > title)"""
        soup = BeautifulSoup(html, 'html.parser')
        # Öncelik 1: h1 etiketi
        h1 = soup.find('h1')
        if h1 and len(h1.get_text(strip=True)) > 10:
            return h1.get_text(strip=True)
        # Öncelik 2: og:title meta etiketi
        og = soup.find('meta', property='og:title')
        if og and og.get('content') and len(og['content'].strip()) > 10:
            return og['content'].strip()
        # Öncelik 3: <title> etiketi
        title_tag = soup.find('title')
        if title_tag and len(title_tag.get_text(strip=True)) > 10:
            return title_tag.get_text(strip=True)
        return ""

    async def crawl_with_crawl4ai(self, url: str, topic: str, expected_date: str = None, expected_title: str = None) -> List[Dict]:
        """Crawl a single URL using Crawl4AI. If it's a category/list page, dive into ALL real article links."""
        try:
            browser_config = get_browser_config()
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=10,
                page_timeout=60000,
                magic=True,
                simulate_user=True,
                override_navigator=True,
            )

            async with AsyncWebCrawler(config=browser_config) as crawler:
                logger.info(f"Crawling root/initial URL: {url}")
                result = await crawler.arun(url=url, config=crawler_config)

                # DERİNLEMESİNE ARAMA YAKLAŞIMI (DEPTH=1)
                # Sitenin kendi içine (Örn: fnss.com.tr/haberler) girip oradaki TÜM güncel haberlere tek tek dalıyoruz.
                if result.success and hasattr(result, 'html') and result.html:
                    soup = BeautifulSoup(result.html, 'html.parser')
                    parsed_base = urllib.parse.urlparse(url)

                    # Sayfadaki navbar ve footer menülerini filtrelemeden önce çöp linkleri eyleyelim.
                    # Hedefimiz ortadaki "card", "item", "news", "post" gibi divler içindeki gerçek href'leri yakalamaktır.
                    valid_child_links = []

                    for a_tag in soup.find_all('a', href=True):
                        href = a_tag['href']

                        # Eğer href bir medya dosyası (zip, pdf, jpg) ise veya javascript, anchor ise atla
                        if href.startswith(('javascript:', '#', 'mailto:', 'tel:')): continue
                        if href.endswith(('.zip', '.pdf', '.jpg', '.png', '.rar', '.doc', '.docx')): continue

                        # Parent div class'larına bakıp bunun bir "Haber Kartı/Öğesi" olup olmadığını tahmin edelim
                        # 'news', 'item', 'card', 'article', 'post' gibi class'lar içinde olanlar
                        is_news_container = False
                        parent = a_tag.parent
                        for _ in range(3): # Yukarıya doğru 3 seviye kontrol et
                            if parent and parent.has_attr('class'):
                                class_str = ' '.join(parent['class']).lower()
                                if any(kw in class_str for kw in ['news', 'item', 'card', 'article', 'post', 'haber']):
                                    is_news_container = True
                                    break
                            if parent:
                                parent = parent.parent

                        # Eğer navbar veya menüyse atla (Genelde header, nav içinde olurlar)
                        is_menu = False
                        p = a_tag.parent
                        while p and p.name != 'body':
                            if p.name in ['nav', 'header', 'footer'] or (p.has_attr('id') and any(m in p['id'].lower() for m in ['menu', 'nav', 'header'])):
                                is_menu = True
                                break
                            p = p.parent

                        if is_menu: continue

                        full_href = urllib.parse.urljoin(url, href)
                        parsed_href = urllib.parse.urlparse(full_href)

                        # Link, girdiğimiz domain'e ait olmalı ve yolu daha uzun olmalı (Yani alt sayfa olmalı)
                        if parsed_href.netloc == parsed_base.netloc and len(parsed_href.path) > len(parsed_base.path) + 2:

                            # Daha gelişmiş filtre: Eğer div news kapsayıcısı ise veya link metni uzun bir cümle ise
                            link_text = a_tag.get_text(strip=True)

                            if is_news_container or len(link_text) > 25:
                                if full_href not in valid_child_links:
                                    valid_child_links.append(full_href)

                    # Eğer geçerli alt sayfalar bulduysak HEPSİNE dalıyoruz (max_articles kadar)
                    if valid_child_links:
                        # Kategori sayfasından en fazla max_articles kadar habere dal
                        links_to_crawl = valid_child_links[:self.max_articles]
                        logger.info(f"Detected category page with {len(valid_child_links)} article links. Diving into {len(links_to_crawl)} articles...")

                        all_deep_articles = []
                        for article_url in links_to_crawl:
                            try:
                                logger.info(f"  Diving into article: {article_url}")
                                deep_result = await crawler.arun(url=article_url, config=crawler_config)
                                if deep_result.success:
                                    deep_content = ""
                                    deep_title = ""

                                    # Gerçek makale başlığını çıkar (h1, og:title, title)
                                    if hasattr(deep_result, 'html') and deep_result.html:
                                        deep_title = self._extract_title_from_html(deep_result.html)

                                    # Markdown veya düz metin çıkarımı
                                    if hasattr(deep_result, 'markdown') and deep_result.markdown:
                                        deep_content = deep_result.markdown
                                    elif hasattr(deep_result, 'html') and deep_result.html:
                                        dsoup = BeautifulSoup(deep_result.html, 'html.parser')
                                        deep_content = dsoup.get_text(separator='\n', strip=True)

                                    if deep_content and len(deep_content) > 100:
                                        deep_content = deep_content[:8000]
                                        # Sayfadan gerçek yayın tarihini çıkar
                                        real_date = ""
                                        if hasattr(deep_result, 'html') and deep_result.html:
                                            real_date = self._extract_date_from_html(deep_result.html)
                                        final_date = real_date if real_date else (expected_date or datetime.now().strftime("%Y-%m-%d"))
                                        # Gerçek başlığı kullan, yoksa link metninden veya expected_title'dan al
                                        final_title = deep_title if deep_title else (expected_title or topic)

                                        article = {
                                            "title": final_title,
                                            "summary": deep_content,
                                            "url": article_url,
                                            "published_date": final_date,
                                            "content": deep_content,
                                            "topic": topic
                                        }
                                        logger.info(f"  Successfully extracted: '{final_title}' from {article_url} ({len(deep_content)} chars)")
                                        all_deep_articles.append(article)
                                    else:
                                        logger.warning(f"  No usable content from article: {article_url}")
                                else:
                                    logger.warning(f"  Failed to crawl article: {article_url}")
                            except Exception as e:
                                logger.error(f"  Error crawling article {article_url}: {str(e)}")
                                continue

                        if all_deep_articles:
                            logger.info(f"Extracted {len(all_deep_articles)} articles from category page {url}")
                            return all_deep_articles

                # Alt habere gidemezsek veya tekil haber içindeysek kendi sayfa metnini kullan
                final_content = ""
                if hasattr(result, 'markdown') and result.markdown:
                    final_content = result.markdown
                elif hasattr(result, 'cleaned_html') and result.cleaned_html:
                    soup_fb = BeautifulSoup(result.cleaned_html, 'html.parser')
                    final_content = soup_fb.get_text(separator='\n', strip=True)

                if final_content and len(final_content) > 100:
                    final_content = final_content[:8000]
                    # Tekil sayfa ise gerçek tarihi çıkar
                    real_date = ""
                    if hasattr(result, 'html') and result.html:
                        real_date = self._extract_date_from_html(result.html)
                    final_date = real_date if real_date else (expected_date or datetime.now().strftime("%Y-%m-%d"))
                    # Tekil sayfa ise gerçek başlığı çıkar
                    real_title = ""
                    if hasattr(result, 'html') and result.html:
                        real_title = self._extract_title_from_html(result.html)
                    final_title = real_title if real_title else (expected_title or topic)
                    article = {
                        "title": final_title,
                        "summary": final_content,
                        "url": url,
                        "published_date": final_date,
                        "content": final_content,
                        "topic": topic
                    }
                    logger.info(f"Successfully extracted real content from {url} ({len(final_content)} chars)")
                    return [article]
                else:
                    logger.warning(f"No usable content from {url}")
                    return []

        except Exception as e:
            logger.error(f"Error crawling {url}: {str(e)}")
            # Fallback to simple httpx-based crawling
            return await self._fallback_crawl(url, topic, expected_date, expected_title)
    
    async def _fallback_crawl(self, url: str, topic: str, expected_date: str = None, expected_title: str = None) -> List[Dict]:
        """Fallback crawler using httpx when Crawl4AI fails"""
        try:
            logger.info(f"Using fallback crawler for {url}")
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                content = soup.get_text(separator='\n', strip=True)[:3000]
                
                if content and len(content) > 100:
                    final_date = expected_date if expected_date else datetime.now().strftime("%Y-%m-%d")
                    final_title = expected_title if expected_title else topic
                    article = {
                        "title": final_title,
                        "summary": content,
                        "url": url,
                        "published_date": final_date,
                        "content": content,
                        "topic": topic
                    }
                    logger.info(f"Fallback extracted content from {url} ({len(content)} chars)")
                    return [article]
                return []
            
        except Exception as e:
            logger.error(f"Fallback crawler failed for {url}: {str(e)}")
            return []
    
    def crawl_rss_feeds(self, rss_urls: List[str], topic: str) -> List[Dict]:
        """Crawl RSS feeds for articles"""
        articles = []
        
        for rss_url in rss_urls:
            try:
                feed = feedparser.parse(rss_url)
                
                for entry in feed.entries[:self.max_articles]:
                    article = {
                        "title": entry.get("title", ""),
                        "summary": entry.get("summary", ""),
                        "url": entry.get("link", ""),
                        "published_date": entry.get("published", ""),
                        "content": self._extract_content_from_url(entry.get("link", "")),
                        "topic": topic
                    }
                    articles.append(article)
                    
            except Exception as e:
                logger.error(f"Error parsing RSS feed {rss_url}: {str(e)}")
                
        return articles
    
    def _extract_content_from_url(self, url: str) -> str:
        """Extract content from a single URL using httpx and BeautifulSoup"""
        try:
            import httpx
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = httpx.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()
            
            # Get text content
            text = soup.get_text()
            
            # Clean up text
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            # Return first 1000 characters
            return text[:1000] + "..." if len(text) > 1000 else text
            
        except Exception as e:
            logger.error(f"Error extracting content from {url}: {str(e)}")
            return ""
    
    async def search_news_urls(self, topic: str) -> List[Dict]:
        """Search for recent news by scraping Search Engines directly via Crawl4AI (Headless Browser)."""
        search_results = []
        seen = set()
        
        logger.info(f"Using Crawl4AI headless browser to scrape search engines for: {topic}")
        query = urllib.parse.quote_plus(topic)
        
        try:
            browser_config = get_browser_config()
            crawler_config = get_crawler_config()
            
            async with AsyncWebCrawler(config=browser_config) as crawler:

                # ÖNCELİK 1: Doğrudan Google Arama (Normal Google Search, News değil)
                # Sadece son 24 saati kapsayacak şekilde (qdr:d parametresi ile) ve Türkçe olarak arar.
                goog_url = f"https://www.google.com.tr/search?q={query}&tbm=nws&tbs=qdr:d&hl=tr&gl=TR"
                logger.info(f"Scraping Google Search Türkiye directly via Browser: {goog_url}")
                
                g_result = await crawler.arun(url=goog_url, config=crawler_config)
                if g_result.success and hasattr(g_result, 'html') and g_result.html:
                    soup = BeautifulSoup(g_result.html, 'html.parser')
                    # Google Search HTML yapısındaki standart a tagları içinden geçerli haber linklerini çıkar
                    for a_tag in soup.find_all('a', href=True):
                        href = a_tag['href']

                        # Google yönlendirmelerini temizle (/url?q=...)
                        if '/url?q=' in href:
                            href = href.split('/url?q=')[1].split('&')[0]
                            href = urllib.parse.unquote(href)

                        # Google iç linkleri değilse ve medya/pdf değilse ekle
                        if href.startswith('http') and not href.startswith('https://google.com') and not href.startswith('https://www.google.com') and not href.startswith('https://support.google.com'):
                            title = a_tag.get_text(strip=True)

                            if len(title) > 20: # Sadece uzun başlığı olan geçerli linkler
                                if href not in seen:
                                    # Google snippet'inden tarih çıkarmayı dene
                                    parent_el = a_tag.find_parent(['div', 'li', 'article'])
                                    snippet_date = extract_date_from_search_result(a_tag, parent_el)
                                    seen.add(href)
                                    search_results.append({
                                        "url": href,
                                        "date": snippet_date or "",
                                        "title": title,
                                        "search_source": "Google"
                                    })
                                    if len(search_results) >= 5: # En iyi 5 sonucu al yeter
                                        break

                # ÖNCELİK 2: Yeterli sonuç yoksa DuckDuckGo (Bing'i tamamen çıkardık, eski kalıyor diye)
                if len(search_results) < 3:
                    ddg_url = f"https://html.duckduckgo.com/html/?q={query}+haberler"
                    logger.info(f"Scraping DuckDuckGo HTML via Browser: {ddg_url}")
                    
                    ddg_result = await crawler.arun(url=ddg_url, config=crawler_config)
                    if ddg_result.success and hasattr(ddg_result, 'html') and ddg_result.html:
                        soup = BeautifulSoup(ddg_result.html, 'html.parser')
                        for a_tag in soup.select('a.result__snippet'):
                            href = a_tag.get('href', '')
                            title = a_tag.get_text(strip=True)
                            if "//duckduckgo.com/l/?uddg=" in href:
                                resolved_url = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])
                                if resolved_url not in seen and len(title) > 20:
                                    # DuckDuckGo snippet'inden tarih çıkar
                                    parent_el = a_tag.find_parent(class_='result') or a_tag.find_parent(class_='web-result')
                                    ddg_date = extract_date_from_search_result(a_tag, parent_el)
                                    seen.add(resolved_url)
                                    search_results.append({
                                        "url": resolved_url,
                                        "date": ddg_date or "",
                                        "title": title,
                                        "search_source": "DuckDuckGo"
                                    })
                                    
        except Exception as e:
            logger.error(f"Crawl4AI search engine scrape failed: {str(e)}")

        # Google headless + DDG yeterli sonuç vermediyse Google httpx ile dene
        if len(search_results) < 3:
            logger.info(f"[News] Headless sonuçlar yetersiz ({len(search_results)}), Google httpx deneniyor...")
            try:
                today_date = datetime.now().strftime("%m.%d.%Y")
                httpx_results = await google_search_httpx(
                    f"{topic} {today_date} haberler", max_results=5
                )
                for item in httpx_results:
                    if item["url"] not in seen and len(search_results) < 5:
                        seen.add(item["url"])
                        search_results.append({
                            "url": item["url"],
                            "date": "",
                            "title": item["title"],
                            "search_source": "Startpage (Google)"
                        })
                logger.info(f"[News] Google httpx ile toplam: {len(search_results)} sonuç")
            except Exception as e:
                logger.warning(f"[News] Google httpx hatası: {e}")

        # En güncel/önemli ilk 3 haberi seçelim
        final_results = search_results[:3]
        logger.info(f"Got {len(final_results)} dynamic URLs directly via Headless Browser for topic {topic}")
        return final_results
    
    async def search_social_urls(self, topic: str) -> List[Dict]:
        """LinkedIn ve Twitter/X'te DuckDuckGo uzerinden arama yapar.
        Birden fazla arama stratejisi ile sonuc bulmaya calisir.
        LinkedIn oncelikli. Filtreler kaldirildi - tum sonuclar gosterilir.
        """
        from config import config
        if not getattr(config, 'SOCIAL_SEARCH_ENABLED', True):
            logger.info("[Social] SOCIAL_SEARCH_ENABLED=false, atlaniyor")
            return []

        social_results = []
        seen = set()

        # LinkedIn icin coklu strateji: en spesifikten en genise
        platforms = [
            {
                "key": "linkedin",
                "name": "LinkedIn",
                "queries": [
                    f"{topic} site:linkedin.com",
                    f"{topic} linkedin",
                ],
                "icon": "LinkedIn",
            },
            {
                "key": "twitter",
                "name": "Twitter/X",
                "queries": [
                    f"{topic} site:x.com OR site:twitter.com",
                    f"{topic} twitter",
                ],
                "icon": "Twitter/X",
            },
        ]

        enabled_platforms = getattr(config, 'SOCIAL_PLATFORMS', ['linkedin', 'twitter'])
        max_per_platform = getattr(config, 'SOCIAL_MAX_RESULTS_PER_PLATFORM', 3)

        try:
            browser_config = get_browser_config()
            crawler_config = get_crawler_config()

            async with AsyncWebCrawler(config=browser_config) as crawler:
                for pinfo in platforms:
                    if pinfo["key"] not in enabled_platforms:
                        continue

                    count = 0
                    for query in pinfo["queries"]:
                        if count >= max_per_platform:
                            break

                        encoded = urllib.parse.quote_plus(query)
                        ddg_url = f"https://html.duckduckgo.com/html/?q={encoded}"

                        logger.info(f"[Social] === {pinfo['name']} ===")
                        logger.info(f"[Social] Sorgu: '{query}'")

                        try:
                            ddg_result = await crawler.arun(url=ddg_url, config=crawler_config)
                            if not (ddg_result.success and hasattr(ddg_result, 'html') and ddg_result.html):
                                logger.warning(f"[Social] DuckDuckGo basarisiz: {pinfo['name']}")
                                continue

                            logger.info(f"[Social] HTML: {len(ddg_result.html)} karakter")
                            soup = BeautifulSoup(ddg_result.html, 'html.parser')

                            all_links = soup.find_all('a', href=True)
                            logger.info(f"[Social] Sayfada {len(all_links)} link bulundu")

                            for a_tag in all_links:
                                if count >= max_per_platform:
                                    break

                                href = a_tag.get('href', '')
                                title = a_tag.get_text(strip=True)

                                if 'uddg=' in href:
                                    href = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])

                                if not href.startswith('http') or len(title) < 10:
                                    continue
                                if 'duckduckgo.com' in href:
                                    continue
                                if href in seen:
                                    continue

                                # Snippet'ten icerik cikar
                                content = title
                                date_str = ""
                                parent = a_tag.find_parent(class_='result') or a_tag.find_parent(class_='web-result')
                                if parent:
                                    snippet_el = parent.select_one('.result__snippet')
                                    if snippet_el:
                                        content = snippet_el.get_text(strip=True)
                                    # Arama snippet'inden tarih çıkar
                                    date_str = extract_date_from_search_result(a_tag, parent)
                                # Snippet'ten bulunamazsa URL'deki platform ID'den dene
                                if not date_str:
                                    date_str = extract_social_date("", href)

                                seen.add(href)
                                social_results.append({
                                    "url": href,
                                    "date": date_str,
                                    "title": f"{pinfo['icon']}: {title}",
                                    "platform": pinfo["key"],
                                    "source_type": "social_media",
                                    "content": content,
                                    "search_source": "DuckDuckGo",
                                })
                                count += 1
                                logger.info(f"[Social] [+] #{count}: '{title[:60]}' -> {href[:80]}")

                        except Exception as e:
                            logger.error(f"[Social] {pinfo['name']} hata: {e}")
                            continue

                        # Bu sorgu yeterli sonuc verdiyse digerine gecme
                        if count >= max_per_platform:
                            break

                    # Startpage (Google sonuçları) — her zaman ek kaynak olarak çalışır
                    # Sorgu formatı: "{konu} {tarih} {platform}" (Google'daki ile aynı)
                    try:
                        logger.info(f"[Social] {pinfo['name']}: Startpage (Google) araması başlıyor...")
                        google_results = await google_search_social_httpx(
                            topic, platform=pinfo["key"], max_results=max_per_platform,
                            recency_days=getattr(config, 'SOCIAL_SEARCH_RECENCY_DAYS', 7),
                        )
                        sp_count = 0
                        for g_item in google_results:
                            if g_item["url"] in seen:
                                continue
                            seen.add(g_item["url"])
                            social_results.append({
                                "url": g_item["url"],
                                "date": "",
                                "title": f"{pinfo['icon']}: {g_item['title']}",
                                "platform": pinfo["key"],
                                "source_type": "social_media",
                                "content": g_item.get("snippet", g_item["title"]),
                                "search_source": "Startpage (Google)",
                            })
                            sp_count += 1
                            logger.info(f"[Social] [Startpage] #{sp_count}: '{g_item['title'][:60]}' -> {g_item['url'][:80]}")
                        count += sp_count
                    except Exception as e:
                        logger.warning(f"[Social] Startpage hatası: {e}")

                    logger.info(f"[Social] {pinfo['name']} toplam: {count} (DDG + Startpage)")

        except Exception as e:
            logger.error(f"[Social] Genel hata: {e}", exc_info=True)

        logger.info(f"[Social] === Toplam sosyal medya sonucu: {len(social_results)} ===")
        return social_results

    async def fetch_live_data(self, topic: str) -> List[Dict]:
        """Main method to fetch live data for a topic using dynamic Web+News+Social searches."""
        logger.info(f"Fetching live data for topic: {topic}")

        all_articles = []

        # 1. Web haber araması (mevcut yaklaşım)
        search_urls = await self.search_news_urls(topic)
        logger.info(f"Found {len(search_urls)} web URLs to crawl for {topic}")

        if search_urls:
            # Crawl found URLs using Crawl4AI (or fallback)
            crawl_tasks = []
            for item in search_urls:
                crawl_tasks.append(
                    self.crawl_with_crawl4ai(
                        url=item["url"],
                        topic=topic,
                        expected_date=item["date"],
                        expected_title=item["title"]
                    )
                )

            crawl_results = await asyncio.gather(*crawl_tasks, return_exceptions=True)

            for idx, result in enumerate(crawl_results):
                if isinstance(result, list):
                    # Kaynak bilgisini ekle
                    source = search_urls[idx].get("search_source", "") if idx < len(search_urls) else ""
                    for article in result:
                        if source:
                            article["search_source"] = source
                    all_articles.extend(result)
                elif isinstance(result, Exception):
                    logger.error(f"Crawling task failed: {str(result)}")

        # 2. Sosyal medya araması (LinkedIn + Twitter/X) — mevcut yapı
        try:
            social_urls = await self.search_social_urls(topic)
            if social_urls:
                logger.info(f"Found {len(social_urls)} social media URLs for {topic}")
                social_articles = await self._enrich_social_results(social_urls, topic)
                all_articles.extend(social_articles)
                logger.info(f"Added {len(social_articles)} social media articles for {topic}")
        except Exception as e:
            logger.error(f"Social media search failed for {topic}: {e}")

        # 3. Tarih aralığı tabanlı ek sosyal medya araması (yeni modül)
        try:
            date_range_articles = await self.date_range_crawler.fetch_date_range_social(topic)
            if date_range_articles:
                # Aynı URL'leri tekrar eklememek için dedup yap
                existing_urls = {a.get('url', '') for a in all_articles}
                new_articles = [a for a in date_range_articles if a.get('url', '') not in existing_urls]
                all_articles.extend(new_articles)
                logger.info(f"[DateRange] Added {len(new_articles)} new social articles for {topic} "
                           f"({len(date_range_articles) - len(new_articles)} duplicates skipped)")
        except Exception as e:
            logger.error(f"Date range social search failed for {topic}: {e}")

        # Tarih filtresi: çok eski içerikleri ele
        all_articles = self._filter_by_recency(all_articles)

        web_count = len([a for a in all_articles if a.get('source_type') != 'social_media'])
        social_count = len([a for a in all_articles if a.get('source_type') == 'social_media'])
        logger.info(f"Found {len(all_articles)} articles for {topic} (web: {web_count}, social: {social_count})")
        return all_articles
    
    async def _enrich_social_results(self, social_urls: List[Dict], topic: str) -> List[Dict]:
        """Sosyal medya sonuçlarını zenginleştirir.
        Profil/company sayfası bulunduysa Google'da o profilin son postlarını arar.
        Bulunan post URL'lerinden og:description ile içerik çeker.
        """
        articles = []

        # Profil sayfası mı yoksa doğrudan post mu ayırt et
        profile_items = []
        post_items = []
        for item in social_urls:
            url = item.get("url", "")
            # LinkedIn company sayfası
            if re.search(r'linkedin\.com/company/[^/]+', url) and '/posts/' not in url and '/pulse/' not in url:
                profile_items.append(item)
            # Twitter/X profil veya hashtag sayfası (status/ içermeyen)
            elif re.search(r'(?:twitter\.com|x\.com)/', url) and '/status/' not in url:
                profile_items.append(item)
            else:
                post_items.append(item)

        logger.info(f"[Social] {len(profile_items)} profil, {len(post_items)} dogrudan post")

        try:
            browser_config = get_browser_config()
            crawler_config = get_crawler_config()

            async with AsyncWebCrawler(config=browser_config) as crawler:
                # 1. Profil sayfaları için Google'da son postları ara
                for item in profile_items:
                    try:
                        posts = await self._search_profile_posts(
                            crawler, crawler_config, item, topic
                        )
                        articles.extend(posts)
                    except Exception as e:
                        logger.error(f"[Social] Profil post arama hatası ({item.get('url')}): {e}")
                        articles.append(self._social_fallback(item, topic))

                # 2. Doğrudan post URL'lerinden içerik çek
                for item in post_items:
                    try:
                        enriched = await self._fetch_post_content(
                            crawler, crawler_config, item, topic
                        )
                        articles.append(enriched)
                    except Exception as e:
                        logger.error(f"[Social] Post içerik hatası ({item.get('url')}): {e}")
                        articles.append(self._social_fallback(item, topic))

        except Exception as e:
            logger.error(f"[Social] Enrichment crawler hatası: {e}")
            # Tüm sonuçları fallback olarak ekle
            for item in profile_items + post_items:
                articles.append(self._social_fallback(item, topic))

        return articles

    async def _search_profile_posts(
        self, crawler, crawler_config, item: Dict, topic: str
    ) -> List[Dict]:
        """Profil sayfası bulunduğunda, birden fazla strateji ile son postları arar:
        1. Profil sayfasını doğrudan crawl edip post linklerini çeker
        2. Google'da indexed post URL'lerini arar
        3. DuckDuckGo'da indexed post URL'lerini arar
        """
        url = item.get("url", "")
        platform = item.get("platform", "unknown")

        # Profil adını çıkar
        profile_name = ""
        profile_slug = ""
        if 'linkedin.com/company/' in url:
            match = re.search(r'linkedin\.com/company/([^/]+)', url)
            if match:
                profile_slug = match.group(1)
                profile_name = profile_slug.replace('-', ' ')
        elif re.search(r'(?:twitter\.com|x\.com)/(\w+)', url):
            match = re.search(r'(?:twitter\.com|x\.com)/(\w+)', url)
            if match:
                profile_slug = match.group(1)
                profile_name = profile_slug

        if not profile_name:
            return [self._social_fallback(item, topic)]

        post_urls = []
        seen = set()

        # --- Strateji 1: Profil sayfasını doğrudan crawl et ---
        try:
            logger.info(f"[Social] Profil sayfası crawl ediliyor: {url}")
            profile_result = await crawler.arun(url=url, config=crawler_config)
            if profile_result.success and hasattr(profile_result, 'html') and profile_result.html:
                profile_soup = BeautifulSoup(profile_result.html, 'html.parser')
                # LinkedIn profil sayfasında post linkleri bul
                for a_tag in profile_soup.find_all('a', href=True):
                    href = a_tag['href']
                    if href.startswith('/'):
                        href = f"https://www.linkedin.com{href}"
                    is_post = False
                    if platform == 'linkedin' and re.search(r'linkedin\.com/(posts|feed/update)/', href):
                        is_post = True
                    elif platform == 'twitter' and re.search(r'(twitter\.com|x\.com)/\w+/status/\d+', href):
                        is_post = True
                    if is_post and href not in seen:
                        title = a_tag.get_text(strip=True)
                        if len(title) < 10:
                            title = f"{profile_name} paylasimi"
                        seen.add(href)
                        post_urls.append({"url": href, "title": title})
                        if len(post_urls) >= 3:
                            break
                if post_urls:
                    logger.info(f"[Social] Profil sayfasından {len(post_urls)} post linki bulundu")
        except Exception as e:
            logger.warning(f"[Social] Profil crawl hatası: {e}")

        # --- Strateji 2: Google'da post ara ---
        if len(post_urls) < 3:
            try:
                if platform == 'linkedin':
                    search_query = f'"{profile_name}" site:linkedin.com/posts/ OR site:linkedin.com/feed/update'
                else:
                    search_query = f'from:{profile_name} site:x.com OR site:twitter.com'

                encoded_q = urllib.parse.quote_plus(search_query)
                google_url = f"https://www.google.com/search?q={encoded_q}&tbs=qdr:m&hl=tr&gl=TR&num=5"

                logger.info(f"[Social] Google post araması: {search_query}")
                result = await crawler.arun(url=google_url, config=crawler_config)
                if result.success and hasattr(result, 'html') and result.html:
                    self._extract_post_urls_from_search(
                        result.html, platform, post_urls, seen, max_count=3
                    )
            except Exception as e:
                logger.warning(f"[Social] Google post araması hatası: {e}")

        # --- Strateji 3: DuckDuckGo'da post ara ---
        if len(post_urls) < 3:
            try:
                if platform == 'linkedin':
                    ddg_query = f'{topic} site:linkedin.com/posts/{profile_slug}'
                else:
                    ddg_query = f'{topic} from:{profile_slug} site:x.com'

                encoded_q = urllib.parse.quote_plus(ddg_query)
                ddg_url = f"https://html.duckduckgo.com/html/?q={encoded_q}"

                logger.info(f"[Social] DuckDuckGo post araması: {ddg_query}")
                ddg_result = await crawler.arun(url=ddg_url, config=crawler_config)
                if ddg_result.success and hasattr(ddg_result, 'html') and ddg_result.html:
                    ddg_soup = BeautifulSoup(ddg_result.html, 'html.parser')
                    for a_tag in ddg_soup.find_all('a', href=True):
                        href = a_tag.get('href', '')
                        if 'uddg=' in href:
                            href = urllib.parse.unquote(href.split('uddg=')[1].split('&')[0])
                        is_post = False
                        if platform == 'linkedin' and re.search(r'linkedin\.com/(posts|feed/update)/', href):
                            is_post = True
                        elif platform == 'twitter' and re.search(r'(twitter\.com|x\.com)/\w+/status/\d+', href):
                            is_post = True
                        if is_post and href not in seen:
                            title = a_tag.get_text(strip=True)
                            if len(title) < 10:
                                title = f"{profile_name} paylasimi"
                            seen.add(href)
                            post_urls.append({"url": href, "title": title})
                            if len(post_urls) >= 3:
                                break
                    if post_urls:
                        logger.info(f"[Social] DuckDuckGo'dan ek post bulundu, toplam: {len(post_urls)}")
            except Exception as e:
                logger.warning(f"[Social] DuckDuckGo post araması hatası: {e}")

        # --- Strateji 4: Genel konu araması (Google News + LinkedIn) ---
        if len(post_urls) < 1:
            try:
                if platform == 'linkedin':
                    fallback_query = f'{topic} site:linkedin.com/posts'
                else:
                    fallback_query = f'{topic} site:x.com'
                encoded_q = urllib.parse.quote_plus(fallback_query)
                google_url = f"https://www.google.com/search?q={encoded_q}&tbs=qdr:w&hl=tr&gl=TR&num=5"
                logger.info(f"[Social] Genel konu post araması: {fallback_query}")
                result = await crawler.arun(url=google_url, config=crawler_config)
                if result.success and hasattr(result, 'html') and result.html:
                    self._extract_post_urls_from_search(
                        result.html, platform, post_urls, seen, max_count=3
                    )
            except Exception as e:
                logger.warning(f"[Social] Genel konu araması hatası: {e}")

        if not post_urls:
            logger.info(f"[Social] Hiçbir stratejide post bulunamadı: {profile_name}")
            return [self._social_fallback(item, topic)]

        logger.info(f"[Social] Toplam {len(post_urls)} post URL bulundu: {profile_name}")

        # Bulunan post URL'lerinden içerik çek
        posts = []
        for post_info in post_urls:
            try:
                post_result = await crawler.arun(url=post_info["url"], config=crawler_config)
                if post_result.success and hasattr(post_result, 'html') and post_result.html:
                    post_content = self._extract_meta_content(post_result.html)
                    pub_date = extract_social_date(post_result.html, post_info["url"]) or mark_unknown_date()
                    if post_content and len(post_content) > 30:
                        posts.append({
                            "title": post_info["title"],
                            "summary": post_content,
                            "url": post_info["url"],
                            "published_date": pub_date,
                            "content": post_content,
                            "topic": topic,
                            "source_type": "social_media",
                            "platform": platform,
                            "search_source": item.get("search_source", ""),
                        })
                        logger.info(f"[Social] Post içeriği çekildi ({pub_date}): {post_info['title'][:60]}")
                        continue

                # Meta tag'den çekemediyse URL ID'den tarih dene
                id_date = extract_social_date("", post_info["url"]) or mark_unknown_date()
                posts.append({
                    "title": post_info["title"],
                    "summary": post_info["title"],
                    "url": post_info["url"],
                    "published_date": id_date,
                    "content": post_info["title"],
                    "topic": topic,
                    "source_type": "social_media",
                    "platform": platform,
                    "search_source": item.get("search_source", ""),
                })
            except Exception as e:
                logger.error(f"[Social] Post içerik çekme hatası: {e}")

        return posts if posts else [self._social_fallback(item, topic)]

    def _extract_post_urls_from_search(
        self, html: str, platform: str, post_urls: list, seen: set, max_count: int = 3
    ):
        """Google/arama motoru HTML sonuçlarından post URL'lerini çıkarır."""
        soup = BeautifulSoup(html, 'html.parser')
        for a_tag in soup.find_all('a', href=True):
            if len(post_urls) >= max_count:
                break
            href = a_tag['href']
            if '/url?q=' in href:
                href = href.split('/url?q=')[1].split('&')[0]
                href = urllib.parse.unquote(href)

            is_post = False
            if platform == 'linkedin' and re.search(r'linkedin\.com/(posts|feed/update)/', href):
                is_post = True
            elif platform == 'twitter' and re.search(r'(twitter\.com|x\.com)/\w+/status/\d+', href):
                is_post = True

            if is_post and href not in seen:
                title = a_tag.get_text(strip=True)
                if len(title) > 15:
                    seen.add(href)
                    post_urls.append({"url": href, "title": title})
                    logger.info(f"[Social] Arama'dan post bulundu: {title[:60]}")

    async def _fetch_post_content(
        self, crawler, crawler_config, item: Dict, topic: str
    ) -> Dict:
        """Doğrudan bir post URL'sinden içerik çeker (og:description ve markdown)."""
        url = item.get("url", "")
        platform = item.get("platform", "unknown")

        try:
            result = await crawler.arun(url=url, config=crawler_config)
            if result.success and hasattr(result, 'html') and result.html:
                content = self._extract_meta_content(result.html)
                if content and len(content) > 30:
                    pub_date = extract_social_date(result.html, url) or item.get("date", "") or mark_unknown_date()
                    return {
                        "title": item.get("title", topic),
                        "summary": content,
                        "url": url,
                        "published_date": pub_date,
                        "content": content,
                        "topic": topic,
                        "source_type": "social_media",
                        "platform": platform,
                        "search_source": item.get("search_source", ""),
                    }
        except Exception as e:
            logger.error(f"[Social] Post fetch hatası ({url}): {e}")

        return self._social_fallback(item, topic)

    def _extract_meta_content(self, html: str) -> str:
        """HTML'den og:description, twitter:description veya description meta tag'ini çeker.
        LinkedIn ve Twitter login olmadan bile bu meta tag'lerde post içeriğini verir.
        """
        soup = BeautifulSoup(html, 'html.parser')

        # Öncelik sırasıyla meta tag'lerden içerik çek
        for prop in ['og:description', 'twitter:description', 'description']:
            meta = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if meta and meta.get('content') and len(meta['content'].strip()) > 30:
                return meta['content'].strip()[:3000]

        # og:title da faydalı olabilir
        og_title = soup.find('meta', property='og:title')
        title_content = og_title['content'].strip() if og_title and og_title.get('content') else ""

        # Markdown içerikten de deneyebiliriz
        return title_content

    def _extract_social_date(self, html: str, url: str) -> str:
        """Sosyal medya postunun gerçek tarihini çıkarır. date_utils modülüne yönlendirir."""
        return extract_social_date(html, url)

    def _filter_by_recency(self, articles: List[Dict], max_age_days: int = 30) -> List[Dict]:
        """Tarih bilgisi olan makaleleri kontrol eder, çok eski olanları eler.
        Web haberleri: Tarih bilgisi olmayan veya parse edilemeyen makaleler korunur.
        Sosyal medya: Tarih bilgisi olmayan makaleler ELENIR (tarihsiz sosyal medya güvenilmez).
        """
        cutoff = datetime.now() - timedelta(days=max_age_days)
        filtered = []
        for article in articles:
            pub_date_str = article.get("published_date", "")
            is_social = article.get("source_type") == "social_media"

            if not pub_date_str:
                if is_social:
                    # Sosyal medya tarihsiz ise dahil etme
                    logger.info(f"[Recency] Tarihsiz sosyal medya elendi: {article.get('title', '')[:60]}")
                    continue
                else:
                    # Web haberleri tarihsiz olabilir
                    filtered.append(article)
                    continue
            try:
                pub_date = dateutil.parser.parse(pub_date_str, fuzzy=True)
                if pub_date.tzinfo:
                    pub_date = pub_date.replace(tzinfo=None)
                if pub_date >= cutoff:
                    filtered.append(article)
                else:
                    logger.info(f"[Recency] Eski icerik elendi: {article.get('title', '')[:60]} ({pub_date_str})")
            except Exception:
                if is_social:
                    logger.info(f"[Recency] Tarih parse edilemedi, sosyal medya elendi: {article.get('title', '')[:60]}")
                    continue
                filtered.append(article)
        if len(filtered) < len(articles):
            logger.info(f"[Recency] {len(articles) - len(filtered)} eski icerik elendi, kalan: {len(filtered)}")
        return filtered

    def _social_fallback(self, item: Dict, topic: str) -> Dict:
        """Sosyal medya sonucunu sadece başlık bilgisiyle article olarak döndürür."""
        url = item.get("url", "")
        fallback_date = item.get("date", "") or extract_social_date("", url) or mark_unknown_date()
        return {
            "title": item.get("title", topic),
            "summary": item.get("content", item.get("title", "")),
            "url": url,
            "published_date": fallback_date,
            "content": item.get("content", item.get("title", "")),
            "topic": topic,
            "source_type": "social_media",
            "platform": item.get("platform", "unknown"),
            "search_source": item.get("search_source", ""),
        }

    def _parse_llm_extraction(self, extracted_content: str, source_url: str, topic: str) -> List[Dict]:
        """Parse LLM extracted content into article format"""
        try:
            articles = []
            
            # Convert to string if not already
            content_str = str(extracted_content)
            
            # Try to extract from text directly - most reliable approach
            articles_data = self._extract_from_text(content_str)
            
            for item in articles_data:
                if isinstance(item, dict) and item.get("title"):
                    article = {
                        "title": item.get("title", "").strip(),
                        "summary": item.get("summary", "").strip(),
                        "url": item.get("url", source_url),
                        "published_date": datetime.now().strftime("%Y-%m-%d"),
                        "content": item.get("summary", "").strip(),
                        "topic": topic
                    }
                    articles.append(article)
            
            return articles
            
        except Exception as e:
            logger.error(f"Error parsing LLM extraction: {str(e)}")
            return []
    
    def _extract_from_text(self, text: str) -> List[Dict]:
        """Extract articles from plain text LLM response"""
        articles = []
        
        # Split text into potential article sections
        import re
        
        # Look for numbered lists, bullet points, or clear separators
        sections = re.split(r'\n\s*(?:\d+[.):]|[-*•]|Article|Title)', text)
        
        for section in sections:
            section = section.strip()
            if len(section) < 30:  # Skip very short sections
                continue
                
            lines = [line.strip() for line in section.split('\n') if line.strip()]
            if not lines:
                continue
                
            # Extract title (first meaningful line)
            title = lines[0]
            title = re.sub(r'^[:\-\s]+', '', title).strip()  # Clean prefixes
            
            # Extract summary (remaining lines)
            summary_lines = lines[1:] if len(lines) > 1 else []
            summary = ' '.join(summary_lines)[:400] if summary_lines else title[:200]
            
            # Basic validation - must look like a news title
            if (title and len(title) > 15 and len(title) < 200 and 
                not title.lower().startswith(('http', 'www', 'click', 'read more'))):
                articles.append({
                    "title": title,
                    "summary": summary
                })
                
                if len(articles) >= 10:  # Limit to prevent too many
                    break
        
        return articles
    
    def _extract_articles_from_markdown(self, markdown_content: str, source_url: str, topic: str) -> List[Dict]:
        """Extract articles from markdown content"""
        try:
            articles = []
            lines = markdown_content.split('\n')
            
            current_title = None
            current_content = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                # Look for headers (titles)
                if line.startswith('#'):
                    # Save previous article if exists
                    if current_title and len(current_title) > 15:
                        summary = ' '.join(current_content)[:300]
                        articles.append({
                            "title": current_title,
                            "summary": summary,
                            "url": source_url,
                            "published_date": datetime.now().strftime("%Y-%m-%d"),
                            "content": summary,
                            "topic": topic
                        })
                        
                        if len(articles) >= self.max_articles:
                            break
                    
                    # Start new article
                    current_title = line.lstrip('#').strip()
                    current_content = []
                    
                elif current_title:
                    # Add to current article content
                    if not line.startswith('[') and not line.startswith('http'):
                        current_content.append(line)
            
            # Don't forget the last article
            if current_title and len(current_title) > 15:
                summary = ' '.join(current_content)[:300]
                articles.append({
                    "title": current_title,
                    "summary": summary,
                    "url": source_url,
                    "published_date": datetime.now().strftime("%Y-%m-%d"),
                    "content": summary,
                    "topic": topic
                })
            
            return articles[:self.max_articles]
            
        except Exception as e:
            logger.error(f"Error extracting articles from markdown: {str(e)}")
            return []
    
    def _extract_articles_from_html(self, html_content: str, source_url: str, topic: str) -> List[Dict]:
        """Extract articles from HTML content using BeautifulSoup"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            articles = []
            
            # Common selectors for article elements
            article_selectors = [
                'article',
                '.post',
                '.article',
                '.story',
                '.entry',
                '[data-testid="post"]',
                '.c-entry-box--compact',
                '.post-item',
                '.article-item',
                '.news-item',
                'h2',
                'h3'
            ]
            
            for selector in article_selectors:
                elements = soup.select(selector)[:self.max_articles * 2]  # Get more to filter later
                
                for element in elements:
                    title_elem = element.find(['h1', 'h2', 'h3', 'h4']) or element.find(class_=lambda x: x and 'title' in x.lower() if x else False)
                    link_elem = element.find('a', href=True) or element.find(class_=lambda x: x and 'link' in x.lower() if x else False)
                    
                    if title_elem and title_elem.get_text(strip=True):
                        title = title_elem.get_text(strip=True)
                        
                        # Get article URL
                        article_url = source_url
                        if link_elem and link_elem.get('href'):
                            href = link_elem.get('href')
                            if href.startswith('http'):
                                article_url = href
                            elif href.startswith('/'):
                                from urllib.parse import urljoin
                                article_url = urljoin(source_url, href)
                        
                        # Get summary/content
                        summary = ""
                        content_elem = element.find(['p', 'div'], class_=lambda x: x and any(word in x.lower() for word in ['summary', 'excerpt', 'description']) if x else False)
                        if content_elem:
                            summary = content_elem.get_text(strip=True)[:300]
                        else:
                            # Fallback: get first paragraph
                            p_elem = element.find('p')
                            if p_elem:
                                summary = p_elem.get_text(strip=True)[:300]
                        
                        article = {
                            "title": title,
                            "summary": summary,
                            "url": article_url,
                            "published_date": datetime.now().strftime("%Y-%m-%d"),
                            "content": summary,
                            "topic": topic
                        }
                        articles.append(article)
                        
                        if len(articles) >= self.max_articles:
                            break
                
                if articles:
                    break  # Found articles with this selector
            
            return articles
            
        except Exception as e:
            logger.error(f"Error extracting articles from HTML: {str(e)}")
            return []
    
    def _filter_articles(self, articles: List[Dict], topic: str) -> List[Dict]:
        """Filter articles - just verify they have content since search engines did the topic relevance"""
        filtered = []
        for article in articles:
            content = article.get("summary", "") or article.get("content", "")
            if content and len(content) > 50:
                article["topic"] = topic
                filtered.append(article)
        return filtered[:self.max_articles]
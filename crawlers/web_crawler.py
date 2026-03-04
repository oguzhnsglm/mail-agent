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
            browser_config = BrowserConfig(headless=True)
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                word_count_threshold=10,
                page_timeout=60000,
                magic=True
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
            browser_config = BrowserConfig(headless=True)
            crawler_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS, 
                page_timeout=30000,
                magic=True # Bot korumalarını asmak ve JS'i daha iyi parse etmek için çok önemli
            )
            
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
                                    seen.add(href)
                                    search_results.append({
                                        "url": href,
                                        "date": datetime.now().strftime("%Y-%m-%d"),
                                        "title": title
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
                                    seen.add(resolved_url)
                                    search_results.append({
                                        "url": resolved_url,
                                        "date": datetime.now().strftime("%Y-%m-%d"),
                                        "title": title
                                    })
                                    
        except Exception as e:
            logger.error(f"Crawl4AI search engine scrape failed: {str(e)}")

        # En güncel/önemli ilk 3 haberi seçelim
        final_results = search_results[:3]
        logger.info(f"Got {len(final_results)} dynamic URLs directly via Headless Browser for topic {topic}")
        return final_results
    
    async def fetch_live_data(self, topic: str) -> List[Dict]:
        """Main method to fetch live data for a topic using dynamic Web+News searches."""
        logger.info(f"Fetching live data for topic: {topic}")
        
        all_articles = []
        
        # Get dynamic URLs based on search
        search_urls = await self.search_news_urls(topic)
        logger.info(f"Found {len(search_urls)} URLs to crawl for {topic}")
        
        if not search_urls:
            return []
            
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
        
        # Execute crawling tasks concurrently
        crawl_results = await asyncio.gather(*crawl_tasks, return_exceptions=True)
        
        for result in crawl_results:
            if isinstance(result, list):
                all_articles.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Crawling task failed: {str(result)}")
        
        # Filter articles by topic relevance and recency
        filtered_articles = self._filter_articles(all_articles, topic)
        
        logger.info(f"Found {len(filtered_articles)} relevant articles for {topic}")
        return filtered_articles
    
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
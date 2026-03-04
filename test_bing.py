import asyncio
from crawl4ai import AsyncWebCrawler
from bs4 import BeautifulSoup
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

async def run():
    async with AsyncWebCrawler() as c:
        res = await c.arun('https://www.bing.com/news/search?q=FNSS&qft=interval%3d%227%22', magic=True)
        html = res.html if hasattr(res, 'html') else ''
        soup = BeautifulSoup(html, 'html.parser')
        links = soup.find_all('a', href=True)
        
        valid_links = []
        for l in links:
            href = l.get('href', '')
            title = l.get_text(strip=True)
            valid_links.append((href, title))
                 
        print(f'Total links found: {len(valid_links)}')
        for i, (h, t) in enumerate(valid_links[:30]):
            print(f'{i} - Title: {t}')
            print(f'    Link: {h}')

asyncio.run(run())

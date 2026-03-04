import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from crawlers.web_crawler import WebCrawler

@pytest.fixture
def crawler():
    return WebCrawler()

@pytest.fixture
def sample_articles():
    return [
        {
            "title": "AI Breakthrough in Machine Learning",
            "summary": "New developments in AI technology",
            "url": "https://example.com/ai-news",
            "published_date": "2024-01-01",
            "content": "Sample content about AI",
            "relevance_score": 3
        },
        {
            "title": "Tech Industry Updates",
            "summary": "Latest tech industry news",
            "url": "https://example.com/tech-news",
            "published_date": "2024-01-01",
            "content": "Sample tech content",
            "relevance_score": 1
        }
    ]

class TestWebCrawler:
    
    def test_crawler_initialization(self, crawler):
        """Test crawler initialization"""
        assert crawler.timeout > 0
        assert crawler.max_articles > 0
    
    def test_filter_articles(self, crawler, sample_articles):
        """Test article filtering by relevance"""
        topic = "AI news"
        filtered = crawler._filter_articles(sample_articles, topic)
        
        # Should return articles sorted by relevance
        assert len(filtered) <= crawler.max_articles
        assert all(article.get("topic") == topic for article in filtered)
        
        # Should be sorted by relevance score (descending)
        if len(filtered) > 1:
            assert filtered[0]["relevance_score"] >= filtered[1]["relevance_score"]
    
    @patch('requests.get')
    def test_extract_content_from_url(self, mock_get, crawler):
        """Test content extraction from URL"""
        # Mock response
        mock_response = Mock()
        mock_response.content = b"<html><body><h1>Test Title</h1><p>Test content</p></body></html>"
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        content = crawler._extract_content_from_url("https://example.com")
        
        assert "Test Title" in content
        assert "Test content" in content
        mock_get.assert_called_once()
    
    @patch('requests.get')
    def test_extract_content_error_handling(self, mock_get, crawler):
        """Test error handling in content extraction"""
        mock_get.side_effect = Exception("Network error")
        
        content = crawler._extract_content_from_url("https://example.com")
        
        assert content == ""
    
    @patch('feedparser.parse')
    def test_crawl_rss_feeds(self, mock_parse, crawler):
        """Test RSS feed crawling"""
        # Mock feed data
        mock_feed = Mock()
        mock_feed.entries = [
            Mock(
                title="Test Article",
                summary="Test summary",
                link="https://example.com/article",
                published="2024-01-01"
            )
        ]
        mock_parse.return_value = mock_feed
        
        with patch.object(crawler, '_extract_content_from_url', return_value="Test content"):
            articles = crawler.crawl_rss_feeds(["https://example.com/rss"], "AI news")
        
        assert len(articles) > 0
        assert articles[0]["title"] == "Test Article"
        assert articles[0]["topic"] == "AI news"
    
    @pytest.mark.asyncio
    async def test_fetch_live_data(self, crawler):
        """Test live data fetching"""
        with patch.object(crawler, 'crawl_with_crawl4ai', new_callable=AsyncMock) as mock_crawl:
            mock_crawl.return_value = [
                {
                    "title": "Test AI Article",
                    "summary": "AI related content",
                    "url": "https://example.com",
                    "published_date": "2024-01-01",
                    "content": "AI content"
                }
            ]
            
            articles = await crawler.fetch_live_data("AI news")
            
            assert len(articles) > 0
            assert all("AI" in article.get("title", "") or "AI" in article.get("summary", "") 
                      for article in articles)

if __name__ == "__main__":
    pytest.main([__file__])
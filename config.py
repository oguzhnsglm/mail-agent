import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

class Config:
    # OpenRouter Configuration
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    
    # Gmail SMTP Configuration
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
    
    # Newsletter Configuration
    SENDER_EMAIL = os.getenv("SENDER_EMAIL")
    RECIPIENT_EMAILS = os.getenv("RECIPIENT_EMAILS", "").split(",")
    NEWSLETTER_TITLE = os.getenv("NEWSLETTER_TITLE", "Günlük Haber Bülteni")
    
    # Scheduling Configuration
    SCHEDULE_TIMES = os.getenv("SCHEDULE_TIMES", "09:00").split(",")
    TIMEZONE = os.getenv("TIMEZONE", "Europe/Istanbul")
    
    # Crawling Configuration
    MAX_ARTICLES_PER_TOPIC = int(os.getenv("MAX_ARTICLES_PER_TOPIC", "5"))
    CRAWL_TIMEOUT = int(os.getenv("CRAWL_TIMEOUT", "30"))
    
    # LLM Configuration
    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-4-turbo-preview")
    TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "3000"))
    
    # Topics to crawl  –  .env'den virgülle ayırarak istediğiniz konuları yazabilirsiniz
    # Örn: TOPICS="FNSS savunma,Türkiye ekonomi,yapay zeka"
    _topics_env = os.getenv("TOPICS", "")
    TOPICS: List[str] = (
        [t.strip() for t in _topics_env.split(",") if t.strip()]
        if _topics_env
        else [
            "FNSS Savunma Sistemleri",
            "Türkiye savunma sanayii",
        ]
    )
    
    # News sources (fallback - empty, search handles everything)
    NEWS_SOURCES = []

    # Social Media Search Configuration
    # Sosyal medya aramasını açıp kapatmak için: SOCIAL_SEARCH_ENABLED=true/false
    SOCIAL_SEARCH_ENABLED = os.getenv("SOCIAL_SEARCH_ENABLED", "true").lower() in ("true", "1", "yes")
    
    # Hangi platformlarda arama yapılacak (virgülle ayırarak): twitter,linkedin
    _social_platforms_env = os.getenv("SOCIAL_PLATFORMS", "twitter,linkedin")
    SOCIAL_PLATFORMS: List[str] = [p.strip() for p in _social_platforms_env.split(",") if p.strip()]
    
    # Sosyal medya sonuçlarının zaman aralığı (gün cinsinden, Google qdr parametresi)
    SOCIAL_SEARCH_RECENCY_DAYS = int(os.getenv("SOCIAL_SEARCH_RECENCY_DAYS", "2"))
    
    # Platform başına maksimum sonuç sayısı
    SOCIAL_MAX_RESULTS_PER_PLATFORM = int(os.getenv("SOCIAL_MAX_RESULTS_PER_PLATFORM", "3"))

config = Config()
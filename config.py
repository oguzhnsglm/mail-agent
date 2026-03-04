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

config = Config()
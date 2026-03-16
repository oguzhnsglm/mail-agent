import logging
import os
from datetime import datetime

def setup_logger(name: str = "newsletter_agent") -> logging.Logger:
    """Setup logger with file and console handlers"""
    
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Create formatters
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # File handler — UTF-8 encoding for emoji/unicode support
    log_filename = f"logs/newsletter_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(log_filename, encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Console handler — Windows cp1254 Unicode hatalarını önlemek için errors='replace'
    import sys
    console_stream = open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger
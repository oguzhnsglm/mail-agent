#!/usr/bin/env python3
"""
24/7 AI Newsletter Agent
Main entry point for the newsletter generation system
"""

import argparse
import sys
import asyncio
from datetime import datetime

# Windows icin Playwright asyncio fix:
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from scheduler.newsletter_scheduler import NewsletterScheduler
from utils.logger import setup_logger
from config import config

logger = setup_logger(__name__)

def print_banner():
    """Print application banner"""
    banner = """
    ╔══════════════════════════════════════════════════════════════╗
    ║                    24/7 AI Newsletter Agent                  ║
    ║                                                              ║
    ║  Automated AI-powered newsletter generation and delivery     ║
    ║  Built with Crawl4AI, LangGraph, OpenRouter & Gmail API      ║
    ╚══════════════════════════════════════════════════════════════╝
    """
    print(banner)

def validate_config():
    """Validate configuration before starting"""
    errors = []
    
    if not config.OPENROUTER_API_KEY:
        errors.append("OpenRouter API key not configured")
    
    if not config.SENDER_EMAIL:
        errors.append("Sender email not configured")
    
    if not config.RECIPIENT_EMAILS or not config.RECIPIENT_EMAILS[0]:
        errors.append("Recipient emails not configured")
    
    if errors:
        logger.error("Configuration errors found:")
        for error in errors:
            logger.error(f"  - {error}")
        logger.error("Please check your .env file and configuration")
        return False
    
    return True

def main():
    """Main application entry point"""
    parser = argparse.ArgumentParser(description="24/7 AI Newsletter Agent")
    parser.add_argument(
        "--mode", 
        choices=["schedule", "once", "test"],
        default="schedule",
        help="Run mode: schedule (continuous), once (single run), or test (dry run)"
    )
    parser.add_argument(
        "--config-check",
        action="store_true",
        help="Check configuration and exit"
    )
    
    args = parser.parse_args()
    
    print_banner()
    logger.info(f"Starting AI Newsletter Agent in {args.mode} mode")
    logger.info(f"Configuration: {len(config.TOPICS)} topics, {len(config.RECIPIENT_EMAILS)} recipients")
    
    # Validate configuration
    if not validate_config():
        sys.exit(1)
    
    if args.config_check:
        logger.info("Configuration check passed!")
        logger.info(f"Topics: {', '.join(config.TOPICS)}")
        logger.info(f"Schedule times: {', '.join(config.SCHEDULE_TIMES)}")
        logger.info(f"Recipients: {len(config.RECIPIENT_EMAILS)} configured")
        return
    
    # Initialize scheduler
    scheduler = NewsletterScheduler()
    
    try:
        if args.mode == "once":
            logger.info("Running newsletter generation once...")
            scheduler.run_once()
            
        elif args.mode == "test":
            logger.info("Running in test mode (dry run)...")
            # TODO: Implement test mode with mock data
            logger.info("Test mode not yet implemented")
            
        elif args.mode == "schedule":
            logger.info("Starting scheduled newsletter service...")
            scheduler.start_scheduler()
            
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Application error: {str(e)}")
        sys.exit(1)
    finally:
        if hasattr(scheduler, 'stop_scheduler'):
            scheduler.stop_scheduler()
        logger.info("Application shutdown complete")

if __name__ == "__main__":
    main()
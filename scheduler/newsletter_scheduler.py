import schedule
import time
import asyncio
from datetime import datetime
from typing import List

from crawlers.web_crawler import WebCrawler
from agents.newsletter_agents import NewsletterAgents
from email_service.gmail_client import GmailClient
from utils.logger import setup_logger
from config import config

logger = setup_logger(__name__)

class NewsletterScheduler:
    def __init__(self):
        self.crawler = WebCrawler()
        self.agents = NewsletterAgents()
        self.gmail_client = GmailClient()
        self.is_running = False
    
    async def newsletter_run(self):
        """Main newsletter generation workflow"""
        logger.info("=" * 50)
        logger.info("Starting newsletter generation process...")
        logger.info("=" * 50)
        
        try:
            # Step 1: Crawl data for all topics
            logger.info("Step 1: Crawling data for all topics...")
            all_articles = []
            
            for topic in config.TOPICS:
                logger.info(f"Crawling data for topic: {topic}")
                articles = await self.crawler.fetch_live_data(topic)
                all_articles.extend(articles)
                
                # Add small delay between topics to be respectful to servers
                await asyncio.sleep(2)
            
            if not all_articles:
                logger.warning("No articles found. Skipping newsletter generation.")
                return

            web_count = len([a for a in all_articles if a.get('source_type') != 'social_media'])
            social_count = len([a for a in all_articles if a.get('source_type') == 'social_media'])
            logger.info(f"Total articles collected: {len(all_articles)} (web: {web_count}, social: {social_count})")
            
            # Step 2: Process articles through agent workflow
            logger.info("Step 2: Processing articles through AI agents...")
            agent_results = self.agents.process_articles(all_articles)
            
            final_newsletter = agent_results.get("final_newsletter", "")
            logger.info(f"Generated newsletter content length: {len(final_newsletter)}")
            if final_newsletter:
                logger.info(f"Newsletter preview: {final_newsletter[:200]}...")
            
            if not final_newsletter:
                logger.error("Failed to generate newsletter content")
                return
            
            # Step 3: Extract subject line and send email
            logger.info("Step 3: Sending newsletter via email...")
            subject = self._extract_subject_line(final_newsletter)
            
            success = self.gmail_client.send_newsletter(final_newsletter, subject)
            
            if success:
                logger.info("Newsletter generation and delivery completed successfully!")
            else:
                logger.error("Failed to send newsletter")
                
        except Exception as e:
            logger.error(f"Error in newsletter generation: {str(e)}")
            raise
    
    def _extract_subject_line(self, newsletter_content: str) -> str:
        """Extract subject line from newsletter content or generate default"""
        lines = newsletter_content.split('\n')
        
        # Look for subject line suggestion in the content
        for line in lines:
            if 'subject' in line.lower() and ('line' in line.lower() or ':' in line):
                # Extract the subject line
                if ':' in line:
                    subject = line.split(':', 1)[1].strip()
                    # Remove quotes if present
                    subject = subject.strip('"').strip("'")
                    if subject:
                        return subject
        
        # Default subject line with Turkish date
        _tr_months = {
            1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan",
            5: "Mayıs", 6: "Haziran", 7: "Temmuz", 8: "Ağustos",
            9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
        }
        now = datetime.now()
        date_str = f"{now.day} {_tr_months.get(now.month, '')} {now.year}"
        return f"{config.NEWSLETTER_TITLE} - {date_str}"
    
    def schedule_newsletter(self):
        """Schedule newsletter generation at configured times"""
        logger.info("Setting up newsletter schedule...")
        
        for time_str in config.SCHEDULE_TIMES:
            time_str = time_str.strip()
            logger.info(f"Scheduling newsletter for {time_str}")
            schedule.every().day.at(time_str).do(self._run_async_newsletter)
        
        logger.info(f"Newsletter scheduled for times: {', '.join(config.SCHEDULE_TIMES)}")
    
    def _run_async_newsletter(self):
        """Wrapper to run async newsletter function"""
        try:
            asyncio.run(self.newsletter_run())
        except Exception as e:
            logger.error(f"Error running scheduled newsletter: {str(e)}")
    
    def run_once(self):
        """Run newsletter generation once (for testing)"""
        logger.info("Running newsletter generation once...")
        asyncio.run(self.newsletter_run())
    
    def start_scheduler(self):
        """Start the scheduler loop"""
        self.schedule_newsletter()
        self.is_running = True
        
        logger.info("Newsletter scheduler started. Press Ctrl+C to stop.")
        logger.info(f"Next scheduled runs: {schedule.jobs}")
        
        try:
            while self.is_running:
                schedule.run_pending()
                time.sleep(60)  # Check every minute
        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
            self.is_running = False
        except Exception as e:
            logger.error(f"Scheduler error: {str(e)}")
            self.is_running = False
    
    def stop_scheduler(self):
        """Stop the scheduler"""
        self.is_running = False
        schedule.clear()
        logger.info("Newsletter scheduler stopped")
    
    def get_schedule_info(self) -> List[str]:
        """Get information about scheduled jobs"""
        job_info = []
        for job in schedule.jobs:
            job_info.append(f"Next run: {job.next_run}")
        return job_info
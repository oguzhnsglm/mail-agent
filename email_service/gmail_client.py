import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from datetime import datetime

import markdown2

from utils.logger import setup_logger
from config import config

logger = setup_logger(__name__)


class GmailClient:
    """Send emails via Gmail SMTP. No credentials.json needed — just an App Password."""

    def __init__(self):
        self.sender_email = config.SENDER_EMAIL
        self.app_password = config.GMAIL_APP_PASSWORD
        self.smtp_host = "smtp.gmail.com"
        self.smtp_port = 587

    def send_email(
        self,
        to_emails: List[str],
        subject: str,
        body_html: str,
        body_text: Optional[str] = None
    ) -> bool:
        """Send an email via SMTP."""
        if not self.sender_email or not self.app_password:
            logger.error("SENDER_EMAIL or GMAIL_APP_PASSWORD not configured in .env")
            return False

        msg = MIMEMultipart("alternative")
        msg["From"] = self.sender_email
        msg["To"] = ", ".join(to_emails)
        msg["Subject"] = subject

        if body_text:
            msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.app_password)
                server.sendmail(self.sender_email, to_emails, msg.as_string())

            logger.info(f"Email sent to {', '.join(to_emails)}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("SMTP authentication failed. Check SENDER_EMAIL and GMAIL_APP_PASSWORD.")
            return False
        except Exception as e:
            logger.error(f"Error sending email: {e}")
            return False

    def format_newsletter_html(self, newsletter_content: str, subject: str) -> str:
        """Format newsletter content as beautiful HTML email."""
        if not newsletter_content.strip():
            html_content = "<p>No content available</p>"
        else:
            html_content = markdown2.markdown(
                newsletter_content,
                extras=["tables", "fenced-code-blocks", "break-on-newline"]
            )

        html_template = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{subject}</title>
            <style>
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; }}
                .container {{ max-width: 650px; margin: 0 auto; background: #ffffff; border-radius: 20px; overflow: hidden; box-shadow: 0 20px 40px rgba(0,0,0,0.1); }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 40px 30px; text-align: center; }}
                .header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 10px; }}
                .header .date {{ font-size: 16px; opacity: 0.9; }}
                .content {{ padding: 40px 30px; }}
                .content h2 {{ color: #2c3e50; font-size: 24px; margin: 30px 0 15px 0; padding-bottom: 10px; border-bottom: 3px solid #667eea; display: inline-block; }}
                .content h3 {{ color: #34495e; font-size: 20px; margin: 25px 0 10px 0; }}
                .content p {{ color: #555; line-height: 1.8; margin-bottom: 15px; font-size: 16px; }}
                .content ul {{ margin: 15px 0; padding-left: 20px; }}
                .content li {{ color: #555; line-height: 1.7; margin-bottom: 8px; }}
                .highlight {{ background: linear-gradient(120deg, #a8edea 0%, #fed6e3 100%); padding: 20px; border-radius: 15px; margin: 20px 0; border-left: 5px solid #667eea; }}
                .article-card {{ background: #f8f9fa; padding: 20px; border-radius: 12px; margin: 15px 0; border-left: 4px solid #667eea; }}
                .footer {{ background: #2c3e50; color: white; padding: 30px; text-align: center; }}
                .footer p {{ margin-bottom: 10px; opacity: 0.8; }}
                a {{ color: #667eea; text-decoration: none; font-weight: 500; }}
                @media (max-width: 600px) {{
                    .container {{ margin: 10px; border-radius: 15px; }}
                    .header, .content {{ padding: 25px 20px; }}
                    .header h1 {{ font-size: 24px; }}
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>{config.NEWSLETTER_TITLE}</h1>
                    <div class="date">{datetime.now().strftime('%B %d, %Y')}</div>
                </div>
                <div class="content">
                    {html_content}
                </div>
                <div class="footer">
                    <p>Powered by FNSS Assist AI</p>
                    <p style="font-size: 12px; margin-top: 15px; opacity: 0.6;">
                        Generated on {datetime.now().strftime('%Y-%m-%d at %H:%M:%S')}
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        return html_template

    def send_newsletter(self, newsletter_content: str, subject: str) -> bool:
        """Send newsletter to all configured recipients."""
        if not config.RECIPIENT_EMAILS or not config.RECIPIENT_EMAILS[0]:
            logger.error("No recipient emails configured")
            return False

        html_content = self.format_newsletter_html(newsletter_content, subject)
        text_content = newsletter_content.replace("**", "").replace("#", "")

        return self.send_email(
            to_emails=config.RECIPIENT_EMAILS,
            subject=subject,
            body_html=html_content,
            body_text=text_content
        )

"""
FastAPI server for newsletter generation.
Dashboard with job scheduling + API endpoints.
"""

import sys
import asyncio

# Çok önemli: Uvicorn ve Playwright'ın Windows'ta çakışmaması için en üstte policy zorlanmalı
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import nest_asyncio
nest_asyncio.apply()

from fastapi import FastAPI, HTTPException, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from pydantic import BaseModel
from typing import List, Optional, Dict
import asyncio
from datetime import datetime

import concurrent.futures

from scheduler.newsletter_scheduler import NewsletterScheduler
from utils.logger import setup_logger

logger = setup_logger(__name__)

app = FastAPI(
    title="Günlük Haber Bülteni API",
    description="Herhangi bir konuda en güncel haberleri toplayıp e-posta ile gönderen servis",
    version="2.0.0"
)

scheduler = NewsletterScheduler()

system_status = {
    "last_run": None,
    "total_sent": 0,
    "status": "idle"
}

# Scheduled jobs storage: {job_id: {email, topics, schedule_time, active, last_sent}}
scheduled_jobs: Dict[str, dict] = {}
scheduler_task = None  # background scheduler asyncio task


class GenerateRequest(BaseModel):
    email: str
    topics: List[str] = ["yapay zeka haberleri"]
    newsletter_title: Optional[str] = "Günlük Haber Bülteni"


class GenerateResponse(BaseModel):
    success: bool
    message: str


# --- Job Scheduler ---

async def scheduler_loop():
    """Check every 30 seconds if any job's time has arrived."""
    logger.info("Job scheduler started")
    while True:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            for job_id, job in scheduled_jobs.items():
                if not job["active"]:
                    continue
                if job["schedule_time"] == current_time:
                    # Don't re-send within the same minute
                    if job.get("last_triggered") == current_time:
                        continue
                    job["last_triggered"] = current_time
                    logger.info(f"Job {job_id} triggered at {current_time} for {job['email']}")
                    asyncio.create_task(
                        run_newsletter_for_user(job["email"], job["topics"], "Günlük Haber Bülteni")
                    )
        except Exception as e:
            logger.error(f"Scheduler loop error: {e}")

        await asyncio.sleep(30)


@app.on_event("startup")
async def startup_event():
    """Start the scheduler loop on server startup."""
    global scheduler_task
    scheduler_task = asyncio.create_task(scheduler_loop())
    logger.info("Newsletter scheduler background task started")


# --- Dashboard ---

@app.get("/", response_class=HTMLResponse)
async def root():
    """Dashboard with form and job list."""
    # Build jobs table rows
    jobs_html = ""
    if scheduled_jobs:
        for jid, job in scheduled_jobs.items():
            status_badge = '<span style="color:green">Aktif</span>' if job["active"] else '<span style="color:gray">Pasif</span>'
            topics_str = ", ".join(job["topics"])
            last_sent = job.get("last_sent") or "-"
            jobs_html += f"""
            <tr>
                <td>{job['email']}</td>
                <td>{topics_str}</td>
                <td><b>{job['schedule_time']}</b></td>
                <td>{status_badge}</td>
                <td>{last_sent}</td>
                <td>
                    <form method="post" action="/jobs/{jid}/delete" style="display:inline">
                        <button type="submit" class="btn btn-danger">Sil</button>
                    </form>
                </td>
            </tr>"""
    else:
        jobs_html = '<tr><td colspan="6" style="text-align:center;color:#888">Henuz job eklenmedi</td></tr>'

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Newsletter Agent</title>
        <meta charset="utf-8">
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f5f7fa; padding: 30px; }}
            .header {{ background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 30px; border-radius: 12px; margin-bottom: 25px; }}
            .header h1 {{ font-size: 26px; margin-bottom: 5px; }}
            .header p {{ opacity: 0.9; }}
            .card {{ background: white; border-radius: 10px; padding: 25px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }}
            .card h2 {{ color: #333; margin-bottom: 15px; font-size: 20px; }}
            .status-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; margin-bottom: 10px; }}
            .status-item {{ background: #f0f4ff; padding: 15px; border-radius: 8px; text-align: center; }}
            .status-item .label {{ font-size: 13px; color: #666; }}
            .status-item .value {{ font-size: 22px; font-weight: bold; color: #333; margin-top: 5px; }}
            .form-group {{ margin-bottom: 15px; }}
            .form-group label {{ display: block; font-weight: 600; margin-bottom: 5px; color: #444; }}
            .form-group input {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; }}
            .form-group small {{ color: #888; font-size: 12px; }}
            .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
            .btn {{ padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }}
            .btn-primary {{ background: #667eea; color: white; }}
            .btn-primary:hover {{ background: #5a6fd6; }}
            .btn-send {{ background: #28a745; color: white; }}
            .btn-send:hover {{ background: #218838; }}
            .btn-danger {{ background: #dc3545; color: white; padding: 5px 12px; font-size: 12px; }}
            .btn-danger:hover {{ background: #c82333; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th {{ background: #f8f9fa; padding: 10px; text-align: left; font-size: 13px; color: #666; border-bottom: 2px solid #dee2e6; }}
            td {{ padding: 10px; border-bottom: 1px solid #eee; font-size: 14px; }}
            .section-divider {{ border: none; height: 1px; background: #eee; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Günlük Haber Bülteni</h1>
            <p>İstediğiniz konuda en güncel haberleri toplayıp e-posta ile gönderin</p>
        </div>

        <!-- Status -->
        <div class="card">
            <div class="status-grid">
                <div class="status-item">
                    <div class="label">Durum</div>
                    <div class="value">{system_status['status']}</div>
                </div>
                <div class="status-item">
                    <div class="label">Son Gonderim</div>
                    <div class="value" style="font-size:14px">{system_status['last_run'] or 'Henuz yok'}</div>
                </div>
                <div class="status-item">
                    <div class="label">Toplam Gonderilen</div>
                    <div class="value">{system_status['total_sent']}</div>
                </div>
            </div>
        </div>

        <!-- Add Job Form -->
        <div class="card">
            <h2>Yeni Job Ekle</h2>
            <form method="post" action="/jobs/add">
                <div class="form-group">
                    <label>E-posta Adresi</label>
                    <input type="email" name="email" placeholder="ornek@gmail.com" required>
                </div>
                <div class="form-group">
                    <label>Konular</label>
                    <input type="text" name="topics" placeholder="örn: FNSS savunma, Türkiye ekonomi, yapay zeka" required>
                    <small>Virgül ile ayırın – her konu için internetten en güncel haberler aranacak</small>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Gonderim Saati</label>
                        <input type="time" name="schedule_time" value="09:00" required>
                    </div>
                </div>
                <button type="submit" class="btn btn-primary">Job Ekle</button>
            </form>

            <hr class="section-divider">

            <h2>Hemen Gönder (Tek Seferlik)</h2>
            <form method="post" action="/send-now">
                <div class="form-group">
                    <label>E-posta Adresi</label>
                    <input type="email" name="email" placeholder="ornek@gmail.com" required>
                </div>
                <div class="form-group">
                    <label>Konular</label>
                    <input type="text" name="topics" placeholder="örn: FNSS savunma, blockchain, ekonomi" required>
                    <small>En güncel haberler aranıp hemen e-posta olarak gönderilecek</small>
                </div>
                <button type="submit" class="btn btn-send">Şimdi Gönder</button>
            </form>
        </div>

        <!-- Jobs List -->
        <div class="card">
            <h2>Zamanlanmis Joblar ({len(scheduled_jobs)})</h2>
            <table>
                <thead>
                    <tr>
                        <th>E-posta</th>
                        <th>Konular</th>
                        <th>Saat</th>
                        <th>Durum</th>
                        <th>Son Gonderim</th>
                        <th>Islem</th>
                    </tr>
                </thead>
                <tbody>
                    {jobs_html}
                </tbody>
            </table>
        </div>

        <p style="text-align:center;margin-top:20px;color:#888;font-size:13px">
            <a href="/docs" style="color:#667eea">API Documentation</a>
        </p>
    </body>
    </html>
    """


# --- Form Handlers ---

@app.post("/jobs/add")
async def add_job(email: str = Form(...), topics: str = Form(...), schedule_time: str = Form(...)):
    """Add a scheduled job from the dashboard form."""
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    job_id = f"job_{len(scheduled_jobs)+1}_{datetime.now().strftime('%H%M%S')}"

    scheduled_jobs[job_id] = {
        "email": email,
        "topics": topic_list,
        "schedule_time": schedule_time,
        "active": True,
        "created_at": datetime.now().isoformat(),
        "last_sent": None,
        "last_triggered": None,
    }
    logger.info(f"Job added: {job_id} -> {email} at {schedule_time}")
    return RedirectResponse(url="/", status_code=303)


@app.post("/jobs/{job_id}/delete")
async def delete_job(job_id: str):
    """Delete a scheduled job."""
    if job_id in scheduled_jobs:
        del scheduled_jobs[job_id]
        logger.info(f"Job deleted: {job_id}")
    return RedirectResponse(url="/", status_code=303)


@app.post("/send-now")
async def send_now(background_tasks: BackgroundTasks, email: str = Form(...), topics: str = Form(...)):
    """Send newsletter immediately from dashboard form."""
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    background_tasks.add_task(run_newsletter_for_user, email, topic_list, "Günlük Haber Bülteni")
    logger.info(f"Immediate send triggered for {email}")
    return RedirectResponse(url="/", status_code=303)


# --- API Endpoints ---

@app.post("/api/generate", response_model=GenerateResponse)
async def generate_newsletter(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Generate newsletter for given email and topics, then send it."""
    if not req.email:
        raise HTTPException(status_code=400, detail="E-posta adresi gerekli")
    background_tasks.add_task(run_newsletter_for_user, req.email, req.topics, req.newsletter_title)
    return GenerateResponse(
        success=True,
        message=f"Newsletter uretimi baslatildi. Hazir olunca {req.email} adresine gonderilecek."
    )


@app.post("/api/generate-sync", response_model=GenerateResponse)
async def generate_newsletter_sync(req: GenerateRequest):
    """Generate and send newsletter synchronously (waits for completion)."""
    if not req.email:
        raise HTTPException(status_code=400, detail="E-posta adresi gerekli")
    success = await run_newsletter_for_user(req.email, req.topics, req.newsletter_title)
    if success:
        return GenerateResponse(success=True, message=f"Newsletter {req.email} adresine gonderildi!")
    else:
        return GenerateResponse(success=False, message="Newsletter uretilemedi veya gonderilemedi.")


async def run_newsletter_for_user(email: str, topics: List[str], title: str = "AI Newsletter") -> bool:
    """Crawl topics, process through agents, send email."""
    try:
        logger.info(f"Generating newsletter for {email} | Topics: {topics}")
        system_status["status"] = "generating"

        # Step 1: Crawl articles
        # Windows'ta Playwright subprocess desteği için crawler'ı ayrı thread'de
        # kendi ProactorEventLoop'u ile çalıştırıyoruz. Bu sayede uvicorn'un
        # event loop tipi ne olursa olsun Playwright sorunsuz çalışır.
        def _crawl_topic_in_thread(topic):
            if sys.platform == 'win32':
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(scheduler.crawler.fetch_live_data(topic))
            finally:
                loop.close()

        all_articles = []
        loop = asyncio.get_event_loop()
        for topic in topics:
            articles = await loop.run_in_executor(None, _crawl_topic_in_thread, topic)
            all_articles.extend(articles)

        if not all_articles:
            logger.warning(f"No articles found for {email}")
            system_status["status"] = "idle"
            return False

        logger.info(f"Collected {len(all_articles)} articles")

        # Step 2: Process through AI agents
        agent_results = scheduler.agents.process_articles(all_articles)
        final_newsletter = agent_results.get("final_newsletter", "")

        if not final_newsletter:
            logger.error(f"Agent pipeline returned empty for {email}")
            system_status["status"] = "idle"
            return False

        # Step 3: Format and send email
        subject = scheduler._extract_subject_line(final_newsletter)
        html_content = scheduler.gmail_client.format_newsletter_html(final_newsletter, subject)
        text_content = final_newsletter.replace('**', '').replace('#', '')

        success = scheduler.gmail_client.send_email(
            to_emails=[email],
            subject=subject,
            body_html=html_content,
            body_text=text_content
        )

        if success:
            system_status["total_sent"] += 1
            system_status["last_run"] = datetime.now().isoformat()
            # Update job's last_sent if this was a scheduled job
            for job in scheduled_jobs.values():
                if job["email"] == email:
                    job["last_sent"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            logger.info(f"Newsletter sent to {email}")
        else:
            logger.error(f"Failed to send newsletter to {email}")

        return success

    except Exception as e:
        logger.error(f"Newsletter generation error for {email}: {e}")
        return False
    finally:
        system_status["status"] = "idle"


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "scheduled_jobs": len(scheduled_jobs),
        "version": "2.0.0"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

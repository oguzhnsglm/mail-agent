"""
title: Günlük Haber Bülteni Tool
author: Newsletter Agent
author_url: https://github.com
description: AI destekli günlük haber bülteni oluşturup e-posta ile gönderir. Herhangi bir konuda en güncel haberleri internetüzerinden arar, derler ve gönderir.
required_open_webui_version: 0.4.0
requirements: requests
version: 1.1.0
licence: MIT
"""

import requests
from pydantic import BaseModel, Field


class Tools:
    def __init__(self):
        self.valves = self.Valves()

    class Valves(BaseModel):
        """Admin ayarlari - Newsletter servisinin adresi."""
        newsletter_api_url: str = Field(
            "http://localhost:8000",
            description="Newsletter servisinin calistigi adres (ornek: http://localhost:8000)"
        )

    class UserValves(BaseModel):
        """Kullanici ayarlari - Her kullanici kendi bilgilerini girer."""
        email: str = Field(
            "",
            description="Bülten alacaginiz e-posta adresiniz (ornek: isim@gmail.com)"
        )

    async def send_newsletter(
        self,
        topics: str,
        __event_emitter__=None,
        __user__=None,
    ) -> str:
        """
        Belirtilen konularda AI destekli newsletter olusturur ve kullanicinin e-postasina gonderir.
        Kullanici 'bana AI haberleri gonder', 'teknoloji newsletter istiyorum' gibi isteklerle tetikler.
        :param topics: Virgulle ayrilmis konu listesi (ornek: "AI news, Machine Learning, savunma teknolojileri")
        """
        # Get user email from UserValves
        email = ""
        if __user__ and "valves" in __user__:
            email = __user__["valves"].email

        if not email:
            return "Hata: E-posta adresiniz ayarlanmamis. Lutfen OpenWebUI ayarlarindan e-posta adresinizi girin (Workspace > Tools > Günlük Haber Bülteni Tool > User Settings)."

        # Parse topics
        topic_list = [t.strip() for t in topics.split(",") if t.strip()]
        if not topic_list:
            topic_list = ["güncel haberler"]

        # Emit status
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": f"Newsletter hazirlaniyor: {', '.join(topic_list)}...", "done": False}
            })

        # Call newsletter service
        api_url = self.valves.newsletter_api_url.rstrip("/")
        try:
            response = requests.post(
                f"{api_url}/api/generate-sync",
                json={
                    "email": email,
                    "topics": topic_list,
                    "newsletter_title": "Günlük Haber Bülteni"
                },
                timeout=300  # 5 min timeout - newsletter generation takes time
            )
            result = response.json()

            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": "Tamamlandi!", "done": True}
                })

            if result.get("success"):
                return f"Newsletter basariyla {email} adresine gonderildi! Konular: {', '.join(topic_list)}"
            else:
                return f"Newsletter gonderilemedi: {result.get('message', 'Bilinmeyen hata')}"

        except requests.exceptions.ConnectionError:
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": "Servis baglanti hatasi", "done": True}
                })
            return "Hata: Newsletter servisine baglanilamiyor. Servisin calistigindan emin olun (http://localhost:8000)."
        except requests.exceptions.Timeout:
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": "Zaman asimi", "done": True}
                })
            return "Hata: Newsletter uretimi zaman asimina ugradi. Lutfen tekrar deneyin."
        except Exception as e:
            if __event_emitter__:
                await __event_emitter__({
                    "type": "status",
                    "data": {"description": "Hata olustu", "done": True}
                })
            return f"Hata: {str(e)}"

    async def check_newsletter_service(
        self,
        __event_emitter__=None,
    ) -> str:
        """
        Newsletter servisinin calisip calismadigini kontrol eder.
        Kullanici 'servis calisiyor mu', 'newsletter durumu' gibi sorularla tetikler.
        """
        api_url = self.valves.newsletter_api_url.rstrip("/")
        try:
            response = requests.get(f"{api_url}/health", timeout=10)
            data = response.json()
            if data.get("status") == "healthy":
                return f"Newsletter servisi calisiyor. Versiyon: {data.get('version', '?')}"
            return "Newsletter servisi yanit veriyor ama saglikli degil."
        except Exception:
            return "Newsletter servisine ulasilamiyor. Servisin calistigindan emin olun."

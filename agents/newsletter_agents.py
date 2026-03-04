from typing import List, Dict, Any
from langgraph.graph import StateGraph, END
import datetime

from agents.llm_client import OpenRouterClient
from utils.logger import setup_logger

logger = setup_logger(__name__)

def _today_tr():
    months = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    now = datetime.datetime.now()
    return f"{now.day} {months[now.month]} {now.year}"

class NewsletterAgents:
    def __init__(self):
        self.llm_client = OpenRouterClient()
        self.graph = self._create_agent_graph()

    def _create_agent_graph(self) -> StateGraph:
        """Create the multi-agent workflow graph using LangGraph"""
        from typing import TypedDict

        class NewsletterState(TypedDict):
            raw_articles: List[Dict]
            final_newsletter: str

        # Create workflow graph - simplified to directly format raw articles
        workflow = StateGraph(NewsletterState)

        # Add node (agent)
        workflow.add_node("editor", self._editor_agent)

        # Define the workflow edges
        workflow.add_edge("editor", END)

        # Set entry point
        workflow.set_entry_point("editor")

        return workflow.compile()

    def _get_topic(self, state: Dict[str, Any]) -> str:
        articles = state.get("raw_articles", [])
        if articles:
            return articles[0].get("topic", "Güncel Haberler")
        return "Güncel Haberler"

    def _editor_agent(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Editor agent - formats the raw articles directly into the newsletter"""
        logger.info("Editor agent structuring raw data...")
        
        articles = state.get("raw_articles", [])
        topic = self._get_topic(state)
        
        if not articles:
            logger.warning("No articles provided to editor agent")
            state["final_newsletter"] = f"Konu: {topic}\n\nYeni bir haber bulunamadı."
            return state

        # Format directly from raw data without LLM hallucination
        system_message = "Sen sadece sana verilen HTML/Web metinlerini temiz, yalın bir Türkçe haber formatında düzenleyen bir asistansın. ASLA ekstra bir bilgi ekleme, yorum yapma veya giriş/çıkış cümlesi kurma."
        
        articles_text = ""
        for i, article in enumerate(articles, 1):
            url = article.get('url', 'URL Yok')
            content = article.get('content', '') or article.get('summary', '')
            content = content[:8000] # Limit content length
            title = article.get('title', f"Haber {i}")
            pub_date = article.get('published_date', '')
            articles_text += f"\nHABER {i}:\nURL: {url}\nBAŞLIK: {title}\nTARİH: {pub_date}\nİÇERİK ÖZETİ: {content}\n---\n"
        
        prompt = f"""Bugünkü konu: {topic}
Tarih: {_today_tr()}

Aşağıda web crawler tarafından çekilmiş, ham (raw) haber verileri ve kaynak linkleri bulunuyor. Senden tek istenen bu verileri aşağıda belirtilen kesin E-POSTA FORMATI ile Türkçe olarak temizlemen ve listelemen. ASLA içerikte olmayan bir şeyi uydurma. ASLA giriş (Merhaba, bültene hoşgeldiniz vb.) veya kapanış (İyi günler, saygılar vb.) mesajı yazma.

VERİLER:
{articles_text}

İSTENEN KESİN FORMAT:
Subject Line: [{_today_tr()} - {topic} Haberleri]

(Her haber için aşağıdaki bloğu aynen çoğalt)
### [Haber Başlığı]
**Tarih:** [Sana gönderilen TARİH alanını kullan. Eğer boşsa {_today_tr()} yaz.]
**Detaylar:** [Sana gönderilen İÇERİK ÖZETİ içindeki gerçek bilgileri 3-5 cümlelik detaylı bir Türkçe haber paragrafına çevir. Uydurma bilgi ekleme. İçerikteki önemli detayları, rakamları ve isimleri mutlaka dahil et.]
**Kaynak Linki:** [Sana gönderilen "URL" linki, hiçbir html etiketi olmadan direkt çıplak link]
---
"""
        try:
            final_newsletter = self.llm_client.generate_completion(
                prompt=prompt,
                system_message=system_message,
                temperature=0.1
            )
            state["final_newsletter"] = final_newsletter.strip()
            logger.info("Editor agent correctly formatted text.")
        except Exception as e:
            logger.error(f"Error in LLM completion: {e}")
            state["final_newsletter"] = f"Mail içeriği oluşturulamadı: {str(e)}"

        return state
    
    def process_articles(self, articles: List[Dict]) -> Dict[str, Any]:
        """Main method to process articles through the agent workflow"""
        logger.info(f"Processing {len(articles)} articles through agent workflow")
        
        initial_state = {
            "raw_articles": articles,
            "final_newsletter": ""
        }
        
        try:
            final_state = self.graph.invoke(initial_state)
            logger.info("Agent workflow completed successfully")
            return final_state
        except Exception as e:
            logger.error(f"Error in agent workflow: {str(e)}")
            return initial_state

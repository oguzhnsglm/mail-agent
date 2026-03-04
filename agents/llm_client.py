import requests
from typing import Dict, Any, Optional
from utils.logger import setup_logger
from config import config

logger = setup_logger(__name__)

class OpenRouterClient:
    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.base_url = config.OPENROUTER_BASE_URL
        self.default_model = config.DEFAULT_MODEL
        self.temperature = config.TEMPERATURE
        self.max_tokens = config.MAX_TOKENS
        
        if not self.api_key:
            raise ValueError("OpenRouter API key not found in configuration")
    
    def generate_completion(
        self, 
        prompt: str, 
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_message: Optional[str] = None
    ) -> str:
        """Generate completion using OpenRouter API"""
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000",  # Required by OpenRouter
            "X-Title": "AI Newsletter Agent"  # Optional, for tracking
        }
        
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature or self.temperature,
            "max_tokens": max_tokens or self.max_tokens
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=180
            )
            response.raise_for_status()
            
            result = response.json()
            
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                logger.error(f"Unexpected response format: {result}")
                return ""
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling OpenRouter API: {str(e)}")
            return ""
        except Exception as e:
            logger.error(f"Unexpected error in LLM completion: {str(e)}")
            return ""
    
    def generate_with_retry(
        self, 
        prompt: str, 
        max_retries: int = 3,
        **kwargs
    ) -> str:
        """Generate completion with retry logic"""
        
        for attempt in range(max_retries):
            try:
                result = self.generate_completion(prompt, **kwargs)
                if result:
                    return result
                else:
                    logger.warning(f"Empty response on attempt {attempt + 1}")
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {str(e)}")
                
            if attempt < max_retries - 1:
                logger.info(f"Retrying in 2 seconds...")
                import time
                time.sleep(2)
        
        logger.error(f"All {max_retries} attempts failed")
        return ""
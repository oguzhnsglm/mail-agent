import requests
from typing import Dict, Any, Optional
from utils.logger import setup_logger
from config import config

logger = setup_logger(__name__)

class OpenRouterClient:
    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.base_url = (config.OPENROUTER_BASE_URL or "").rstrip("/")
        self.default_model = config.DEFAULT_MODEL
        self.temperature = config.TEMPERATURE
        self.max_tokens = config.MAX_TOKENS
        
        if not self.api_key:
            raise ValueError("OpenRouter API key not found in configuration")

    def _normalized_model(self, model: str) -> str:
        model = (model or "").strip()
        if not model:
            return model

        # gpt-5.4 is not a valid public model id for OpenAI/OpenRouter chat completions.
        if model == "gpt-5.4":
            logger.warning("Invalid model id 'gpt-5.4' detected; switching to 'gpt-5.2'")
            model = "gpt-5.2"

        # If the endpoint is OpenAI, provider prefixes like openai/gpt-5 are invalid.
        if "api.openai.com" in self.base_url and "/" in model:
            model = model.split("/", 1)[1]

        return model
    
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
        
        resolved_model = self._normalized_model(model or self.default_model)
        resolved_max_tokens = self.max_tokens if max_tokens is None else max_tokens

        payload = {
            "model": resolved_model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
        }
        if resolved_model.startswith("gpt-5"):
            payload["max_completion_tokens"] = resolved_max_tokens
        else:
            payload["max_tokens"] = resolved_max_tokens

        try:
            request_url = f"{self.base_url}/chat/completions"
            response = requests.post(
                request_url,
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
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            response_text = (e.response.text or "")[:1200] if e.response is not None else ""
            logger.error(
                f"LLM API HTTP error | status={status_code} | model={payload.get('model')} | "
                f"url={request_url} | response={response_text}"
            )
            return ""
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Error calling OpenRouter API | model={payload.get('model')} | "
                f"url={request_url} | error={str(e)}"
            )
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

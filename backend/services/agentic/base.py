import logging
import httpx
from typing import List, Dict, Any, Callable
from backend.core.config import settings

logger = logging.getLogger("pilot.agent.base")

class BaseAgent:
    """
    Base Agent class that maintains message history and coordinates LLM calls
    with sequential fallback: Groq -> Gemini -> Ollama.
    """
    def __init__(self, name: str, system_instruction: str, tools: Dict[str, Callable] = None):
        self.name = name
        self.system_instruction = system_instruction
        self.tools = tools or {}
        self.history: List[Dict[str, str]] = []

    def clear_history(self):
        """Clears stateful conversation history."""
        self.history = []

    def add_message(self, role: str, content: str):
        """Adds a message to the agent's contextual memory."""
        self.history.append({"role": role, "content": content})

    async def call_llm(self, prompt: str, schema_format: str = None, system_instruction: str = None) -> str:
        """
        Executes a prompt against configured LLMs, prioritizing local Ollama first,
        falling back to Groq second, and Gemini third.
        """
        sys_inst = system_instruction or self.system_instruction

        # 1. Ollama (Priority 1)
        try:
            url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
            messages = [
                {"role": "system", "content": sys_inst},
                *self.history,
                {"role": "user", "content": prompt}
            ]
            payload = {
                "model": "qwen2.5:3b",
                "stream": False,
                "messages": messages,
                "options": {"temperature": 0.2}
            }
            if schema_format == "json":
                payload["format"] = "json"

            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    result = resp.json().get("message", {}).get("content", "").strip()
                    if result:
                        return result
        except Exception as e:
            logger.warning(f"[{self.name}] Local Ollama execution failed: {e}. Falling back to Groq...")

        # 2. Groq (Fallback 2)
        if settings.GROQ_API_KEY:
            try:
                from groq import AsyncGroq
                client = AsyncGroq(api_key=settings.GROQ_API_KEY)
                messages = [
                    {"role": "system", "content": sys_inst},
                    *self.history,
                    {"role": "user", "content": prompt}
                ]
                kwargs = {
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    "temperature": 0.2
                }
                # Fix Groq JSON mode error: messages must mention 'json' if response_format of type 'json_object' is used
                if schema_format == "json":
                    kwargs["response_format"] = {"type": "json_object"}
                    # Ensure "json" is present in the system prompt or user query
                    has_json = any("json" in str(m.values()).lower() for m in messages)
                    if not has_json:
                        # Append a subtle instruction to user query to avoid Groq validation error
                        messages[-1]["content"] += "\nReturn the response strictly in JSON format."

                resp = await client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"[{self.name}] Groq execution failed: {e}. Falling back to Gemini...")

        # 3. Gemini (Fallback 3)
        if settings.GEMINI_API_KEY:
            try:
                import google.generativeai as genai
                genai.configure(api_key=settings.GEMINI_API_KEY)
                model = genai.GenerativeModel("gemini-2.5-flash")
                
                config = {}
                if schema_format == "json":
                    config["response_mime_type"] = "application/json"
                
                # Synthesize history into prompt context for Gemini, including system instruction
                full_prompt = f"SYSTEM INSTRUCTION: {sys_inst}\n\n"
                for msg in self.history:
                    full_prompt += f"{msg['role'].upper()}: {msg['content']}\n"
                full_prompt += f"USER: {prompt}"

                resp = await model.generate_content_async(full_prompt, generation_config=config)
                return resp.text.strip()
            except Exception as e:
                logger.error(f"[{self.name}] Gemini execution failed: {e}")

        raise RuntimeError(f"[{self.name}] All LLM providers failed or are unconfigured.")



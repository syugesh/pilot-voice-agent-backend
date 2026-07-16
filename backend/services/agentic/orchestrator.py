import json
import logging
from typing import Dict, Any, List
from backend.services.agentic.base import BaseAgent
from backend.services.agentic.subagents import TravelSubAgent, SlidesSubAgent, BiometricSubAgent

logger = logging.getLogger("pilot.agent.orchestrator")

class OrchestratorAgent(BaseAgent):
    """
    Main Orchestrator Agent that routes user intents to specialized sub-agents
    and manages stateful interaction context.
    """
    def __init__(self):
        system_instruction = (
            "You are the Lead Orchestrator for PILOT. Your goal is to analyze user prompts "
            "and determine the correct routing category.\n"
            "Categories:\n"
            "1. 'travel': Flight search, travel bookings, hotel, train, or cab queries.\n"
            "2. 'slides': Slide navigation, creating presentations, editing slides, clearing presentations.\n"
            "3. 'biometrics': Biometric verification, voice profile checks, user enrollment.\n"
            "4. 'general_qa': Informational questions, code requests, calculations, or detailed general research.\n"
            "5. 'chit_chat': Simple greetings, short comments, or simple conversational checks.\n\n"
            "You MUST respond ONLY with a JSON object in this format:\n"
            "{\n"
            '  "category": "travel" | "slides" | "biometrics" | "general_qa" | "chit_chat",\n'
            '  "reason": "short explanation"\n'
            "}"
        )
        super().__init__(name="Orchestrator", system_instruction=system_instruction)
        
        # Instantiate subagents
        self.travel_agent = TravelSubAgent()
        self.slides_agent = SlidesSubAgent()
        self.biometrics_agent = BiometricSubAgent()

    async def route_and_execute(self, text: str, speaker_id: str, role: str, context: List[Dict[str, Any]], session_id: str = "") -> Dict[str, Any]:
        """
        Decides category and forwards the execution to the appropriate sub-agent.
        """
        # Step 1: Query the LLM to get the routing category
        routing_json = await self.call_llm(
            prompt=f"Utterance: '{text}'\nSpeaker: {speaker_id} (Role: {role})",
            schema_format="json"
        )
        
        try:
            decision = json.loads(routing_json)
            category = decision.get("category", "chit_chat")
        except Exception:
            # Safe fallback: default to chit_chat
            category = "chit_chat"
            
        logger.info(f"[Orchestrator] Routed query '{text}' to category: {category}")
        
        # Keep track of dialogue history
        self.add_message("user", text)
        
        # Step 2: Delegate to the correct agent or return standard response
        if category == "travel":
            # Delegate to Travel Agent
            agent_reply = await self.travel_agent.call_llm(text, schema_format="json")
            return await self._parse_subagent_output(agent_reply, speaker_id, session_id)
        elif category == "slides":
            # Delegate to Slides Agent
            agent_reply = await self.slides_agent.call_llm(text, schema_format="json")
            return await self._parse_subagent_output(agent_reply, speaker_id, session_id)
        elif category == "biometrics":
            # Delegate to Biometrics Agent
            agent_reply = await self.biometrics_agent.call_llm(text, schema_format="json")
            return await self._parse_subagent_output(agent_reply, speaker_id, session_id)
        elif category == "general_qa":
            # Return general_qa tool delegation
            return {
                "action": "delegate",
                "preamble": "Let me look that up for you.",
                "tool": "general_qa",
                "args": {"query": text},
                "mode": "queue"
            }
        else:
            # Direct response (chit_chat)
            # Query itself for a conversational chat response — override system prompt so it responds conversationally
            chat_reply = await self.call_llm(
                prompt=f"Respond conversationally to: {text}. Keep it under 2 sentences.",
                system_instruction="You are PILOT, a helpful voice AI assistant. Respond conversationally and warmly. Keep responses under 2 sentences."
            )
            return {
                "action": "respond_now",
                "preamble": chat_reply,
                "tool": None,
                "args": {},
                "mode": "queue"
            }

    async def _parse_subagent_output(self, reply: str, speaker_id: str, session_id: str = "") -> Dict[str, Any]:
        try:
            result = json.loads(reply)
            
            # Instant-Execution Intercept for Slide Navigation tools
            tool_name = result.get("tool")
            
            original_tool_name = tool_name
            # Normalize Travel Agent tool names to match backend registry and supervisor expectations
            if tool_name in ("hotel_search", "train_search", "cab_search"):
                result["tool"] = "flight_search"
                tool_name = "flight_search"
                if "args" not in result:
                    result["args"] = {}
                # Ensure the correct service_type is set for search dispatch
                if "service_type" not in result["args"]:
                    if "hotel" in original_tool_name:
                        result["args"]["service_type"] = "hotels"
                    elif "train" in original_tool_name:
                        result["args"]["service_type"] = "trains"
                    elif "cab" in original_tool_name:
                        result["args"]["service_type"] = "cabs"
            elif tool_name in ("hotel_book", "train_book", "cab_book"):
                result["tool"] = "flight_book"
                tool_name = "flight_book"

            if tool_name in ("ppt_navigate", "ppt_jump_to_title"):
                from backend.tools.ppt_copilot import ppt_navigate, ppt_jump_to_title
                
                # Execute the tool immediately in the request pipeline
                if tool_name == "ppt_navigate":
                    await ppt_navigate(result.get("args", {}), session_id)
                elif tool_name == "ppt_jump_to_title":
                    await ppt_jump_to_title(result.get("args", {}), session_id)
                
                # Convert to direct spoken response to bypass bg_supervisor queue
                result["action"] = "respond_now"
                result["tool"] = None

            # Format preamble if needed
            preamble = result.get("preamble", "")
            if preamble and speaker_id and speaker_id not in ("You", "unknown"):
                result["preamble"] = f"Hello {speaker_id}! {preamble}"
            return result
        except Exception:
            return {
                "action": "respond_now",
                "preamble": reply,
                "tool": None,
                "args": {},
                "mode": "queue"
            }

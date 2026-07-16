/**
 * WS event types — TypeScript side (frontend).
on the client side events. 
 */


// union of all recognized event string.
export type WSEventType =
  | "transcript" // run live transcribed audio text.
  | "tool_start"
  | "tool_end"
  | "job_queued" 
  | "confirm_prompt" //rbac gates , policy
  | "ppt_command" 
  | "tts_audio" //base64 audio arrays for tts playback.
  | "tool_blocked" 
  | "route_decision"
  | "session_state"
  | "barge_in"
  | "chat_message"
  | "profile_updated"
  // DISABLED: route_page superseded by navigate_page (tools/navigation.py,
  // transferred from the upstream GD-template ppt-copilot implementation)
  // | "route_page"
  | "navigate_page"
  // Customer Resolution — CSR-dashboard resolution/escalation assessment,
  // transferred from the upstream pilot-voice-agent-backend implementation.
  | "resolution_update"
  | "sentiment_update"
  | "ping";

// generic event interface. 
export interface WSEvent<T = unknown> {
  type: WSEventType;
  payload: T;
}

// interface for transcript event payload.
export interface TranscriptPayload {
  text: string;
  speaker: string | null;
  role: string | null;
  confidence: number;
  timestamp: number;
}


// interface for initiation of tool.
export interface ToolStartPayload {
  job_id: string;
  tool: string;
  speaker: string | null;
  role: string | null;
}

// interface for completion of tool.
export interface ToolEndPayload {
  job_id: string;
  tool: string;
  result: Record<string, unknown>;
  latency_ms?: number;
}

// interface for rbac gates , policy decision.
export interface ConfirmPromptPayload {
  tool: string;
  speaker: string;
  message: string;
}

// interface for ppt command. 
export interface PPTCommandPayload {
  action: "next" | "prev" | "first" | "last" | "goto";
  index?: number;
}


// interface for navigate_page (voice-driven page redirect), transferred
// from the upstream GD-template ppt-copilot implementation.
export interface NavigatePagePayload {
  page: "dashboard" | "ppt" | "care" | "email" | "meetings" | "chat" | "guideline" | "resolution" | "profile" | "settings";
}

// interface for resolution_update (Customer Resolution dashboard).
export interface ResolutionUpdatePayload {
  resolution_confidence: number;
  recommendation: "resolve" | "escalate";
  reasoning: string;
  escalation_target: string | null;
  escalation_reasons: string[];
  signals: Record<string, unknown>;
  issue_summary: string;
  kb_articles: { doc_id: number; title: string; excerpt: string; score: number }[];
  status?: string;
  spoken_reply?: string;
}

// interface for sentiment_update (live customer frustration meter).
export interface SentimentUpdatePayload {
  sentiment: "negative" | "neutral" | "positive";
  sentiment_score: number;
  frustration_score: number;
  urgency: "low" | "medium" | "high";
  timestamp: number;
}

// interface for job queue/interrupt.
export interface JobQueuedPayload {
  job_id: string;
  tool: string;
  requester: string | null;
  mode: "queue" | "interrupt";
}

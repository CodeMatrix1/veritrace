export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:7860";

export type EvidenceView = {
  id: string;
  quote: string;
  stance: "supports" | "contradicts" | "irrelevant";
  reason: string;
  validation_status: "valid" | "contextual" | "invalid";
  validation_reason: string | null;
  used_in_verdict: boolean;
  source_title: string | null;
  source_url: string;
  publisher: string;
  source_tier?: "primary" | "secondary" | "unknown";
  retrieved_via: string;
  published_date: string | null;
  retrieved_at: string;
};

export type ClaimView = {
  id: string;
  text: string;
  verdict: string | null;
  confidence: string | null;
  rule_id: string | null;
  explanation: string | null;
  evidence: EvidenceView[];
  gaps: string[];
};

export type LedgerEntry = {
  label: string;
  claim_id: string | null;
  kind: "source_statement" | "model_assessment" | "uncertain";
  statement: string;
};

export type SourceRow = {
  title: string | null;
  publisher: string;
  date: string | null;
  url: string;
};

export type SourceHistoryView = {
  earliest_found: SourceRow | null;
  earliest_label: string;
  note: string;
  dated: SourceRow[];
  undated: SourceRow[];
};

export type AnswerSentence = {
  claim_id?: string;
  fallback?: boolean;
  text: string;
  sources: { publisher: string; url: string }[];
};

export type Report = {
  overall_result: string;
  answer?: AnswerSentence[];
  summary: string | null;
  summary_label: string | null;
  claims: ClaimView[];
  not_investigated: { text: string; kind: string }[];
  ledger: LedgerEntry[];
  source_history: Record<string, SourceHistoryView>;
  coverage_gaps: string[];
  checked_at?: string;
  reused?: boolean;
  source_page?: { title: string; url: string } | null;
};

export type Audit = {
  audit_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  input_type: string;
  input_text: string;
  claims: string[];
  source_page: { title: string; url: string } | null;
  created_at: string;
  elapsed_seconds: number;
  estimate_seconds: number;
  current_stage: string | null;
  overall_result: string | null;
  public_error: string | null;
  report: Report | null;
};

export type TraceEvent = {
  id: number;
  stage: string;
  status: string;
  message: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : "Please check your input.";
    throw new ApiError(detail, response.status);
  }
  return response.json();
}

export function createAudit(type: "text" | "url", content: string, fresh = false) {
  return request<{ audit_id: string }>("/api/audits", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, content, fresh }),
  });
}

export function cancelAudit(id: string) {
  return request<{ status: string }>(`/api/audits/${id}/cancel`, { method: "POST" });
}

export function getAudit(id: string) {
  return request<Audit>(`/api/audits/${id}`);
}

export function getEvents(id: string, afterId: number) {
  return request<{ events: TraceEvent[]; last_id: number; finished: boolean }>(
    `/api/audits/${id}/events?after_id=${afterId}`,
  );
}

export type ChatReply = {
  found: boolean;
  mode: "saved" | "live" | "web" | "document" | "none";
  answer: string;
  understood_as?: string;
  sections?: { title: string; sentences: string[] }[];
  pages: number;
  sources: {
    title: string;
    url: string;
    publisher: string;
    published: string | null;
    official: boolean;
    quote: string;
  }[];
};

export function askQuestion(
  question: string,
  documentUrl?: string,
  history: { question: string; answer: string; source_urls: string[] }[] = [],
) {
  return request<ChatReply>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, document_url: documentUrl, history }),
  });
}

export function getChatStatus() {
  return request<{ pages: number; remaining?: number }>("/api/chat/status");
}

export function getHealth() {
  return request<{ status: string; daily_audits_remaining: number }>("/api/health");
}

export function formatLabel(value: string) {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

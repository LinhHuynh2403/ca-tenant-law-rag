// In production, Vercel injects VITE_API_URL (set in the project's env
// vars) so the built frontend points at the deployed backend instead of
// localhost.
const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export interface CitationInfo {
  citation: string;
  text: string;
  source_url: string;
  section_number: string;
  subsection_path: string | null;
  label: string | null;
}

export interface AskResponse {
  answer: string;
  citations: CitationInfo[];
  is_fully_grounded: boolean;
}

export async function askQuestion(question: string): Promise<AskResponse> {
  const res = await fetch(`${API_BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });

  if (!res.ok) {
    throw new Error(`Request failed (${res.status}): ${await res.text()}`);
  }

  return res.json();
}

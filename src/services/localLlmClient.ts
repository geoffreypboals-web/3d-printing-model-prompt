import type { ChatMessage } from "../types.js";

const baseUrl = process.env.LOCAL_LLM_BASE_URL ?? "http://127.0.0.1:11434";
const model = process.env.LOCAL_LLM_MODEL ?? "llama3";

export class LocalLlmError extends Error {}

const describeFailedResponse = async (response: Response): Promise<string> => {
  const body = await response.text().catch(() => "");
  const trimmed = body.trim().slice(0, 300);
  return `local llm request failed (${response.status})${trimmed ? `: ${trimmed}` : ""}`;
};

/**
 * Extracts the first top-level JSON object from a model response, tolerating
 * stray text around it. Mirrors the defensive parsing already used in
 * 3dPrintFarmManager's slicerProfileConsolidator.ts for the same local-LLM setup.
 */
export const extractJsonObject = <T>(raw: string): T => {
  const jsonStart = raw.indexOf("{");
  const jsonEnd = raw.lastIndexOf("}");
  if (jsonStart === -1 || jsonEnd === -1 || jsonEnd <= jsonStart) {
    throw new LocalLlmError("local llm response did not contain a JSON object");
  }
  return JSON.parse(raw.slice(jsonStart, jsonEnd + 1)) as T;
};

export const chatWithLocalLlm = async (systemPrompt: string, history: ChatMessage[]): Promise<string> => {
  const response = await fetch(`${baseUrl}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      messages: [{ role: "system", content: systemPrompt }, ...history],
      stream: false,
      options: { temperature: 0.4 }
    })
  });

  if (!response.ok) {
    throw new LocalLlmError(await describeFailedResponse(response));
  }

  const payload = (await response.json()) as { message?: { content?: string } };
  const content = payload.message?.content?.trim();
  if (!content) {
    throw new LocalLlmError("empty local llm response");
  }
  return content;
};

export const generateWithLocalLlm = async (prompt: string): Promise<string> => {
  const response = await fetch(`${baseUrl}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model, prompt, stream: false, options: { temperature: 0.3 } })
  });

  if (!response.ok) {
    throw new LocalLlmError(await describeFailedResponse(response));
  }

  const payload = (await response.json()) as { response?: string };
  const content = payload.response?.trim();
  if (!content) {
    throw new LocalLlmError("empty local llm response");
  }
  return content;
};

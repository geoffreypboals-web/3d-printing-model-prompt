import { Router } from "express";
import { chatWithLocalLlm, extractJsonObject, generateWithLocalLlm, LocalLlmError } from "../services/localLlmClient.js";
import { INTERVIEW_SYSTEM_PROMPT } from "../prompts/interviewSystemPrompt.js";
import { buildSynthesisPrompt } from "../prompts/synthesizePrompt.js";
import type { ChatMessage, InterviewAnswers, InterviewTurnResult, SynthesizedPrompt } from "../types.js";

export const interviewRouter = Router();

interviewRouter.post("/turn", async (req, res) => {
  const history = (req.body?.history ?? []) as ChatMessage[];
  if (!Array.isArray(history)) {
    res.status(400).json({ error: "history must be an array of chat messages" });
    return;
  }

  try {
    const raw = await chatWithLocalLlm(INTERVIEW_SYSTEM_PROMPT, history);
    const turn = extractJsonObject<InterviewTurnResult>(raw);
    res.json(turn);
  } catch (error) {
    const message = error instanceof LocalLlmError ? error.message : "unexpected error contacting the local LLM";
    res.status(502).json({ error: message });
  }
});

interviewRouter.post("/finalize", async (req, res) => {
  const answers = (req.body?.answers ?? {}) as InterviewAnswers;

  try {
    const raw = await generateWithLocalLlm(buildSynthesisPrompt(answers));
    const synthesized = extractJsonObject<SynthesizedPrompt>(raw);
    res.json(synthesized);
  } catch (error) {
    const message = error instanceof LocalLlmError ? error.message : "unexpected error contacting the local LLM";
    res.status(502).json({ error: message });
  }
});

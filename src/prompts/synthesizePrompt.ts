import type { InterviewAnswers } from "../types.js";

export const buildSynthesisPrompt = (answers: InterviewAnswers): string =>
  `You are an expert prompt engineer for text-to-3D mesh generators (Meshy, Tripo3D, Rodin, and similar) running on a local Ollama model. ` +
  `Turn the structured interview answers below into a single high-quality generation prompt. ` +
  `Write "prompt" as a dense, comma-separated descriptive phrase (the way these tools expect: subject first, then style, materials, and notable details) rather than full sentences. ` +
  `Write "negativePrompt" listing concrete things to exclude, based on any constraints given (leave it a sensible empty string if nothing was excluded). ` +
  `Write "styleTags" as 3-8 short lowercase tags (e.g. "low-poly", "hard-surface", "pbr", "game-ready", "watertight") drawn from the style, topology, and output-format answers. ` +
  `Return ONLY this JSON object, no text before or after it: {"prompt": "...", "negativePrompt": "...", "styleTags": ["..."]}. ` +
  `Interview answers: ${JSON.stringify(answers)}`;

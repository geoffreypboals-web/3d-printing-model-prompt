import { randomUUID } from "node:crypto";
import { Router } from "express";
import { activeRenderProvider, render, RenderProviderError } from "../services/renderProviders/index.js";
import type { SynthesizedPrompt } from "../types.js";

export const renderRouter = Router();

const outputDir = process.env.OUTPUT_DIR ?? "output";

renderRouter.get("/provider", (_req, res) => {
  res.json({ provider: activeRenderProvider() });
});

renderRouter.post("/", async (req, res) => {
  const synthesized = req.body as SynthesizedPrompt;
  if (!synthesized?.prompt) {
    res.status(400).json({ error: "prompt is required" });
    return;
  }

  const runId = randomUUID();
  try {
    const result = await render(synthesized, outputDir, runId);
    res.json(result);
  } catch (error) {
    const message = error instanceof RenderProviderError ? error.message : "unexpected error while rendering";
    res.status(502).json({ error: message });
  }
});

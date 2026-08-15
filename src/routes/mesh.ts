import { randomUUID } from "node:crypto";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { Router } from "express";
import { analyzeMinThickness, applySolidify, BlenderError } from "../services/blenderMesh.js";
import type { ThicknessAnalysisResult, ThicknessApplyRequest, ThicknessApplyResult } from "../types.js";

export const meshRouter = Router();

const outputDir = process.env.OUTPUT_DIR ?? "output";

const resolveWithinOutputDir = (meshPath: string): string => {
  const resolvedOutputDir = path.resolve(outputDir);
  const resolved = path.resolve(meshPath);
  if (resolved !== resolvedOutputDir && !resolved.startsWith(resolvedOutputDir + path.sep)) {
    throw new Error("meshPath must be inside the output directory");
  }
  return resolved;
};

const describeError = (error: unknown): { status: number; message: string } => {
  if (error instanceof BlenderError) {
    return { status: 502, message: error.message };
  }
  return { status: 400, message: error instanceof Error ? error.message : "unexpected error" };
};

meshRouter.post("/analyze-thickness", async (req, res) => {
  const meshPath = req.body?.meshPath as string | undefined;
  if (!meshPath) {
    res.status(400).json({ error: "meshPath is required" });
    return;
  }

  try {
    const resolved = resolveWithinOutputDir(meshPath);
    const estimatedMinThicknessMm = await analyzeMinThickness(resolved);
    const result: ThicknessAnalysisResult = { estimatedMinThicknessMm };
    res.json(result);
  } catch (error) {
    const { status, message } = describeError(error);
    res.status(status).json({ error: message });
  }
});

meshRouter.post("/apply-thickness", async (req, res) => {
  const body = req.body as Partial<ThicknessApplyRequest>;
  if (
    !body?.meshPath ||
    typeof body.thicknessMm !== "number" ||
    !Number.isFinite(body.thicknessMm) ||
    body.thicknessMm <= 0 ||
    (body.preserve !== "inside" && body.preserve !== "outside")
  ) {
    res.status(400).json({ error: "meshPath, a positive thicknessMm, and preserve ('inside'|'outside') are required" });
    return;
  }

  try {
    const resolvedInput = resolveWithinOutputDir(body.meshPath);
    await mkdir(outputDir, { recursive: true });

    const runId = randomUUID();
    const outputMeshPath = path.join(outputDir, `${runId}.glb`);
    const outputPreviewPath = path.join(outputDir, `${runId}.png`);

    await applySolidify(resolvedInput, outputMeshPath, outputPreviewPath, body.thicknessMm, body.preserve);

    const result: ThicknessApplyResult = {
      meshPath: outputMeshPath,
      imageUrl: `/output/${runId}.png`
    };
    res.json(result);
  } catch (error) {
    const { status, message } = describeError(error);
    res.status(status).json({ error: message });
  }
});

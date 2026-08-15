import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { RenderResult, SynthesizedPrompt } from "../../types.js";
import { RenderProviderError } from "./localSdProvider.js";

// Meshy's text-to-3D API. Verify field names/paths against https://docs.meshy.ai
// before relying on this in production — third-party API shapes drift over time.
const meshyBaseUrl = "https://api.meshy.ai/openapi/v2/text-to-3d";
const pollIntervalMs = 3000;
const maxPollAttempts = 60;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export const renderWithMeshy = async (
  synthesized: SynthesizedPrompt,
  outputDir: string,
  runId: string
): Promise<RenderResult> => {
  const apiKey = process.env.MESHY_API_KEY;
  if (!apiKey) {
    throw new RenderProviderError("MESHY_API_KEY is not set");
  }

  const createResponse = await fetch(meshyBaseUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      mode: "preview",
      prompt: synthesized.prompt,
      negative_prompt: synthesized.negativePrompt,
      art_style: "realistic"
    })
  });

  if (!createResponse.ok) {
    throw new RenderProviderError(`Meshy task creation failed (${createResponse.status})`);
  }

  const created = (await createResponse.json()) as { result?: string };
  const taskId = created.result;
  if (!taskId) {
    throw new RenderProviderError("Meshy did not return a task id");
  }

  for (let attempt = 0; attempt < maxPollAttempts; attempt += 1) {
    await sleep(pollIntervalMs);
    const statusResponse = await fetch(`${meshyBaseUrl}/${taskId}`, {
      headers: { Authorization: `Bearer ${apiKey}` }
    });
    if (!statusResponse.ok) {
      throw new RenderProviderError(`Meshy task status check failed (${statusResponse.status})`);
    }
    const status = (await statusResponse.json()) as {
      status?: string;
      thumbnail_url?: string;
      model_urls?: { glb?: string };
    };

    if (status.status === "FAILED" || status.status === "CANCELED") {
      throw new RenderProviderError(`Meshy task ended with status ${status.status}`);
    }

    if (status.status === "SUCCEEDED" && status.thumbnail_url) {
      await mkdir(outputDir, { recursive: true });
      const imageResponse = await fetch(status.thumbnail_url);
      const imageBuffer = Buffer.from(await imageResponse.arrayBuffer());
      const imagePath = path.join(outputDir, `${runId}.png`);
      await writeFile(imagePath, imageBuffer);

      let meshPath: string | undefined;
      if (status.model_urls?.glb) {
        const meshResponse = await fetch(status.model_urls.glb);
        const meshBuffer = Buffer.from(await meshResponse.arrayBuffer());
        meshPath = path.join(outputDir, `${runId}.glb`);
        await writeFile(meshPath, meshBuffer);
      }

      return {
        provider: "meshy",
        imagePath,
        imageUrl: status.thumbnail_url,
        meshPath
      };
    }
  }

  throw new RenderProviderError("Meshy task did not complete in time");
};

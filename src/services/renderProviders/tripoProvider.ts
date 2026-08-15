import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { RenderResult, SynthesizedPrompt } from "../../types.js";
import { RenderProviderError } from "./localSdProvider.js";

// Tripo3D's task API. Verify field names/paths against https://platform.tripo3d.ai/docs
// before relying on this in production — third-party API shapes drift over time.
const tripoBaseUrl = "https://api.tripo3d.ai/v2/openapi/task";
const pollIntervalMs = 3000;
const maxPollAttempts = 60;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export const renderWithTripo = async (
  synthesized: SynthesizedPrompt,
  outputDir: string,
  runId: string
): Promise<RenderResult> => {
  const apiKey = process.env.TRIPO_API_KEY;
  if (!apiKey) {
    throw new RenderProviderError("TRIPO_API_KEY is not set");
  }

  const createResponse = await fetch(tripoBaseUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({
      type: "text_to_model",
      prompt: synthesized.prompt,
      negative_prompt: synthesized.negativePrompt
    })
  });

  if (!createResponse.ok) {
    throw new RenderProviderError(`Tripo3D task creation failed (${createResponse.status})`);
  }

  const created = (await createResponse.json()) as { data?: { task_id?: string } };
  const taskId = created.data?.task_id;
  if (!taskId) {
    throw new RenderProviderError("Tripo3D did not return a task id");
  }

  for (let attempt = 0; attempt < maxPollAttempts; attempt += 1) {
    await sleep(pollIntervalMs);
    const statusResponse = await fetch(`${tripoBaseUrl}/${taskId}`, {
      headers: { Authorization: `Bearer ${apiKey}` }
    });
    if (!statusResponse.ok) {
      throw new RenderProviderError(`Tripo3D task status check failed (${statusResponse.status})`);
    }
    const status = (await statusResponse.json()) as {
      data?: { status?: string; output?: { rendered_image?: string; pbr_model?: string } };
    };
    const data = status.data;

    if (data?.status === "failed" || data?.status === "banned") {
      throw new RenderProviderError(`Tripo3D task ended with status ${data.status}`);
    }

    if (data?.status === "success" && data.output?.rendered_image) {
      await mkdir(outputDir, { recursive: true });
      const imageResponse = await fetch(data.output.rendered_image);
      const imageBuffer = Buffer.from(await imageResponse.arrayBuffer());
      const imagePath = path.join(outputDir, `${runId}.png`);
      await writeFile(imagePath, imageBuffer);

      let meshPath: string | undefined;
      if (data.output.pbr_model) {
        const meshResponse = await fetch(data.output.pbr_model);
        const meshBuffer = Buffer.from(await meshResponse.arrayBuffer());
        meshPath = path.join(outputDir, `${runId}.glb`);
        await writeFile(meshPath, meshBuffer);
      }

      return {
        provider: "tripo",
        imagePath,
        imageUrl: data.output.rendered_image,
        meshPath
      };
    }
  }

  throw new RenderProviderError("Tripo3D task did not complete in time");
};

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import type { RenderResult, SynthesizedPrompt } from "../../types.js";

const sdBaseUrl = process.env.SD_BASE_URL ?? "http://127.0.0.1:7860";

export class RenderProviderError extends Error {}

export const renderWithLocalSd = async (
  synthesized: SynthesizedPrompt,
  outputDir: string,
  runId: string
): Promise<RenderResult> => {
  const response = await fetch(`${sdBaseUrl}/sdapi/v1/txt2img`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: `${synthesized.prompt}, ${synthesized.styleTags.join(", ")}, product render, studio lighting, turntable concept shot`,
      negative_prompt: synthesized.negativePrompt,
      steps: 25,
      width: 768,
      height: 768
    })
  });

  if (!response.ok) {
    throw new RenderProviderError(`local Stable Diffusion request failed (${response.status})`);
  }

  const payload = (await response.json()) as { images?: string[] };
  const image = payload.images?.[0];
  if (!image) {
    throw new RenderProviderError("local Stable Diffusion returned no image");
  }

  await mkdir(outputDir, { recursive: true });
  const imagePath = path.join(outputDir, `${runId}.png`);
  await writeFile(imagePath, Buffer.from(image, "base64"));

  return {
    provider: "local-sd",
    imagePath,
    imageUrl: `data:image/png;base64,${image}`
  };
};

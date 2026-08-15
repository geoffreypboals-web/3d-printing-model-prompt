import type { RenderResult, SynthesizedPrompt } from "../../types.js";
import { RenderProviderError, renderWithLocalSd } from "./localSdProvider.js";
import { renderWithMeshy } from "./meshyProvider.js";
import { renderWithTripo } from "./tripoProvider.js";

export { RenderProviderError };

export type RenderProviderName = "local-sd" | "meshy" | "tripo";

export const activeRenderProvider = (): RenderProviderName => {
  const configured = (process.env.RENDER_PROVIDER ?? "local-sd").toLowerCase();
  if (configured === "meshy" || configured === "tripo" || configured === "local-sd") {
    return configured;
  }
  return "local-sd";
};

export const render = async (
  synthesized: SynthesizedPrompt,
  outputDir: string,
  runId: string
): Promise<RenderResult> => {
  const provider = activeRenderProvider();
  switch (provider) {
    case "meshy":
      return renderWithMeshy(synthesized, outputDir, runId);
    case "tripo":
      return renderWithTripo(synthesized, outputDir, runId);
    default:
      return renderWithLocalSd(synthesized, outputDir, runId);
  }
};

import { execFile } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const scriptsDir = path.join(__dirname, "..", "..", "scripts", "blender");
const blenderBin = process.env.BLENDER_BIN ?? "blender";
const timeoutMs = Number(process.env.BLENDER_TIMEOUT_MS ?? 120000);

export class BlenderError extends Error {}

const runBlenderScript = async (scriptName: string, args: string[]): Promise<string> => {
  const scriptPath = path.join(scriptsDir, scriptName);
  try {
    const { stdout } = await execFileAsync(
      blenderBin,
      ["--background", "--factory-startup", "--python", scriptPath, "--", ...args],
      { timeout: timeoutMs, maxBuffer: 16 * 1024 * 1024 }
    );
    if (!stdout.includes("RESULT_OK")) {
      throw new BlenderError(`blender script did not report success: ${scriptName}\n${stdout.slice(-2000)}`);
    }
    return stdout;
  } catch (error) {
    if (error instanceof BlenderError) {
      throw error;
    }
    const details = error instanceof Error ? error.message : String(error);
    throw new BlenderError(`blender script failed: ${scriptName}: ${details}`);
  }
};

export type SolidifyDirection = "inside" | "outside";

/**
 * Applies a Solidify modifier and writes both the modified mesh and an
 * auto-framed preview render. See scripts/blender/solidify.py for the
 * offset-direction mapping (validated against a known test sphere before
 * this feature was wired up).
 */
export const applySolidify = async (
  inputGlbPath: string,
  outputGlbPath: string,
  outputPreviewPath: string,
  thicknessMm: number,
  direction: SolidifyDirection
): Promise<void> => {
  await runBlenderScript("solidify.py", [
    inputGlbPath,
    outputGlbPath,
    outputPreviewPath,
    String(thicknessMm),
    direction
  ]);
};

/**
 * Estimates the minimum wall thickness of a mesh via binary search over
 * Blender's 3D Print Toolbox thin-wall check (see scripts/blender/analyze_thickness.py,
 * validated against a known 0.3mm test shell before this feature was wired up).
 */
export const analyzeMinThickness = async (inputGlbPath: string): Promise<number> => {
  const stdout = await runBlenderScript("analyze_thickness.py", [inputGlbPath]);
  const match = stdout.match(/MIN_THICKNESS_MM=([\d.]+)/);
  if (!match) {
    throw new BlenderError(`could not parse thickness result from blender output\n${stdout.slice(-2000)}`);
  }
  return Number(match[1]);
};

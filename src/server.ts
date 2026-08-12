import "dotenv/config";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import { interviewRouter } from "./routes/interview.js";
import { renderRouter } from "./routes/render.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.join(__dirname, "..", "public");
const outputDir = process.env.OUTPUT_DIR ?? "output";
const port = Number(process.env.PORT ?? 4100);

const app = express();
app.use(express.json({ limit: "2mb" }));

app.get("/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.use("/api/interview", interviewRouter);
app.use("/api/render", renderRouter);
app.use("/output", express.static(outputDir));
app.use(express.static(publicDir));

app.listen(port, () => {
  console.log(`mesh-prompt-interviewer listening on http://0.0.0.0:${port}`);
});

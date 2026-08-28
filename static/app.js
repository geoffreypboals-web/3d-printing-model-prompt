// Project: 3D Printing Model Prompt
// File: /home/user/3d-printing-model-prompt/static/app.js
// Description: The one form page's client-side logic -- posts to
//     /api/generate and renders the result or a clear error, no
//     framework, no build step.
// Inputs: user input from #text-input / #size-input.
// Outputs: DOM updates to #status / #result.
// Troubleshooting:
//     - If nothing happens on click, open the browser console -- a
//       network error there means the container isn't reachable, not a
//       bug in this script (this file has no retry/polling logic at all).
const textInput = document.getElementById("text-input");
const sizeInput = document.getElementById("size-input");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const button = document.getElementById("generate-btn");

async function generate() {
  const text = textInput.value.trim();
  if (!text) {
    statusEl.textContent = "Describe the object you want printed first.";
    return;
  }

  const body = { text };
  const sizeRaw = sizeInput.value.trim();
  if (sizeRaw) body.max_size_mm = Number(sizeRaw);

  button.disabled = true;
  statusEl.textContent = "Generating... this can take a little while on a local model.";
  resultEl.hidden = true;

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      statusEl.textContent = data.detail || `Request failed (${res.status}).`;
      return;
    }
    statusEl.textContent = "";
    resultEl.textContent = data.result;
    resultEl.hidden = false;
  } catch (err) {
    statusEl.textContent = `Network error: ${err}`;
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", generate);

const chatEl = document.getElementById("chat");
const composerEl = document.getElementById("composer");
const inputEl = document.getElementById("composer-input");
const resultEl = document.getElementById("result");
const resultPromptEl = document.getElementById("result-prompt");
const resultNegativeEl = document.getElementById("result-negative");
const resultTagsEl = document.getElementById("result-tags");
const copyButton = document.getElementById("copy-prompt");
const renderButton = document.getElementById("render-button");
const renderOutputEl = document.getElementById("render-output");
const renderImageEl = document.getElementById("render-image");
const renderMetaEl = document.getElementById("render-meta");

let history = [];
let latestAnswers = {};
let latestSynthesized = null;
let interviewDone = false;

const addBubble = (role, text) => {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  chatEl.appendChild(bubble);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
};

const setComposerEnabled = (enabled) => {
  inputEl.disabled = !enabled;
  composerEl.querySelector("button").disabled = !enabled;
};

const requestTurn = async () => {
  setComposerEnabled(false);
  const thinking = addBubble("assistant", "…");
  try {
    const response = await fetch("/api/interview/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history })
    });
    const data = await response.json();
    thinking.remove();

    if (!response.ok) {
      addBubble("error", data.error ?? "Something went wrong talking to the local model.");
      setComposerEnabled(true);
      return;
    }

    addBubble("assistant", data.reply);
    history.push({ role: "assistant", content: JSON.stringify(data) });
    latestAnswers = data.answers ?? latestAnswers;

    if (data.status === "ready") {
      interviewDone = true;
      await finalizePrompt();
      return;
    }

    setComposerEnabled(true);
    inputEl.focus();
  } catch (error) {
    thinking.remove();
    addBubble("error", "Could not reach the server.");
    setComposerEnabled(true);
  }
};

const finalizePrompt = async () => {
  addBubble("assistant", "Great, I have what I need. Writing the final prompt…");
  try {
    const response = await fetch("/api/interview/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers: latestAnswers })
    });
    const data = await response.json();
    if (!response.ok) {
      addBubble("error", data.error ?? "Could not synthesize the final prompt.");
      return;
    }
    latestSynthesized = data;
    resultPromptEl.textContent = data.prompt;
    resultNegativeEl.textContent = data.negativePrompt || "(none)";
    resultTagsEl.textContent = (data.styleTags || []).join(", ");
    resultEl.hidden = false;
    resultEl.scrollIntoView({ behavior: "smooth" });
  } catch (error) {
    addBubble("error", "Could not reach the server to finalize the prompt.");
  }
};

composerEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = inputEl.value.trim();
  if (!text || interviewDone) {
    return;
  }
  addBubble("user", text);
  history.push({ role: "user", content: text });
  inputEl.value = "";
  requestTurn();
});

copyButton.addEventListener("click", async () => {
  if (!latestSynthesized) return;
  await navigator.clipboard.writeText(latestSynthesized.prompt);
  copyButton.textContent = "Copied!";
  setTimeout(() => (copyButton.textContent = "Copy prompt"), 1500);
});

renderButton.addEventListener("click", async () => {
  if (!latestSynthesized) return;
  renderButton.disabled = true;
  renderButton.textContent = "Rendering…";
  try {
    const response = await fetch("/api/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(latestSynthesized)
    });
    const data = await response.json();
    if (!response.ok) {
      renderMetaEl.textContent = data.error ?? "Rendering failed.";
      renderOutputEl.hidden = false;
      return;
    }
    renderImageEl.src = data.imageUrl;
    renderMetaEl.textContent = `Provider: ${data.provider}${data.meshPath ? ` — mesh saved to ${data.meshPath}` : ""}`;
    renderOutputEl.hidden = false;
  } catch (error) {
    renderMetaEl.textContent = "Could not reach the server to render the preview.";
    renderOutputEl.hidden = false;
  } finally {
    renderButton.disabled = false;
    renderButton.textContent = "Render preview image";
  }
});

// Kick off the interview with an empty history so the model opens with its greeting.
requestTurn();

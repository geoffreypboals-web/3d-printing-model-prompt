export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  role: ChatRole;
  content: string;
};

export type InterviewAnswers = Record<string, string>;

export type InterviewTurnResult = {
  status: "interviewing" | "ready";
  reply: string;
  answers: InterviewAnswers;
};

export type SynthesizedPrompt = {
  prompt: string;
  negativePrompt: string;
  styleTags: string[];
};

export type RenderResult = {
  provider: "local-sd" | "meshy" | "tripo";
  imagePath: string;
  imageUrl: string;
  meshPath?: string;
};

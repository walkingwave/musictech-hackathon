import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const ASK_TOOLS = ["read", "grep", "find", "ls", "ask_web_search", "ask_web_fetch"];
const MAX_PAGE_CHARS = 30_000;

type AskModeState = {
  active: boolean;
  previousTools: string[];
};

function latestAskState(ctx: ExtensionContext): AskModeState | undefined {
  let state: AskModeState | undefined;
  for (const entry of ctx.sessionManager.getBranch()) {
    if (entry.type === "custom" && entry.customType === "ask-mode") {
      const data = entry.data as Partial<AskModeState> | undefined;
      if (typeof data?.active === "boolean" && Array.isArray(data.previousTools)) {
        state = { active: data.active, previousTools: data.previousTools };
      }
    }
  }
  return state;
}

function plainText(html: string): string {
  return html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&#39;/gi, "'")
    .replace(/&quot;/gi, '"')
    .replace(/\s+/g, " ")
    .trim();
}

async function fetchText(url: string, signal?: AbortSignal): Promise<{ url: string; text: string }> {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error("Provide a valid http:// or https:// URL.");
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("Only http:// and https:// URLs are allowed.");
  }

  const response = await fetch(parsed, {
    signal,
    headers: { "User-Agent": "pi-ask-research/1.0" },
    redirect: "follow",
  });
  if (!response.ok) throw new Error(`Request failed: HTTP ${response.status}`);

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("text") && !contentType.includes("json") && !contentType.includes("xml")) {
    throw new Error(`Unsupported response type: ${contentType || "unknown"}`);
  }

  const raw = await response.text();
  const text = contentType.includes("html") || contentType.includes("xml") ? plainText(raw) : raw.trim();
  return { url: response.url, text: text.slice(0, MAX_PAGE_CHARS) };
}

export default function askExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "ask_web_search",
    label: "Ask Web Search",
    description: "Search the public web through DuckDuckGo and return a small list of result titles, URLs, and snippets. This tool never modifies local files.",
    promptSnippet: "Search the public web for sources",
    promptGuidelines: [
      "Use ask_web_search for online research; prefer primary, scholarly, government, and official sources, then inspect important results with ask_web_fetch before citing them.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "Web-search query" }),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 10, description: "Maximum results; defaults to 5" })),
    }),
    async execute(_toolCallId, params, signal) {
      const searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(params.query)}`;
      const response = await fetch(searchUrl, {
        signal,
        headers: { "User-Agent": "pi-ask-research/1.0" },
      });
      if (!response.ok) throw new Error(`Search failed: HTTP ${response.status}`);
      const html = await response.text();
      const results = [...html.matchAll(/<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)]
        .map((match) => {
          const href = match[1].replace(/&amp;/g, "&");
          const redirected = new URL(href, response.url).searchParams.get("uddg");
          return { title: plainText(match[2]), url: redirected ?? new URL(href, response.url).toString() };
        })
        .filter((result, index, list) => result.url.startsWith("http") && list.findIndex((item) => item.url === result.url) === index)
        .slice(0, params.limit ?? 5);
      const text = plainText(html).slice(0, 8_000);

      return {
        content: [{
          type: "text",
          text: results.length
            ? `Search results for ${JSON.stringify(params.query)}:\n${results.map((result, index) => `${index + 1}. ${result.title || result.url}\n   ${result.url}`).join("\n")}\n\nSearch-page text excerpt:\n${text}`
            : `No parseable result URLs found for ${JSON.stringify(params.query)}. Search-page text:\n${text}`,
        }],
        details: { query: params.query, source: response.url, results },
      };
    },
  });

  pi.registerTool({
    name: "ask_web_fetch",
    label: "Ask Web Fetch",
    description: "Fetch and extract readable text from one public web page for source verification. This tool never modifies local files; output is capped at 30,000 characters.",
    promptSnippet: "Fetch a public web page for source verification",
    promptGuidelines: [
      "Use ask_web_fetch to verify significant online claims before citing them, and include the final URL in the answer's references.",
    ],
    parameters: Type.Object({
      url: Type.String({ description: "Public http:// or https:// page URL" }),
    }),
    async execute(_toolCallId, params, signal) {
      const { url, text } = await fetchText(params.url, signal);
      return {
        content: [{ type: "text", text: `Source URL: ${url}\n\n${text || "[No readable text found]"}` }],
        details: { url, truncated: text.length >= MAX_PAGE_CHARS },
      };
    },
  });

  pi.registerCommand("ask", {
    description: "Enter read-only question mode: research online, inspect code, and answer without modifying files",
    handler: async (args, ctx) => {
      const question = args.trim();
      if (!question) {
        if (ctx.hasUI) ctx.ui.notify("Usage: /ask <question>", "warning");
        return;
      }

      const existingState = latestAskState(ctx);
      const previousTools = existingState?.active
        ? existingState.previousTools
        : pi.getActiveTools().filter((name) => !ASK_TOOLS.includes(name));
      pi.setActiveTools(ASK_TOOLS);
      pi.appendEntry<AskModeState>("ask-mode", { active: true, previousTools });

      if (ctx.hasUI) ctx.ui.notify("Ask mode enabled: read-only tools only.", "info");
      pi.sendUserMessage([
        "Answer the following question in read-only Ask mode:",
        question,
        "",
        "You must not modify files, run shell commands, commit, install packages, or change settings.",
        "Use read/grep/find/ls to inspect relevant code. For current factual claims, use ask_web_search and ask_web_fetch; prioritize primary, scholarly, government, or official sources. Verify important claims where practical.",
        "Give a direct answer, distinguish evidence from inference, name uncertainty, and add linked sources for online claims.",
      ].join("\n"));
    },
  });

  pi.registerCommand("ask-off", {
    description: "Leave read-only Ask mode and restore the tools that were active before it",
    handler: async (_args, ctx) => {
      const state = latestAskState(ctx);
      const available = new Set(pi.getAllTools().map((tool) => tool.name));
      const restore = (state?.previousTools ?? []).filter((name) => available.has(name));
      pi.setActiveTools(restore);
      pi.appendEntry<AskModeState>("ask-mode", { active: false, previousTools: restore });
      if (ctx.hasUI) ctx.ui.notify("Ask mode disabled; prior tools restored.", "info");
    },
  });

  pi.on("session_start", (_event, ctx) => {
    const state = latestAskState(ctx);
    if (state?.active) pi.setActiveTools(ASK_TOOLS);
  });
}

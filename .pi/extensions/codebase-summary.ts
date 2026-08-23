import { readdir, readFile, stat } from "node:fs/promises";
import { basename, relative, resolve, sep } from "node:path";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const EXCLUDED_DIRECTORIES = new Set([
  ".git",
  ".next",
  ".venv",
  "coverage",
  "dist",
  "build",
  "node_modules",
  "__pycache__",
]);
const EXCLUDED_FILES = new Set([".env", ".env.local", ".env.production", ".env.development"]);
const SOURCE_EXTENSIONS = new Set([
  ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".css", ".json", ".toml", ".yaml", ".yml", ".md",
]);
const MAX_FILES = 250;
const MAX_DEPTH = 8;
const MAX_README_CHARS = 8_000;
const MAX_EXCERPT_CHARS = 500;

type InventoryOptions = {
  path?: string;
  includeDocs?: boolean;
};

function isInside(root: string, candidate: string): boolean {
  const rel = relative(root, candidate);
  return rel === "" || (!rel.startsWith(`..${sep}`) && rel !== ".." && !rel.includes(`${sep}..${sep}`));
}

function isIncludedFile(name: string): boolean {
  if (EXCLUDED_FILES.has(name) || name.startsWith(".env.")) return false;
  const dot = name.lastIndexOf(".");
  return dot >= 0 && SOURCE_EXTENSIONS.has(name.slice(dot));
}

async function collectFiles(root: string, directory: string, files: string[], depth = 0): Promise<void> {
  if (files.length >= MAX_FILES || depth > MAX_DEPTH) return;
  const entries = await readdir(directory, { withFileTypes: true });

  for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
    if (files.length >= MAX_FILES) return;
    if (entry.isSymbolicLink()) continue;
    const absolute = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      if (!EXCLUDED_DIRECTORIES.has(entry.name)) await collectFiles(root, absolute, files, depth + 1);
    } else if (entry.isFile() && isIncludedFile(entry.name)) {
      files.push(relative(root, absolute));
    }
  }
}

async function readExcerpt(absolutePath: string, maxChars: number): Promise<string> {
  try {
    const content = await readFile(absolutePath, "utf8");
    return content.slice(0, maxChars).trim();
  } catch {
    return "[Unreadable text file]";
  }
}

async function buildInventory(ctx: ExtensionContext, options: InventoryOptions): Promise<string> {
  const root = resolve(ctx.cwd);
  const requestedPath = options.path?.trim() || ".";
  const target = resolve(root, requestedPath.replace(/^@/, ""));
  if (!isInside(root, target)) throw new Error("The requested path must stay inside the current project.");

  const targetStat = await stat(target);
  const files: string[] = [];
  if (targetStat.isDirectory()) {
    await collectFiles(root, target, files);
  } else if (targetStat.isFile() && isIncludedFile(basename(target))) {
    files.push(relative(root, target));
  } else {
    throw new Error("The requested path is not an analyzable source or documentation file.");
  }

  const readmePath = ["README.md", "PLAN.md"].find((name) => files.includes(name));
  const readme = readmePath && options.includeDocs !== false
    ? await readExcerpt(resolve(root, readmePath), MAX_README_CHARS)
    : "";

  const sourceFiles = files.filter((file) => !file.endsWith(".md") && !file.endsWith(".json"));
  const excerpts = await Promise.all(
    sourceFiles.slice(0, 40).map(async (file) => {
      const excerpt = await readExcerpt(resolve(root, file), MAX_EXCERPT_CHARS);
      return `### ${file}\n${excerpt || "[Empty file]"}`;
    }),
  );

  const limitNote = files.length >= MAX_FILES ? `\nFile list capped at ${MAX_FILES} files.` : "";
  return [
    `# Codebase inventory: ${relative(root, target) || "."}`,
    "",
    "## Included files",
    ...files.map((file) => `- ${file}`),
    limitNote,
    readme ? `\n## Project documentation (${readmePath})\n${readme}` : "",
    excerpts.length > 0 ? `\n## Source excerpts (first ${Math.min(sourceFiles.length, 40)} files)\n${excerpts.join("\n\n")}` : "",
    "\n## Notes",
    "- Generated/vendor directories and .env files are excluded.",
    "- Excerpts are orientation aids, not a replacement for reading complete source files.",
  ].filter(Boolean).join("\n");
}

export default function codebaseSummaryExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "codebase_inventory",
    label: "Codebase Inventory",
    description: "Build a bounded, privacy-conscious inventory of the current project's source files, documentation, and short source excerpts. Excludes .env files, dependencies, build output, and virtual environments.",
    promptSnippet: "Inspect the current project's code structure and source excerpts",
    promptGuidelines: [
      "Use codebase_inventory to orient yourself before summarizing a project or identifying its implemented features; read complete relevant files before making detailed claims.",
    ],
    parameters: Type.Object({
      path: Type.Optional(Type.String({ description: "Project-relative file or directory to inspect; defaults to the project root." })),
      includeDocs: Type.Optional(Type.Boolean({ description: "Include README.md or PLAN.md when present; defaults to true." })),
    }),
    async execute(_toolCallId, params, signal, onUpdate, ctx) {
      if (signal?.aborted) return { content: [{ type: "text", text: "Cancelled." }], details: {} };
      onUpdate?.({ content: [{ type: "text", text: "Building codebase inventory…" }] });
      const inventory = await buildInventory(ctx, params);
      return {
        content: [{ type: "text", text: inventory }],
        details: { path: params.path ?? ".", includeDocs: params.includeDocs ?? true },
      };
    },
  });

  pi.registerCommand("code-summary", {
    description: "Analyze the current codebase (or a project-relative path) and summarize architecture and implemented features",
    handler: async (args, ctx) => {
      const path = args.trim() || ".";
      try {
        const inventory = await buildInventory(ctx, { path, includeDocs: true });
        if (ctx.hasUI) ctx.ui.notify("Codebase inventory prepared; requesting analysis…", "info");
        pi.sendUserMessage([
          "Analyze the current codebase using the inventory below.",
          "Provide: (1) a concise product purpose, (2) architecture and data flow, (3) implemented main features, (4) key entry points/modules, (5) dependencies or external services, and (6) gaps, assumptions, and notable risks.",
          "Treat the inventory as orientation only: read complete relevant source files before asserting implementation details. Do not modify files.",
          "",
          inventory,
        ].join("\n"));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (ctx.hasUI) ctx.ui.notify(`Code summary failed: ${message}`, "error");
        else console.error(`Code summary failed: ${message}`);
      }
    },
  });
}

import { marked } from "marked";
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

marked.setOptions({
  gfm: true,
  breaks: true,
});

const turndownService = new TurndownService({
  headingStyle: "atx",
  bulletListMarker: "-",
  codeBlockStyle: "fenced",
  emDelimiter: "*",
  strongDelimiter: "**",
});

turndownService.use(gfm);

export function normalizeEditorHtmlForMarkdown(html: string): string {
  if (typeof document === "undefined") {
    return html;
  }

  const container = document.createElement("div");
  container.innerHTML = html;

  // Tiptap table resize markup causes Turndown to emit raw HTML instead of GFM.
  container.querySelectorAll("colgroup, col").forEach((node) => node.remove());

  container.querySelectorAll("table, thead, tbody, tfoot, tr, th, td").forEach((node) => {
    node.removeAttribute("style");
  });

  container.querySelectorAll("th, td").forEach((cell) => {
    const elementChildren = Array.from(cell.children);
    if (
      elementChildren.length > 0 &&
      elementChildren.every((child) => child.tagName === "P")
    ) {
      cell.innerHTML = elementChildren
        .map((child) => child.innerHTML.trim())
        .filter(Boolean)
        .join("<br>");
    }
  });

  return container.innerHTML;
}

export function canonicalizeMarkdownForEditor(markdown: string): string {
  const html = marked.parse(markdown ?? "") as string;
  const normalizedHtml = normalizeEditorHtmlForMarkdown(html);

  return htmlToEditorMarkdown(normalizedHtml);
}

export function htmlToEditorMarkdown(html: string): string {
  return turndownService
    .turndown(html ?? "")
    .replace(/\n{3,}/g, "\n\n")
    .trimEnd();
}

export function isMarkdownRoundTripStable(markdown: string): boolean {
  return canonicalizeMarkdownForEditor(markdown) === (markdown ?? "").trimEnd();
}

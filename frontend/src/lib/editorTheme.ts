import * as monaco from "monaco-editor";

// User-configurable syntax token colors for the editor. The fixed editor chrome
// (background, gutter, selection…) stays tied to the warm palette; only these
// token hues are exposed for tweaking, persisted in localStorage, applied live.

export interface EditorToken {
  key: string;
  label: string;
  default: string;
}

export const EDITOR_TOKENS: EditorToken[] = [
  { key: "text", label: "Text & identifiers", default: "#ece7df" },
  { key: "keyword", label: "Keywords", default: "#e0a44b" },
  { key: "string", label: "Strings", default: "#9bbf80" },
  { key: "comment", label: "Comments", default: "#7d7468" },
  { key: "number", label: "Numbers", default: "#d98e73" },
  { key: "type", label: "Types & classes", default: "#e8c98a" },
  { key: "operator", label: "Operators & punctuation", default: "#b8ad9d" },
];

export type EditorColors = Record<string, string>;

export const DEFAULT_EDITOR_COLORS: EditorColors = Object.fromEntries(EDITOR_TOKENS.map((t) => [t.key, t.default]));

const STORAGE_KEY = "cadcode.editorColors";

export function loadEditorColors(): EditorColors {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_EDITOR_COLORS, ...(JSON.parse(raw) as EditorColors) };
  } catch {
    /* ignore malformed storage */
  }
  return { ...DEFAULT_EDITOR_COLORS };
}

export function saveEditorColors(c: EditorColors): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(c));
  } catch {
    /* ignore */
  }
}

const hex = (c: string) => c.replace("#", "");

function buildTheme(c: EditorColors): monaco.editor.IStandaloneThemeData {
  return {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "", foreground: hex(c.text) },
      { token: "comment", foreground: hex(c.comment), fontStyle: "italic" },
      { token: "keyword", foreground: hex(c.keyword) },
      { token: "keyword.flow", foreground: hex(c.keyword) },
      { token: "operator", foreground: hex(c.operator) },
      { token: "delimiter", foreground: hex(c.operator) },
      { token: "string", foreground: hex(c.string) },
      { token: "string.escape", foreground: hex(c.number) },
      { token: "number", foreground: hex(c.number) },
      { token: "number.hex", foreground: hex(c.number) },
      { token: "type", foreground: hex(c.type) },
      { token: "type.identifier", foreground: hex(c.type) },
      { token: "identifier", foreground: hex(c.text) },
      { token: "tag", foreground: hex(c.keyword) },
      { token: "attribute.name", foreground: hex(c.type) },
    ],
    colors: {
      "editor.background": "#080706",
      "editor.foreground": "#ece7df",
      "editorLineNumber.foreground": "#544d43",
      "editorLineNumber.activeForeground": "#a59c90",
      "editor.lineHighlightBackground": "#15120c",
      "editor.lineHighlightBorder": "#00000000",
      "editor.selectionBackground": "#3a2f1a",
      "editor.inactiveSelectionBackground": "#241d12",
      "editor.selectionHighlightBackground": "#2a2216",
      "editorCursor.foreground": "#d8a657",
      "editor.findMatchBackground": "#5a4420",
      "editor.findMatchHighlightBackground": "#3a2f1a",
      "editorIndentGuide.background1": "#1e1b16",
      "editorIndentGuide.activeBackground1": "#2f2a20",
      "editorWhitespace.foreground": "#2a2620",
      "editorGutter.background": "#080706",
      "editorError.foreground": "#e0775e",
      "editorWarning.foreground": "#e0a44b",
      "editorBracketMatch.background": "#241d12",
      "editorBracketMatch.border": "#d8a65766",
      "scrollbarSlider.background": "#3a342a66",
      "scrollbarSlider.hoverBackground": "#3a342aaa",
      "scrollbarSlider.activeBackground": "#d8a65799",
      "editorWidget.background": "#17150f",
      "editorWidget.border": "#2a2620",
      "editorSuggestWidget.background": "#17150f",
      "editorSuggestWidget.border": "#2a2620",
      "editorSuggestWidget.selectedBackground": "#211d16",
      "editorHoverWidget.background": "#17150f",
      "editorHoverWidget.border": "#2a2620",
      "input.background": "#080706",
      focusBorder: "#d8a657",
    },
  };
}

/** (Re)define the `cadcode` theme from these colors and apply it live. */
export function applyEditorTheme(c: EditorColors): void {
  monaco.editor.defineTheme("cadcode", buildTheme(c));
  monaco.editor.setTheme("cadcode");
}

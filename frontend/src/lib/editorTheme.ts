import * as monaco from "monaco-editor";

// The editor theme is fully configurable: pick a preset (Dracula, VS Code, …) or
// tweak individual syntax token colors. Everything persists in localStorage and
// applies to Monaco live. A theme = 7 syntax token colors + bg/cursor/selection.

export interface EditorToken {
  key: string;
  label: string;
}

// The individually-pickable syntax tokens (order = UI order).
export const EDITOR_TOKENS: EditorToken[] = [
  { key: "text", label: "Text & identifiers" },
  { key: "keyword", label: "Keywords" },
  { key: "string", label: "Strings" },
  { key: "comment", label: "Comments" },
  { key: "number", label: "Numbers" },
  { key: "type", label: "Types & classes" },
  { key: "operator", label: "Operators & punctuation" },
];

export type EditorColors = Record<string, string>;

export interface EditorPreset {
  key: string;
  label: string;
  colors: EditorColors;
}

// Curated presets — the warm `cadcode` default plus popular editor themes.
export const EDITOR_PRESETS: EditorPreset[] = [
  {
    key: "cadcode",
    label: "cadcode (warm)",
    colors: {
      text: "#ece7df",
      keyword: "#e0a44b",
      string: "#9bbf80",
      comment: "#7d7468",
      number: "#d98e73",
      type: "#e8c98a",
      operator: "#b8ad9d",
      bg: "#080706",
      cursor: "#d8a657",
      selection: "#3a2f1a",
    },
  },
  {
    key: "dracula",
    label: "Dracula",
    colors: {
      text: "#f8f8f2",
      keyword: "#ff79c6",
      string: "#f1fa8c",
      comment: "#6272a4",
      number: "#bd93f9",
      type: "#8be9fd",
      operator: "#ff79c6",
      bg: "#282a36",
      cursor: "#f8f8f0",
      selection: "#44475a",
    },
  },
  {
    key: "vscode",
    label: "VS Code Dark+",
    colors: {
      text: "#d4d4d4",
      keyword: "#569cd6",
      string: "#ce9178",
      comment: "#6a9955",
      number: "#b5cea8",
      type: "#4ec9b0",
      operator: "#d4d4d4",
      bg: "#1e1e1e",
      cursor: "#aeafad",
      selection: "#264f78",
    },
  },
  {
    key: "monokai",
    label: "Monokai",
    colors: {
      text: "#f8f8f2",
      keyword: "#f92672",
      string: "#e6db74",
      comment: "#75715e",
      number: "#ae81ff",
      type: "#66d9ef",
      operator: "#f92672",
      bg: "#272822",
      cursor: "#f8f8f0",
      selection: "#49483e",
    },
  },
  {
    key: "solarized",
    label: "Solarized Dark",
    colors: {
      text: "#93a1a1",
      keyword: "#859900",
      string: "#2aa198",
      comment: "#586e75",
      number: "#d33682",
      type: "#b58900",
      operator: "#859900",
      bg: "#002b36",
      cursor: "#93a1a1",
      selection: "#073642",
    },
  },
  {
    key: "github",
    label: "GitHub Dark",
    colors: {
      text: "#c9d1d9",
      keyword: "#ff7b72",
      string: "#a5d6ff",
      comment: "#8b949e",
      number: "#79c0ff",
      type: "#ffa657",
      operator: "#ff7b72",
      bg: "#0d1117",
      cursor: "#c9d1d9",
      selection: "#264466",
    },
  },
];

export const DEFAULT_EDITOR_COLORS: EditorColors = { ...EDITOR_PRESETS[0].colors };

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
    // Chrome derives from the preset so each theme reads coherently.
    colors: {
      "editor.background": c.bg,
      "editor.foreground": c.text,
      "editorLineNumber.foreground": `${c.comment}aa`,
      "editorLineNumber.activeForeground": c.text,
      "editor.lineHighlightBackground": `${c.selection}55`,
      "editor.lineHighlightBorder": "#00000000",
      "editor.selectionBackground": c.selection,
      "editor.inactiveSelectionBackground": `${c.selection}99`,
      "editor.selectionHighlightBackground": `${c.selection}77`,
      "editorCursor.foreground": c.cursor,
      "editorIndentGuide.background1": `${c.comment}33`,
      "editorIndentGuide.activeBackground1": `${c.comment}66`,
      "editorWhitespace.foreground": `${c.comment}44`,
      "editorGutter.background": c.bg,
      "editorError.foreground": "#e0775e",
      "editorWarning.foreground": "#e0a44b",
      "editorBracketMatch.background": `${c.selection}88`,
      "editorBracketMatch.border": `${c.cursor}88`,
      "scrollbarSlider.background": `${c.text}22`,
      "scrollbarSlider.hoverBackground": `${c.text}44`,
      "scrollbarSlider.activeBackground": `${c.cursor}88`,
      "editorWidget.background": c.bg,
      "editorWidget.border": `${c.text}20`,
      "editorSuggestWidget.background": c.bg,
      "editorSuggestWidget.border": `${c.text}20`,
      "editorSuggestWidget.selectedBackground": c.selection,
      "editorHoverWidget.background": c.bg,
      "editorHoverWidget.border": `${c.text}20`,
      "input.background": c.bg,
      focusBorder: c.cursor,
    },
  };
}

/** (Re)define the `cadcode` theme from these colors and apply it live. */
export function applyEditorTheme(c: EditorColors): void {
  monaco.editor.defineTheme("cadcode", buildTheme(c));
  monaco.editor.setTheme("cadcode");
}

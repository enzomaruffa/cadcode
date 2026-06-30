// Bundle Monaco locally (no CDN) and hand it to @monaco-editor/react.
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker: () => new editorWorker(),
};

loader.config({ monaco });

// Bespoke warm-editorial theme matching the app's design tokens.
// Tuned for legibility on the editor's #080706 (--bg-pane) background.
monaco.editor.defineTheme("cadcode", {
  base: "vs-dark",
  inherit: true,
  rules: [
    { token: "", foreground: "ece7df" },
    { token: "comment", foreground: "7d7468", fontStyle: "italic" },
    { token: "keyword", foreground: "e0a44b" },
    { token: "keyword.flow", foreground: "e0a44b" },
    { token: "operator", foreground: "b8ad9d" },
    { token: "delimiter", foreground: "b8ad9d" },
    { token: "string", foreground: "9bbf80" },
    { token: "string.escape", foreground: "d98e73" },
    { token: "number", foreground: "d98e73" },
    { token: "number.hex", foreground: "d98e73" },
    { token: "type", foreground: "e8c98a" },
    { token: "type.identifier", foreground: "e8c98a" },
    { token: "identifier", foreground: "ece7df" },
    { token: "tag", foreground: "e0a44b" },
    { token: "attribute.name", foreground: "e8c98a" },
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
});

// Expose for debugging / E2E (read-only handle to the bundled Monaco).
(self as unknown as { monaco: typeof monaco }).monaco = monaco;

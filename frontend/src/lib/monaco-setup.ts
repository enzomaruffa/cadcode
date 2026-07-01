// Bundle Monaco locally (no CDN) and hand it to @monaco-editor/react.
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import { applyEditorTheme, loadEditorColors } from "./editorTheme";
import "./monaco-completions"; // build123d autocomplete provider

(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker: () => new editorWorker(),
};

loader.config({ monaco });

// Define + apply the bespoke `cadcode` theme using any persisted color overrides.
applyEditorTheme(loadEditorColors());

// Expose for debugging / E2E (read-only handle to the bundled Monaco).
(self as unknown as { monaco: typeof monaco }).monaco = monaco;

// Bundle Monaco locally (no CDN) and hand it to @monaco-editor/react.
import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

(self as unknown as { MonacoEnvironment: monaco.Environment }).MonacoEnvironment = {
  getWorker: () => new editorWorker(),
};

loader.config({ monaco });

// Expose for debugging / E2E (read-only handle to the bundled Monaco).
(self as unknown as { monaco: typeof monaco }).monaco = monaco;

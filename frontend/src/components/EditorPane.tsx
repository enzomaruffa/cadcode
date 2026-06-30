import Editor, { type OnMount } from "@monaco-editor/react";
import { useEffect, useRef } from "react";
import type * as Monaco from "monaco-editor";
import { useStore } from "../lib/store";
import "../lib/monaco-setup";

export function EditorPane() {
  const source = useStore((s) => s.source);
  const setSource = useStore((s) => s.setSource);
  const runNow = useStore((s) => s.runNow);
  const error = useStore((s) => s.error);

  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof Monaco | null>(null);

  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    // ⌘/Ctrl+Enter forces a run even if a debounce is pending.
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => runNow());
  };

  // Error squiggles on the offending line (plan §11 M1).
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;
    const model = editor.getModel();
    if (!model) return;

    if (error && error.line) {
      const line = Math.min(Math.max(error.line, 1), model.getLineCount());
      monaco.editor.setModelMarkers(model, "cad", [
        {
          startLineNumber: line,
          endLineNumber: line,
          startColumn: 1,
          endColumn: model.getLineMaxColumn(line),
          message: error.message,
          severity: monaco.MarkerSeverity.Error,
        },
      ]);
    } else {
      monaco.editor.setModelMarkers(model, "cad", []);
    }
  }, [error]);

  return (
    <Editor
      className="editor"
      language="python"
      theme="vs-dark"
      value={source}
      onChange={(v) => setSource(v ?? "")}
      onMount={onMount}
      options={{
        fontSize: 13,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 4,
        renderWhitespace: "none",
        smoothScrolling: true,
      }}
    />
  );
}

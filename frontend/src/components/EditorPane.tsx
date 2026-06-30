import Editor, { type OnMount } from "@monaco-editor/react";
import { useEffect, useRef, useState } from "react";
import type * as Monaco from "monaco-editor";
import { useStore } from "../lib/store";
import "../lib/monaco-setup";

interface InlineState {
  top: number;
  left: number;
  selection: string;
}

export function EditorPane() {
  const source = useStore((s) => s.source);
  const setSource = useStore((s) => s.setSource);
  const runNow = useStore((s) => s.runNow);
  const sendChat = useStore((s) => s.sendChat);
  const error = useStore((s) => s.error);
  const setActiveLine = useStore((s) => s.setActiveLine);
  const revealLine = useStore((s) => s.revealLine);

  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof Monaco | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const [inline, setInline] = useState<InlineState | null>(null);
  const [draft, setDraft] = useState("");

  const openInline = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const pos = editor.getPosition();
    const model = editor.getModel();
    if (!pos || !model) return;
    const sel = editor.getSelection();
    const selection = sel && !sel.isEmpty() ? model.getValueInRange(sel) : "";
    const coord = editor.getScrolledVisiblePosition(pos);
    if (!coord) return;
    setDraft("");
    setInline({ top: coord.top + coord.height + 4, left: Math.min(coord.left, 360), selection });
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const submitInline = () => {
    if (!draft.trim()) return setInline(null);
    const sel = inline?.selection?.trim();
    const prompt = sel
      ? `Focus your edit on this selected part of the script:\n\n\`\`\`python\n${sel}\n\`\`\`\n\nRequest: ${draft.trim()}`
      : draft.trim();
    sendChat(prompt);
    setInline(null);
    setDraft("");
  };

  const onMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => runNow());
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyK, () => openInline());
    // Cursor line drives code->geometry highlighting (plan §6).
    editor.onDidChangeCursorPosition((e) => setActiveLine(e.position.lineNumber));
  };

  // Reveal + flash a line when a face is clicked in highlight mode (geometry->code).
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco || revealLine == null) return;
    editor.revealLineInCenter(revealLine);
    editor.setPosition({ lineNumber: revealLine, column: 1 });
    const deco = editor.createDecorationsCollection([
      {
        range: new monaco.Range(revealLine, 1, revealLine, 1),
        options: { isWholeLine: true, className: "line-flash" },
      },
    ]);
    const t = setTimeout(() => deco.clear(), 1200);
    return () => clearTimeout(t);
  }, [revealLine]);

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
    <div className="editor-wrap">
      <Editor
        className="editor"
        language="python"
        theme="vs-dark"
        value={source}
        onChange={(v) => setSource(v ?? "")}
        onMount={onMount}
        options={{
          fontSize: 13,
          fontFamily: '"SF Mono", "JetBrains Mono", "Cascadia Code", Menlo, Consolas, monospace',
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          renderWhitespace: "none",
          smoothScrolling: true,
        }}
      />
      {inline && (
        <div className="inline-edit" style={{ top: inline.top, left: inline.left }}>
          <span className="inline-badge">⌘K</span>
          <input
            ref={inputRef}
            value={draft}
            placeholder={inline.selection ? "edit the selection…" : "ask the agent to edit…"}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submitInline();
              } else if (e.key === "Escape") {
                e.preventDefault();
                setInline(null);
              }
            }}
          />
        </div>
      )}
    </div>
  );
}

import Editor, { DiffEditor, type OnMount } from "@monaco-editor/react";
import { useEffect, useRef, useState } from "react";
import type * as Monaco from "monaco-editor";
import { useStore } from "../lib/store";
import "../lib/monaco-setup";
import { applyEditorTheme, loadEditorColors } from "../lib/editorTheme";

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
  const pendingPatch = useStore((s) => s.pendingPatch);
  const acceptPatch = useStore((s) => s.acceptPatch);
  const rejectPatch = useStore((s) => s.rejectPatch);

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
    // Re-apply any persisted editor theme now that the editor exists (avoids a
    // load-order race where the theme is defined before Monaco is ready).
    applyEditorTheme(loadEditorColors());
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

  // When the agent proposes a patch, show it as an inline diff right in the code
  // pane (current vs proposed), with accept/reject; otherwise the normal editor.
  if (pendingPatch) {
    return (
      <div className="editor-wrap">
        <div className="diff-banner">
          <span>agent's proposed change</span>
          <div className="diff-banner-actions">
            <button className="btn btn-accept" onClick={acceptPatch}>
              Accept
            </button>
            <button className="btn btn-reject" onClick={rejectPatch}>
              Reject
            </button>
          </div>
        </div>
        <DiffEditor
          className="editor"
          language="python"
          theme="cadcode"
          original={source}
          modified={pendingPatch.new_source}
          options={{
            renderSideBySide: false,
            readOnly: true,
            fontSize: 13.5,
            lineHeight: 21,
            fontFamily: '"Geist Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace',
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            automaticLayout: true,
            renderOverviewRuler: false,
            padding: { top: 12, bottom: 12 },
          }}
        />
      </div>
    );
  }

  return (
    <div className="editor-wrap">
      <Editor
        className="editor"
        language="python"
        theme="cadcode"
        value={source}
        onChange={(v) => setSource(v ?? "")}
        onMount={onMount}
        options={{
          fontSize: 13.5,
          lineHeight: 21,
          letterSpacing: 0.2,
          fontFamily: '"Geist Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace',
          fontLigatures: false,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          renderWhitespace: "none",
          renderLineHighlight: "all",
          roundedSelection: true,
          smoothScrolling: true,
          cursorSmoothCaretAnimation: "on",
          cursorBlinking: "smooth",
          padding: { top: 14, bottom: 14 },
          scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
          guides: { indentation: true },
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

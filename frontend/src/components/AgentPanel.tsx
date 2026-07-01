import { useEffect, useRef, useState } from "react";
import { useStore } from "../lib/store";

function DiffView({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="diff">
      {lines.map((ln, i) => {
        let cls = "diff-ctx";
        if (ln.startsWith("+") && !ln.startsWith("+++")) cls = "diff-add";
        else if (ln.startsWith("-") && !ln.startsWith("---")) cls = "diff-del";
        else if (ln.startsWith("@@")) cls = "diff-hunk";
        else if (ln.startsWith("+++") || ln.startsWith("---")) cls = "diff-file";
        return (
          <div key={i} className={cls}>
            {ln || " "}
          </div>
        );
      })}
    </pre>
  );
}

export function AgentPanel() {
  const chat = useStore((s) => s.chat);
  const pendingPatch = useStore((s) => s.pendingPatch);
  const agentBusy = useStore((s) => s.agentBusy);
  const sendChat = useStore((s) => s.sendChat);
  const acceptPatch = useStore((s) => s.acceptPatch);
  const rejectPatch = useStore((s) => s.rejectPatch);
  const previewDiff = useStore((s) => s.previewDiff);
  const diffStats = useStore((s) => s.diffStats);
  const selection = useStore((s) => s.selection);

  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [chat, agentBusy]);

  const submit = () => {
    if (!draft.trim()) return;
    sendChat(draft);
    setDraft("");
  };

  return (
    <div className="agent">
      <div className="agent-log" ref={scrollRef}>
        {chat.length === 0 && (
          <div className="agent-empty">
            Ask the agent to edit the model.
            <br />
            e.g. <em>"add a 3mm chamfer to the top rim"</em>
          </div>
        )}
        {chat.map((m, i) => (
          <div key={i} className={`msg msg-${m.role}${m.error ? " msg-err" : ""}`}>
            {m.text}
          </div>
        ))}

        {agentBusy && <div className="msg msg-assistant agent-thinking">thinking…</div>}
      </div>

      {/* Pending patch is pinned above the input so Accept/Reject are always
          reachable (independent of the conversation scroll or Preview 3D). */}
      {pendingPatch && (
        <div className="patch-card patch-pinned">
          <div className="patch-rationale">{pendingPatch.rationale}</div>
          {pendingPatch.targets.length > 0 && (
            <div className="patch-targets">
              {pendingPatch.targets.map((t, i) => (
                <code key={i}>{t}</code>
              ))}
            </div>
          )}
          <DiffView diff={pendingPatch.diff} />
          {diffStats && (
            <div className="patch-diffstats">
              geometry diff: <span className="d-add">+{diffStats.added}</span>{" "}
              <span className="d-rem">−{diffStats.removed}</span> regions
            </div>
          )}
          <div className="patch-actions">
            <button className="btn btn-accept" onClick={acceptPatch}>
              Accept
            </button>
            <button className="btn" onClick={previewDiff} title="Show the geometric consequence in 3D">
              Preview 3D
            </button>
            <button className="btn btn-reject" onClick={rejectPatch}>
              Reject
            </button>
          </div>
        </div>
      )}

      <div className="agent-input">
        {selection?.selector && (
          <div className="agent-context">
            on <code>{selection.selector}</code>
          </div>
        )}
        <textarea
          value={draft}
          placeholder="Tell the agent what to change…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={2}
        />
        <button className="btn btn-send" onClick={submit} disabled={agentBusy || !draft.trim()}>
          Send
        </button>
      </div>
    </div>
  );
}

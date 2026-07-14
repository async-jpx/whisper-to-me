import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatSource } from "../api/types";
import { md } from "../lib/markdown";
import { useStore } from "../store";

interface ChatMessage {
  role: "user" | "assistant" | "thinking";
  text?: string; // user message
  answer?: string; // assistant
  sources?: ChatSource[];
}

// Module-level messages array survives view switches, resets only on page reload.
let messages: ChatMessage[] = [];
let messageVersion = 0;

export function ChatView() {
  const pushChatTurn = useStore((s) => s.pushChatTurn);
  const popChatTurn = useStore((s) => s.popChatTurn);
  const toast = useStore((s) => s.toast);
  // All message mutations go through addMessage/removeLastMessage below,
  // which bump this state — no polling needed to track the module array.
  const [, setVersion] = useState(messageVersion);
  const [pending, setPending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  // Focus input on mount.
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Scroll to bottom after messages change (and on re-entering the view).
  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  });

  const addMessage = (msg: ChatMessage) => {
    messages.push(msg);
    messageVersion++;
    setVersion(messageVersion);
  };

  const removeLastMessage = () => {
    messages.pop();
    messageVersion++;
    setVersion(messageVersion);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = inputRef.current?.value.trim() ?? "";
    if (!q || pending) return;

    const input = inputRef.current;
    if (!input) return;

    input.value = "";
    setPending(true);

    // History = prior turns only, captured BEFORE pushing the new question.
    const history = useStore.getState().chatHistory.slice(-6);

    addMessage({ role: "user", text: q });
    pushChatTurn({ role: "user", content: q });
    addMessage({ role: "thinking" });

    try {
      const resp = await api.chat(q, history);

      removeLastMessage(); // thinking
      addMessage({ role: "assistant", answer: resp.answer, sources: resp.sources });
      pushChatTurn({ role: "assistant", content: resp.answer });
    } catch {
      removeLastMessage(); // thinking
      popChatTurn(); // roll back the unanswered turn
      toast("Couldn't get an answer.", "error");
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  };

  return (
    <div className="chat-view">
      <div className="chat-messages" ref={messagesRef}>
        {messages.length === 0 ? (
          <div className="chat-hint">
            Ask anything about your past meetings. Answers cite the notes they come from —
            nothing leaves your machine.
          </div>
        ) : (
          messages.map((msg, i) => {
            if (msg.role === "user") {
              return (
                <div key={i} className="chat-msg chat-user">
                  {msg.text}
                </div>
              );
            } else if (msg.role === "thinking") {
              return (
                <div key={i} className="chat-msg chat-assistant chat-thinking">
                  Thinking…
                </div>
              );
            } else {
              return (
                <AssistantMessage
                  key={i}
                  answer={msg.answer ?? ""}
                  sources={msg.sources ?? []}
                />
              );
            }
          })
        )}
      </div>
      <form className="chat-form" onSubmit={handleSubmit}>
        <input
          ref={inputRef}
          className="chat-input"
          type="text"
          placeholder="Ask about your meetings…"
          autoComplete="off"
          disabled={pending}
        />
        <button type="submit" className="btn btn-primary btn-sm" disabled={pending}>
          Ask
        </button>
      </form>
    </div>
  );
}

interface AssistantMessageProps {
  answer: string;
  sources: ChatSource[];
}

function AssistantMessage({ answer, sources }: AssistantMessageProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const processedRef = useRef(false);

  useEffect(() => {
    if (processedRef.current || !bodyRef.current) return;
    processedRef.current = true;

    // Linkify citations: [n] → link to cited note.
    linkifyCitations(bodyRef.current, sources);
  }, [sources]);

  return (
    <div className="chat-msg chat-assistant">
      <div
        className="chat-body"
        ref={bodyRef}
        dangerouslySetInnerHTML={{ __html: md.render(answer) }}
      />
      {sources.length > 0 && <SourceList sources={sources} />}
    </div>
  );
}

interface SourceListProps {
  sources: ChatSource[];
}

function SourceList({ sources }: SourceListProps) {
  const openNote = useStore((s) => s.openNote);

  return (
    <div className="chat-sources">
      {sources.map((src, i) => (
        <span key={src.n}>
          {i > 0 && " · "}
          <a
            href="#"
            className="cite"
            title={src.title}
            onClick={(e) => {
              e.preventDefault();
              openNote(src.name);
            }}
          >
            {src.n}. {src.title}
          </a>
        </span>
      ))}
    </div>
  );
}

function linkifyCitations(root: HTMLElement, sources: ChatSource[]) {
  const byN = new Map(sources.map((s) => [s.n, s]));
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Node[] = [];

  while (walker.nextNode()) {
    nodes.push(walker.currentNode);
  }

  for (const node of nodes) {
    const text = node.textContent ?? "";
    const parts = text.split(/\[(\d+)\]/);
    if (parts.length < 3) continue;

    const frag = document.createDocumentFragment();
    parts.forEach((part, i) => {
      if (i % 2 === 0) {
        if (part) frag.appendChild(document.createTextNode(part));
        return;
      }

      const src = byN.get(Number(part));
      if (!src) {
        frag.appendChild(document.createTextNode(`[${part}]`));
        return;
      }

      const a = document.createElement("a");
      a.className = "cite";
      a.href = "#";
      a.textContent = `[${part}]`;
      a.title = src.title;
      a.addEventListener("click", (evt) => {
        evt.preventDefault();
        useStore.getState().openNote(src.name);
      });
      frag.appendChild(a);
    });

    (node as any).replaceWith(frag);
  }
}

/* 💬 Ask view on the AI SDK's useChat: answers stream token-by-token from
   /api/chat/stream (SSE from the local daemon → local Ollama; nothing leaves
   the machine). The Chat instance lives at module level so the conversation
   survives view switches, resetting only on page reload. */

import { useEffect, useRef, useState } from "react";
import { Chat, useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import type { ChatSource } from "../api/types";
import { md } from "../lib/markdown";
import { useStore } from "../store";

type ChatMessage = UIMessage<unknown, { sources: ChatSource[] }>;

const chat = new Chat<ChatMessage>({
  transport: new DefaultChatTransport({ api: "/api/chat/stream" }),
});

function messageText(msg: ChatMessage): string {
  return msg.parts
    .filter((p) => p.type === "text")
    .map((p) => p.text)
    .join("");
}

function messageSources(msg: ChatMessage): ChatSource[] {
  const part = msg.parts.find((p) => p.type === "data-sources");
  return part?.data ?? [];
}

export function ChatView() {
  const toast = useStore((s) => s.toast);
  const { messages, sendMessage, status } = useChat({
    chat,
    onError: () => toast("Couldn't get an answer.", "error"),
  });
  const pending = status === "submitted" || status === "streaming";
  const inputRef = useRef<HTMLInputElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  // Focus input on mount, and again when an answer finishes.
  useEffect(() => {
    if (!pending) inputRef.current?.focus();
  }, [pending]);

  // Scroll to bottom after messages change (and on re-entering the view).
  useEffect(() => {
    if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    }
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = inputRef.current?.value.trim() ?? "";
    if (!q || pending) return;
    if (inputRef.current) inputRef.current.value = "";
    void sendMessage({ text: q });
  };

  const last = messages[messages.length - 1];
  const thinking = status === "submitted" || (pending && last?.role === "user");

  return (
    <div className="chat-view">
      <div className="chat-messages" ref={messagesRef}>
        {messages.length === 0 && !thinking ? (
          <div className="chat-hint">
            Ask anything about your past meetings. Answers cite the notes they come from —
            nothing leaves your machine.
          </div>
        ) : (
          messages.map((msg, i) =>
            msg.role === "user" ? (
              <div key={msg.id} className="chat-msg chat-user">
                {messageText(msg)}
              </div>
            ) : (
              <AssistantMessage
                key={msg.id}
                answer={messageText(msg)}
                sources={messageSources(msg)}
                complete={!(pending && i === messages.length - 1)}
              />
            )
          )
        )}
        {thinking && (
          <div className="chat-msg chat-assistant chat-thinking">Thinking…</div>
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
  complete: boolean;
}

function AssistantMessage({ answer, sources, complete }: AssistantMessageProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [html, setHtml] = useState("");

  // Markdown re-renders on every streamed delta; citations become links only
  // once the answer is complete (linkify walks the final DOM).
  useEffect(() => {
    setHtml(md.render(answer));
  }, [answer]);

  useEffect(() => {
    if (complete && bodyRef.current) linkifyCitations(bodyRef.current, sources);
  }, [complete, sources, html]);

  return (
    <div className="chat-msg chat-assistant">
      <div className="chat-body" ref={bodyRef} dangerouslySetInnerHTML={{ __html: html }} />
      {complete && sources.length > 0 && <SourceList sources={sources} />}
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

/**
 * Assistant chat (M5).
 *
 * The API returns short replies plus a list of actions the client applies to
 * the viewer state. Quick-reply chips come from the server so they track what
 * actually just happened.
 */

import { useEffect, useRef, useState } from 'react';
import { apiUrl } from '../lib/api';

export interface AssistantAction {
  tool: string;
  args: Record<string, unknown>;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface Props {
  projectId: string;
  sessionId: string;
  context: Record<string, unknown>;
  onActions: (actions: AssistantAction[]) => void;
}

const STARTER_CHIPS = [
  'Warmer floors',
  'Furnish this room',
  'Something more minimal',
];

export function Assistant({ projectId, sessionId, context, onActions }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [chips, setChips] = useState<string[]>(STARTER_CHIPS);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, busy]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setInput('');
    setError(null);
    setBusy(true);
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((m) => [...m, { role: 'user', content: trimmed }]);

    try {
      const response = await fetch(apiUrl('/api/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          project_id: projectId,
          message: trimmed,
          history,
          context,
        }),
      });

      if (!response.ok) {
        const detail = await response
          .json()
          .then((b) => b.detail as string)
          .catch(() => 'The assistant is unavailable.');
        setError(detail);
        return;
      }

      const body = await response.json();
      setMessages((m) => [...m, { role: 'assistant', content: body.reply }]);
      setChips(body.chips ?? STARTER_CHIPS);
      if (body.actions?.length) onActions(body.actions);
    } catch {
      setError('Could not reach the assistant. Check that the API is running.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col" data-testid="assistant">
      <div ref={logRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {messages.length === 0 && !error && (
          <p className="max-w-[48ch] text-[13px] leading-relaxed opacity-70">
            Describe how you want this place to feel and I will restyle it from our
            catalog. Try a mood, an era, or a material.
          </p>
        )}

        {messages.map((message, i) => (
          <div
            key={i}
            className={message.role === 'user' ? 'mt-3 text-right' : 'mt-3'}
          >
            <span
              className={[
                'inline-block max-w-[46ch] px-3 py-2 text-[13px] leading-snug',
                message.role === 'user'
                  ? 'bg-[var(--accent)]'
                  : 'rule border bg-[var(--paper-warm)]',
              ].join(' ')}
            >
              {message.content}
            </span>
          </div>
        ))}

        {busy && (
          <p className="mt-3 text-[13px] opacity-60" role="status">
            Looking through the catalog…
          </p>
        )}

        {error && (
          <p
            className="mt-3 border-l-2 py-1 pl-3 text-[13px]"
            style={{ borderColor: 'var(--staged)' }}
            role="alert"
          >
            {error}
          </p>
        )}
      </div>

      {chips.length > 0 && (
        <div className="scroll-x flex gap-1.5 px-4 pb-2">
          {chips.map((chip) => (
            <button
              key={chip}
              type="button"
              onClick={() => send(chip)}
              disabled={busy}
              className="rule shrink-0 border px-2.5 py-1 text-[12px] opacity-80 hover:opacity-100 disabled:opacity-40"
            >
              {chip}
            </button>
          ))}
        </div>
      )}

      <form
        className="rule flex gap-2 border-t p-3"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Make it feel warmer…"
          aria-label="Message the assistant"
          disabled={busy}
          className="rule min-w-0 flex-1 border bg-transparent px-3 py-2 text-[13px] placeholder:opacity-45"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="btn-primary px-4 py-2 text-[13px]"
        >
          Send
        </button>
      </form>
    </div>
  );
}

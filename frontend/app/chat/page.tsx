"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { askQuestion, getChatStatus, type ChatReply } from "@/lib/api";

const TOPICS = [
  { title: "Schemes", question: "Who qualifies for PM-KISAN?" },
  { title: "Policies", question: "What is the RBI's inflation target?" },
  { title: "Rights & services", question: "How do I file an RTI application?" },
  { title: "Business", question: "What licences are needed to start a new company?" },
];
const MODES: Record<string, string> = {
  saved: "From saved official pages",
  live: "From a live search of official sites",
  document: "From the page you linked",
  web: "From a web search, not official sources, so treat this with extra care",
};

type Turn = { question: string; reply: ChatReply | null; error?: string };

export default function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [documentUrl, setDocumentUrl] = useState("");
  const [showLink, setShowLink] = useState(false);
  const [pages, setPages] = useState<number | null>(null);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatStatus()
      .then((status) => {
        setPages(status.pages);
        setRemaining(status.remaining ?? null);
      })
      .catch(() => setPages(null));
  }, [turns.length, busy]);

  // Braces matter, as scrollIntoView can return a promise, which an effect must not return.
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  async function ask(text: string) {
    const clean = text.trim();
    if (clean.length < 3 || busy) return;
    setBusy(true);
    setQuestion("");
    const history = turns
      .filter((turn) => turn.reply?.found)
      .slice(-3)
      .map((turn) => ({
        question: turn.question,
        answer: (turn.reply?.answer ?? "").slice(0, 2000),
        source_urls: (turn.reply?.sources ?? []).map((source) => source.url).slice(0, 8),
      }));
    setTurns((current) => [...current, { question: clean, reply: null }]);
    const finish = (turn: Partial<Turn>) =>
      setTurns((current) => [...current.slice(0, -1), { ...current[current.length - 1], ...turn }]);
    try {
      finish({ reply: await askQuestion(clean, documentUrl.trim() || undefined, history) });
    } catch (caught) {
      finish({ error: caught instanceof Error ? caught.message : "Please try again." });
    }
    setBusy(false);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    ask(question);
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-white">Understand government information</h1>
        <p className="mt-1 text-slate-400">
          Ask about Indian schemes, policies, rights and public data. Answers come only from official
          sources{pages === null ? "" : `, starting with ${pages} saved pages`}, and every answer cites
          them. This is general information, not legal or eligibility advice.
        </p>
      </div>

      {turns.length === 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {TOPICS.map((topic) => (
            <button
              key={topic.title}
              type="button"
              onClick={() => ask(topic.question)}
              className="card p-4 text-left transition hover:border-cyan-300/40"
            >
              <span className="block font-semibold text-white">{topic.title}</span>
              <span className="text-sm text-slate-400">{topic.question}</span>
            </button>
          ))}
        </div>
      )}

      {turns.map((turn, index) => (
        <div key={index} className="space-y-3">
          <p className="ml-auto w-fit max-w-[85%] rounded-xl bg-cyan-300/15 px-4 py-2 break-words text-cyan-50">
            {turn.question}
          </p>
          <div className="card space-y-3 p-4">
            {turn.error && <p className="text-amber-300">{turn.error}</p>}
            {turn.reply?.understood_as && turn.reply.understood_as !== turn.question && (
              <p className="text-xs text-slate-500">Understood as: {turn.reply.understood_as}</p>
            )}
            {!turn.error && !turn.reply && (
              <p className="animate-pulse text-slate-400">Reading official sources...</p>
            )}
            {turn.reply && !turn.reply.sections?.length && (
              <p className="text-slate-100">{turn.reply.answer}</p>
            )}
            {turn.reply?.sections?.map((section) => (
              <section key={section.title}>
                {(turn.reply?.sections?.length ?? 0) > 1 && (
                  <h3 className="text-sm font-semibold text-slate-300">{section.title}</h3>
                )}
                {section.sentences.map((sentence) => (
                  <p key={sentence} className="break-words text-slate-100">
                    {sentence}
                  </p>
                ))}
              </section>
            ))}
            {turn.reply?.sources.map((source) => (
              <details key={source.url} className="rounded-lg border border-white/10 bg-black/20 p-3 text-sm">
                <summary className="cursor-pointer break-words text-slate-300">
                  {source.publisher}
                  {source.official ? " — Official source" : " — Other source"}
                  {source.published && ` · ${source.published}`}
                </summary>
                <p className="mt-2 break-words text-slate-300">{source.quote}</p>
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 block break-words text-cyan-300 underline"
                >
                  {source.title}
                </a>
              </details>
            ))}
            {turn.reply?.found && (
              <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-500">
                <p>
                  {MODES[turn.reply.mode]}. AI-written, so check the sources before relying on this.
                </p>
                <Link
                  href={`/?text=${encodeURIComponent(turn.reply.answer.slice(0, 2000))}`}
                  className="text-cyan-300 underline"
                >
                  Fact-check this answer
                </Link>
              </div>
            )}
          </div>
        </div>
      ))}
      <div ref={end} />

      <form onSubmit={submit} className="space-y-2">
        {showLink && (
          <input
            aria-label="Official page or PDF link"
            type="url"
            value={documentUrl}
            onChange={(event) => setDocumentUrl(event.target.value)}
            placeholder="Paste a link to a notification or PDF, to ask about that page only"
            className="field"
          />
        )}
        <div className="flex gap-2">
          <input
            aria-label="Your question"
            value={question}
            maxLength={500}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask about a scheme, policy, right or statistic"
            className="field"
          />
          <button type="submit" disabled={busy || question.trim().length < 3} className="primary-button shrink-0">
            Ask
          </button>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-400">
          <button
            type="button"
            onClick={() => {
              setShowLink(!showLink);
              setDocumentUrl("");
            }}
            className="underline"
          >
            {showLink ? "Search all official sources" : "Ask about a specific notification"}
          </button>
          {remaining !== null && <span>{remaining} left today, shared with checks</span>}
        </div>
      </form>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import type { Audit } from "@/lib/api";

const STAGES: Record<string, string> = {
  extract_text: "Reading the text",
  extract_claims: "Finding the claims",
  classify_claims: "Working out what proof is needed",
  investigate_claims: "Searching and reading sources",
  build_source_history: "Tracing the sources",
  write_report: "Writing the answer",
};

export function formatDuration(seconds: number) {
  const total = Math.max(Math.round(seconds), 0);
  const minutes = Math.floor(total / 60);
  if (minutes === 0) return `${total} sec`;
  return total % 60 === 0 ? `${minutes} min` : `${minutes} min ${total % 60} sec`;
}

export default function AuditProgress({
  audit,
  onCancel,
  cancelling,
}: {
  audit: Audit;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const [elapsed, setElapsed] = useState(audit.elapsed_seconds);

  useEffect(() => {
    setElapsed(audit.elapsed_seconds);
    const timer = setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, [audit.elapsed_seconds]);

  const left = audit.estimate_seconds - elapsed;
  const progress = Math.min((elapsed / audit.estimate_seconds) * 100, 95);
  const stage = audit.current_stage ? STAGES[audit.current_stage] : "Waiting to start";

  return (
    <section className="card space-y-5 p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-semibold tracking-widest text-cyan-300 uppercase">
            Checking now
          </p>
          <h2 className="mt-1 text-2xl font-semibold tracking-tight text-white">{stage}</h2>
        </div>
        <button
          type="button"
          onClick={onCancel}
          disabled={cancelling}
          className="rounded-lg border border-white/15 px-3 py-1.5 text-sm font-medium text-slate-200 transition hover:border-red-400/60 hover:text-red-300 disabled:opacity-50"
        >
          {cancelling ? "Cancelling..." : "Cancel"}
        </button>
      </div>

      <div>
        <div className="h-2 overflow-hidden rounded-full bg-white/10">
          <div
            className="progress-bar h-full rounded-full transition-[width] duration-1000"
            style={{ width: `${progress}%` }}
          />
        </div>
        <p className="mt-2 flex justify-between text-sm text-slate-400">
          <span>{formatDuration(elapsed)} so far</span>
          <span>
            {left > 5 ? `About ${formatDuration(left)} left` : "Taking a little longer than usual"}
          </span>
        </p>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-slate-300">Text being verified</h3>
        <p className="mt-1 max-h-40 overflow-y-auto rounded-lg bg-black/25 p-3 break-words whitespace-pre-wrap text-slate-200">
          {audit.input_text}
        </p>
      </div>

      {audit.claims.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-slate-300">
            {audit.claims.length === 1 ? "Claim being checked" : "Claims being checked"}
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-200">
            {audit.claims.map((claim) => (
              <li key={claim} className="break-words">
                {claim}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-slate-500">
        Estimated at 1 min 30 sec per claim. You can leave this page, and the check keeps running.
      </p>
    </section>
  );
}

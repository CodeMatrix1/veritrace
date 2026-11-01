"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { forgetAudit, forgetAudits, loadHistory, type HistoryEntry } from "@/lib/history";
import VerdictBadge from "./VerdictBadge";

export default function AuditList({ limit }: { limit?: number }) {
  const [entries, setEntries] = useState<HistoryEntry[] | null>(null);

  useEffect(() => {
    const refresh = () => setEntries(loadHistory());
    refresh();
    window.addEventListener("veritrace:history-updated", refresh);
    return () => window.removeEventListener("veritrace:history-updated", refresh);
  }, []);

  if (entries === null) return null;
  if (entries.length === 0) {
    return limit ? null : (
      <p className="card p-6 text-slate-400">
        Your audits will appear here. They are saved in this browser only, and audits from the
        extension are added when it is installed.
      </p>
    );
  }
  const shown = limit ? entries.slice(0, limit) : entries;

  return (
    <section className="space-y-3">
      {limit !== undefined && (
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-semibold text-white">Recent audits</h2>
          <Link href="/audits" className="text-sm text-slate-400 underline hover:text-white">
            See all
          </Link>
        </div>
      )}
      <ul className="space-y-2">
        {shown.map((entry) => (
          <li key={entry.id} className="flex items-center gap-2">
            <Link
              href={`/audit/${entry.id}`}
              className="card flex min-w-0 flex-1 items-center justify-between gap-4 p-4 transition hover:border-cyan-300/40"
            >
              <span className="min-w-0">
                <span className="block truncate text-slate-100">{entry.text}</span>
                <span className="text-xs text-slate-500">
                  {new Date(entry.createdAt).toLocaleString()}
                </span>
              </span>
              <span className="shrink-0">
                <VerdictBadge verdict={entry.result ?? "running"} />
              </span>
            </Link>
            <button
              type="button"
              aria-label={`Remove ${entry.text} from this list`}
              title="Remove from this list"
              onClick={() => {
                forgetAudit(entry.id);
                setEntries(loadHistory());
              }}
              className="shrink-0 rounded-md px-2 py-1 text-lg text-slate-500 transition hover:bg-white/10 hover:text-red-300"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      {limit === undefined && (
        <button
          type="button"
          onClick={() => {
            forgetAudits();
            setEntries([]);
          }}
          className="text-sm text-slate-500 underline hover:text-slate-300"
        >
          Clear this list
        </button>
      )}
    </section>
  );
}

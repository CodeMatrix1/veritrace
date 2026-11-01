import { formatLabel, type TraceEvent } from "@/lib/api";

const HIDDEN_STATUSES = new Set(["started", "kept", "merged"]);

export default function LiveTrace({ events, finished }: { events: TraceEvent[]; finished: boolean }) {
  const visible = events.filter((event) => event.message && !HIDDEN_STATUSES.has(event.status));
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">
        {finished ? `Investigation trace (${visible.length} steps)` : "Investigation trace"}
      </h3>
      <ol aria-live="polite" className="max-h-72 space-y-1.5 overflow-y-auto text-sm">
        {visible.map((event) => (
          <li key={event.id} className="flex gap-2">
            <span className="w-36 shrink-0 text-slate-500">{formatLabel(event.stage)}</span>
            <span className="break-words">{event.message}</span>
          </li>
        ))}
        {!finished && <li className="text-slate-500">Working...</li>}
      </ol>
    </section>
  );
}

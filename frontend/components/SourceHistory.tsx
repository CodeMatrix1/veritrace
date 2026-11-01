import type { SourceHistoryView, SourceRow } from "@/lib/api";

function SourceLink({ source }: { source: SourceRow }) {
  return (
    <li className="break-words">
      {source.date ?? "Date unknown"} ·{" "}
      <a href={source.url} target="_blank" rel="noreferrer" className="underline">
        {source.title || source.publisher}
      </a>{" "}
      <span className="text-slate-500">({source.publisher})</span>
    </li>
  );
}

export default function SourceHistory({ history }: { history: SourceHistoryView }) {
  return (
    <section className="rounded-lg border border-white/10 p-4 text-sm">
      <h3 className="font-medium">Source History</h3>
      <p className="mb-2 text-xs text-slate-500">{history.note}</p>
      {history.earliest_found && (
        <p className="mb-2">
          <span className="font-medium">{history.earliest_label}: </span>
          {history.earliest_found.title || history.earliest_found.publisher}
        </p>
      )}
      <ul className="space-y-1">
        {[...history.dated, ...history.undated].map((source) => (
          <SourceLink key={source.url} source={source} />
        ))}
      </ul>
    </section>
  );
}

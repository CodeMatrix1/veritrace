import type { LedgerEntry } from "@/lib/api";

const SECTIONS = [
  { kind: "source_statement", title: "What sources say", note: "Exact quotes from each source." },
  { kind: "model_assessment", title: "How the AI read them", note: "The AI's explanation, not a source." },
  { kind: "uncertain", title: "Still uncertain", note: "What could not be established." },
] as const;

export default function EvidenceLedger({ entries }: { entries: LedgerEntry[] }) {
  return (
    <section className="space-y-3">
      <h3 className="text-sm font-semibold">How this was checked</h3>
      <div className="grid gap-3 md:grid-cols-3">
        {SECTIONS.map((section) => {
          const items = entries.filter((entry) => entry.kind === section.kind);
          if (items.length === 0) return null;
          return (
            <div key={section.kind} className="rounded-lg border border-white/10 p-4">
              <h4 className="font-medium">{section.title}</h4>
              <p className="mb-2 text-xs text-slate-500">{section.note}</p>
              <ul className="space-y-2 text-sm">
                {items.map((entry) => (
                  <li key={entry.label} className="break-words">
                    {entry.statement}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}

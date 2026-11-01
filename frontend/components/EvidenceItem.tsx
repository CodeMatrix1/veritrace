import type { EvidenceView } from "@/lib/api";

const TIERS: Record<string, string> = {
  primary: "Official source",
  secondary: "Other source",
  unknown: "Other source",
};

function stanceLabel(evidence: EvidenceView) {
  if (evidence.validation_status === "contextual") return "Contextual";
  return evidence.stance === "contradicts" ? "Contradicts" : "Supports";
}

export default function EvidenceItem({ evidence }: { evidence: EvidenceView }) {
  const tier = evidence.source_tier && TIERS[evidence.source_tier];
  return (
    <li className="space-y-2 rounded-lg border border-white/10 bg-black/20 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-semibold break-words">
          {evidence.publisher}
          {tier && <span className="font-normal text-slate-400"> — {tier}</span>}
        </p>
        <span className="rounded bg-white/10 px-1.5 py-0.5 text-xs font-medium text-slate-300">
          {stanceLabel(evidence)}
        </span>
      </div>
      <blockquote className="border-l-2 border-white/20 pl-3 break-words text-white">
        {evidence.quote}
      </blockquote>
      <p className="text-xs text-slate-500">
        {evidence.published_date && `${evidence.published_date} · `}
        <a href={evidence.source_url} target="_blank" rel="noreferrer" className="underline">
          {evidence.source_title || "Open source"}
        </a>
        {` · Retrieved ${new Date(evidence.retrieved_at).toLocaleDateString()}`}
        {evidence.retrieved_via === "tavily" && " · Text supplied by the search provider"}
        {!evidence.used_in_verdict && " · Not used in this result"}
      </p>
    </li>
  );
}

import { formatLabel } from "@/lib/api";
import { VERDICT_COPY, verdictTone } from "@/lib/verdicts";

const TONES: Record<string, string> = {
  good: "bg-emerald-400/15 text-emerald-300 ring-emerald-400/30",
  bad: "bg-red-400/15 text-red-300 ring-red-400/30",
  warn: "bg-amber-400/15 text-amber-300 ring-amber-400/30",
  stop: "bg-slate-400/15 text-slate-300 ring-slate-400/30",
};

export default function VerdictBadge({ verdict }: { verdict: string }) {
  const label = VERDICT_COPY[verdict]?.badge ?? formatLabel(verdict);
  return (
    <span
      className={`inline-block rounded-md px-2.5 py-1 text-xs font-bold tracking-wide uppercase ring-1 ${TONES[verdictTone(verdict)]}`}
    >
      {label}
    </span>
  );
}

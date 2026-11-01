import type { AnswerSentence, ClaimView, EvidenceView } from "@/lib/api";
import { formatLabel } from "@/lib/api";
import {
  VERDICT_COPY,
  evidenceGroups,
  explainClaim,
  plainGaps,
  type ExplanationBlock,
} from "@/lib/verdicts";
import EvidenceItem from "./EvidenceItem";
import VerdictBadge from "./VerdictBadge";

const VISIBLE_EVIDENCE = 3;

function Blocks({ blocks }: { blocks: ExplanationBlock[] }) {
  return blocks
    .filter((block) => block.items.length > 0)
    .map((block) => (
      <section key={block.title} className="space-y-1">
        <h4 className="text-sm font-semibold">{block.title}</h4>
        {block.items.length === 1 ? (
          <p className="break-words text-slate-200">{block.items[0]}</p>
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-slate-200">
            {block.items.map((item) => (
              <li key={item} className="break-words">
                {item}
              </li>
            ))}
          </ul>
        )}
      </section>
    ));
}

export function EvidenceGroup({ title, items }: { title: string; items: EvidenceView[] }) {
  const more = items.slice(VISIBLE_EVIDENCE);
  return (
    <section className="space-y-2">
      <h4 className="text-sm font-semibold">{title}</h4>
      <ul className="space-y-3">
        {items.slice(0, VISIBLE_EVIDENCE).map((item) => (
          <EvidenceItem key={item.id} evidence={item} />
        ))}
      </ul>
      {more.length > 0 && (
        <details className="space-y-3">
          <summary className="cursor-pointer text-sm text-slate-400">
            Show {more.length} more
          </summary>
          <ul className="mt-3 space-y-3">
            {more.map((item) => (
              <EvidenceItem key={item.id} evidence={item} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

export default function ClaimReport({
  claim,
  answer,
  showVerdict,
}: {
  claim: ClaimView;
  answer: AnswerSentence[];
  showVerdict: boolean;
}) {
  const verdict = claim.verdict ?? "error";
  const copy = VERDICT_COPY[verdict];
  const insufficient = verdict === "unverified_insufficient";
  const { before, after } = explainClaim(claim, insufficient ? [] : answer);
  const groups = evidenceGroups(claim).filter((group) => group.items.length > 0);
  const gaps = insufficient ? [] : plainGaps(claim.gaps);

  return (
    <article className="space-y-4 card p-5">
      {showVerdict && (
        <div className="space-y-1">
          <VerdictBadge verdict={verdict} />
          <h3 className="text-lg font-semibold">{copy?.headline}</h3>
          <p className="text-sm text-slate-400">{copy?.description}</p>
        </div>
      )}
      <div className="space-y-1">
        <p className="break-words">
          <span className="font-semibold">Claim: </span>
          &ldquo;{claim.text}&rdquo;
        </p>
        {claim.confidence && (
          <p>
            <span className="font-semibold">Confidence: </span>
            {formatLabel(claim.confidence)}
          </p>
        )}
      </div>

      <Blocks blocks={before} />
      {groups.map((group) => (
        <EvidenceGroup key={group.title} title={group.title} items={group.items} />
      ))}
      {insufficient && groups.length === 0 && (
        <section className="space-y-1">
          <h4 className="text-sm font-semibold">Evidence found</h4>
          <p className="text-slate-200">No usable supporting or contradicting evidence was found.</p>
        </section>
      )}
      <Blocks blocks={after} />
      <Blocks blocks={[{ title: "Limits of this check", items: gaps }]} />
    </article>
  );
}

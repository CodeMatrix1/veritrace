import type { AnswerSentence, ClaimView, EvidenceView } from "./api";

export type VerdictCopy = { badge: string; headline: string; description: string };

export type ExplanationBlock = { title: string; items: string[] };

export const VERDICT_COPY: Record<string, VerdictCopy> = {
  supported: {
    badge: "Confirmed",
    headline: "Sources confirm this",
    description: "The available evidence directly supports this claim.",
  },
  contradicted: {
    badge: "Incorrect",
    headline: "Sources show this is incorrect",
    description: "The available evidence establishes a different fact.",
  },
  misleading: {
    badge: "Misleading",
    headline: "True, but missing context",
    description: "The claim contains a correct element but omits important context.",
  },
  unverified_insufficient: {
    badge: "Unconfirmed",
    headline: "Not enough evidence",
    description: "The available sources do not establish whether the claim is true or false.",
  },
  unverified_conflicting: {
    badge: "Sources disagree",
    headline: "Sources disagree",
    description: "Reliable sources disagree about a material part of this claim.",
  },
  partially_supported: {
    badge: "Partly confirmed",
    headline: "Partly confirmed",
    description: "Some claims are confirmed and others are not. Each result is shown below.",
  },
  assessment_incomplete: {
    badge: "Incomplete",
    headline: "Check incomplete",
    description: "At least one claim could not be assessed. This is not a finding about its truth.",
  },
  no_factual_claims: {
    badge: "Nothing to check",
    headline: "Nothing to check",
    description: "We found no factual claims that sources could prove true or false.",
  },
  cancelled: { badge: "Cancelled", headline: "Check cancelled", description: "" },
  failed: { badge: "Failed", headline: "Check failed", description: "" },
  running: { badge: "Running", headline: "Checking", description: "" },
  error: {
    badge: "Not checked",
    headline: "Could not be checked",
    description: "The system could not finish this claim. This is not a finding about its truth.",
  },
};

const COUNT_LABELS: Record<string, string> = {
  supported: "confirmed",
  contradicted: "incorrect",
  misleading: "misleading",
  unverified_conflicting: "where sources disagree",
  unverified_insufficient: "unconfirmed",
  error: "not checked",
};

/** Summarize every claim's result in one line. */
export function resultSummary(claims: ClaimView[]) {
  const counts = new Map<string, number>();
  for (const claim of claims) {
    const label = COUNT_LABELS[claim.verdict ?? "error"];
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const parts = [...counts].map(([label, count]) => `${count} ${label}`);
  return `Of ${claims.length} claims: ${parts.join(", ")}.`;
}

export function verdictTone(verdict: string) {
  if (verdict === "supported") return "good";
  if (verdict === "contradicted" || verdict === "failed") return "bad";
  if (["partially_supported", "misleading"].includes(verdict) || verdict.startsWith("unverified")) {
    return "warn";
  }
  return "stop";
}

const MISSING_CONTEXT: Record<string, string> = {
  time_differs: "The source states this fact for a different period than the claim.",
  scope_differs: "The source limits this fact to a narrower group or condition than the claim.",
  skeptic_missing_qualification: "The source includes a qualification that the claim leaves out.",
};
const CONTEXT_MATTERS =
  "A fact that holds for one period or group does not establish it for another.";
export const NO_PROOF_OF_FALSITY =
  "A lack of public evidence does not prove the claim false.";
export const WEAK_EVIDENCE =
  "The sources found don't meet the evidence standard for this claim. That does not prove it false.";
const STANCE_SECTIONS = {
  supports: "Supporting evidence",
  contradicts: "Contradicting evidence",
};

const unique = (items: (string | null | undefined)[]) => [
  ...new Set(items.filter((item): item is string => Boolean(item))),
];

export function strongestFirst(evidence: EvidenceView[]) {
  const rank = (item: EvidenceView) =>
    (item.used_in_verdict ? 0 : 4) +
    (item.source_tier === "primary" ? 0 : 2) +
    (item.validation_status === "valid" ? 0 : 1);
  return [...evidence].sort((a, b) => rank(a) - rank(b));
}

/** Replace internal gap wording, and remove repeats. */
export function plainGaps(gaps: string[]) {
  return unique(
    gaps.map((gap) => {
      if (gap.startsWith("Unsupported language:")) return "Some sources were not in English.";
      if (gap.includes("search failed")) return "Some searches did not complete.";
      if (gap.startsWith("Only part of this long document")) {
        return "Some long documents were only partly read.";
      }
      return gap;
    }),
  );
}

/** Build each explanation from saved report data, so this adds no LLM calls. */
export function explainClaim(claim: ClaimView, answer: AnswerSentence[]) {
  const sentences = answer.map((sentence) => sentence.text);
  const used = strongestFirst(claim.evidence.filter((item) => item.used_in_verdict));
  // Some reasons only repeat the quote, which the evidence card already shows.
  const reasons = unique(used.filter((item) => item.reason !== item.quote).map((item) => item.reason));

  switch (claim.verdict) {
    case "supported":
      return {
        before: [{ title: "Why it is confirmed", items: unique([...sentences, ...reasons]).slice(0, 3) }],
        after: [],
      };
    case "contradicted":
      return {
        before: [{ title: "What the evidence establishes", items: sentences }],
        after: [{ title: "Why the claim is incorrect", items: reasons.slice(0, 2) }],
      };
    case "misleading":
      return {
        before: [
          { title: "What is true", items: sentences },
          {
            title: "What context is missing",
            items: unique(
              claim.evidence
                .filter((item) => item.used_in_verdict)
                .map((item) => MISSING_CONTEXT[item.validation_reason ?? ""]),
            ),
          },
          { title: "Why it matters", items: [CONTEXT_MATTERS] },
        ],
        after: [],
      };
    case "unverified_conflicting":
      return {
        before: [
          { title: "How the sources disagree", items: sentences },
          { title: "Why it remains unresolved", items: unique([claim.explanation]) },
        ],
        after: [],
      };
    case "unverified_insufficient": {
      const gaps = plainGaps(claim.gaps);
      return {
        before: [
          {
            title: "Why we couldn't verify it",
            items: gaps.length > 0 ? gaps : unique([claim.explanation]),
          },
        ],
        after: [{ title: "What this means", items: [claim.evidence.length > 0 ? WEAK_EVIDENCE : NO_PROOF_OF_FALSITY] }],
      };
    }
    default:
      // The verdict description already says what happened.
      return { before: [], after: [] };
  }
}

/** Group evidence for display, with sides kept apart when sources conflict. */
export function evidenceGroups(claim: ClaimView) {
  if (claim.verdict === "unverified_conflicting") {
    return (["supports", "contradicts"] as const).map((stance) => ({
      title: STANCE_SECTIONS[stance],
      items: strongestFirst(claim.evidence.filter((item) => item.stance === stance)),
    }));
  }
  const title = claim.verdict === "unverified_insufficient" ? "Evidence found" : "Evidence";
  return [{ title, items: strongestFirst(claim.evidence) }];
}

"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import AuditProgress, { formatDuration } from "@/components/AuditProgress";
import ClaimReport, { EvidenceGroup } from "@/components/ClaimReport";
import EvidenceLedger from "@/components/EvidenceLedger";
import InputDetails from "@/components/InputDetails";
import LiveTrace from "@/components/LiveTrace";
import SourceHistory from "@/components/SourceHistory";
import VerdictBadge from "@/components/VerdictBadge";
import {
  cancelAudit,
  createAudit,
  getAudit,
  getEvents,
  type Audit,
  type TraceEvent,
} from "@/lib/api";
import { rememberAudit } from "@/lib/history";
import {
  NO_PROOF_OF_FALSITY,
  VERDICT_COPY,
  WEAK_EVIDENCE,
  resultSummary,
  strongestFirst,
  verdictTone,
} from "@/lib/verdicts";

const POLL_MS = 2000;
const FINISHED = new Set(["completed", "failed", "cancelled"]);

export default function AuditPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [audit, setAudit] = useState<Audit | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    let active = true;
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      try {
        const [batch, latest] = await Promise.all([getEvents(id, cursor), getAudit(id)]);
        if (!active) return;
        cursor = batch.last_id;
        if (batch.events.length > 0) setEvents((current) => [...current, ...batch.events]);
        setAudit(latest);
        setError(null);
        rememberAudit({
          id,
          text: latest.input_text,
          createdAt: latest.created_at,
          result: latest.status === "completed" ? latest.overall_result : latest.status,
        });
        if (batch.finished && batch.events.length === 0) return;
      } catch {
        if (active) setError("Reconnecting to the verification service...");
      }
      if (active) timer = setTimeout(poll, POLL_MS);
    }

    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [id]);

  async function cancel() {
    setCancelling(true);
    try {
      await cancelAudit(id);
      setAudit(await getAudit(id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The check could not be cancelled.");
    }
    setCancelling(false);
  }

  async function runAgain() {
    if (!audit) return;
    setRestarting(true);
    setError(null);
    try {
      const next = await createAudit(audit.input_type as "text" | "url", audit.input_text, true);
      router.push(`/audit/${next.audit_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The check could not be started.");
      setRestarting(false);
    }
  }

  const finished = audit !== null && FINISHED.has(audit.status);
  const report = audit?.report ?? null;
  // Reports saved before plain answers existed still carry bracket labels.
  const answer =
    report?.answer ??
    (report?.summary
      ? [{ text: report.summary.replace(/\s*\[L\d+\]/g, ""), sources: [] }]
      : []);
  const claims = report?.claims ?? [];
  // Older saved reports don't link sentences to claims.
  const linked = answer.some((sentence) => sentence.claim_id);
  const answerFor = (claimId: string) =>
    linked
      ? answer.filter((sentence) => sentence.claim_id === claimId)
      : claims.length === 1
        ? answer
        : [];
  const seenQuotes = new Set<string>();
  const evidence = strongestFirst(claims.flatMap((claim) => claim.evidence)).filter((item) => {
    const isNew = !seenQuotes.has(item.quote);
    seenQuotes.add(item.quote);
    return isNew;
  });
  const insufficient = report?.overall_result === "unverified_insufficient";
  const manyClaims = claims.length > 1;
  // Fallback sentences repeat a quote per claim, which the claim list covers better.
  const summary = manyClaims ? answer.filter((sentence) => !sentence.fallback) : answer;
  const copy = report ? VERDICT_COPY[report.overall_result] : undefined;
  // Claims with no evidence get fixed wording, so nothing on the page is AI-written.
  const aiWritten = claims.some((claim) => claim.evidence.some((item) => item.used_in_verdict));
  const histories = claims.flatMap((claim) => report?.source_history[claim.id] ?? []);

  return (
    <div className="space-y-6">
      {error && <p className="text-sm text-amber-300">{error}</p>}
      {audit === null && !error && <div className="card h-48 animate-pulse" />}

      {audit && !finished && (
        <AuditProgress audit={audit} onCancel={cancel} cancelling={cancelling} />
      )}

      {report && (
        <section className={`card verdict-${verdictTone(report.overall_result)} space-y-3 p-7`}>
          <VerdictBadge verdict={report.overall_result} />
          <h1 className="text-4xl font-semibold tracking-tight text-white">
            {copy?.headline ?? "Audit complete"}
          </h1>
          {manyClaims && <p className="text-lg font-medium text-white">{resultSummary(claims)}</p>}
          {answer.length === 0 && copy && (
            <p className="text-lg text-slate-300">{copy.description}</p>
          )}
          {summary.map((sentence) => (
            <p key={sentence.text} className="text-lg break-words text-slate-100">
              {sentence.text}
            </p>
          ))}
          {insufficient && (
            <p className="text-slate-300">
              {evidence.length > 0 ? WEAK_EVIDENCE : NO_PROOF_OF_FALSITY}
            </p>
          )}
          <div className="space-y-1 pt-1 text-sm text-slate-400">
            {audit && <p>Finished in {formatDuration(audit.elapsed_seconds)}.</p>}
            {report.source_page && (
              <p className="break-words">
                Selected on{" "}
                <a
                  href={report.source_page.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-cyan-300 underline"
                >
                  {report.source_page.title}
                </a>
              </p>
            )}
            {report.reused && report.checked_at && (
              <p>
                Saved result, checked on {new Date(report.checked_at).toLocaleDateString()}.
                Sources were not searched again.
              </p>
            )}
            {aiWritten && report.summary_label && (
              <p className="text-xs text-slate-500">{report.summary_label}</p>
            )}
          </div>
        </section>
      )}

      {audit?.status === "cancelled" && (
        <section className="card space-y-2 p-6">
          <h1 className="text-2xl font-semibold text-white">Check cancelled</h1>
          <p className="break-words text-slate-300">{audit.input_text}</p>
        </section>
      )}
      {audit?.status === "failed" && (
        <p role="alert" className="card border-red-400/30 p-5 text-red-200">
          {audit.public_error}
        </p>
      )}
      {finished && (
        <button type="button" onClick={runAgain} disabled={restarting} className="primary-button">
          {restarting ? "Starting..." : "Check again"}
        </button>
      )}

      {manyClaims && (
        <section className="card space-y-3 p-5">
          <h3 className="text-sm font-semibold text-slate-300">Each claim</h3>
          <ul className="space-y-3">
            {claims.map((claim) => (
              <li key={claim.id} className="flex items-start gap-3">
                <span className="w-32 shrink-0">
                  <VerdictBadge verdict={claim.verdict ?? "error"} />
                </span>
                <span className="min-w-0 break-words text-slate-200">
                  {claim.text}
                  {claim.confidence && (
                    <span className="block text-xs text-slate-500">
                      {claim.confidence[0].toUpperCase() + claim.confidence.slice(1)} confidence
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {evidence.length > 0 && (
        <section className="card p-5">
          <EvidenceGroup title="Evidence" items={evidence} />
        </section>
      )}

      {finished && audit && <InputDetails audit={audit} />}

      {report && report.not_investigated.length > 0 && (
        <section className="card space-y-2 p-5">
          <h3 className="font-semibold text-white">Not checked</h3>
          <ul className="list-disc pl-5 text-sm text-slate-400">
            {report.not_investigated.map((item) => (
              <li key={item.text} className="break-words">
                {item.text}
              </li>
            ))}
          </ul>
        </section>
      )}

      {audit && (
        <details open={!finished} className="card p-5">
          <summary className="cursor-pointer font-medium text-slate-200">
            Investigation details{!finished && " (running)"}
          </summary>
          <div className="mt-4 space-y-5">
            {claims.length > 0 && <h3 className="text-sm font-semibold">Claim by claim</h3>}
            {claims.map((claim) => (
              <ClaimReport
                key={claim.id}
                claim={claim}
                answer={answerFor(claim.id)}
                showVerdict={claims.length > 1}
              />
            ))}
            <LiveTrace events={events} finished={finished} />
            {report && <EvidenceLedger entries={report.ledger} />}
            {histories.map((history, index) => (
              <SourceHistory key={index} history={history} />
            ))}
          </div>
        </details>
      )}

      <p className="text-center text-sm">
        <Link href="/audits" className="text-slate-400 underline hover:text-white">
          See all your audits
        </Link>
      </p>
    </div>
  );
}

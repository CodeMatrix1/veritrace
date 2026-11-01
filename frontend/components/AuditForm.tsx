"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { createAudit, getHealth } from "@/lib/api";
import { rememberAudit } from "@/lib/history";

const MAX_CHARS = 12000;

export default function AuditForm() {
  const router = useRouter();
  const [content, setContent] = useState("");
  const [fresh, setFresh] = useState(false);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // The chatbot's "Fact-check this answer" link arrives with text to check.
  useEffect(() => {
    const text = new URLSearchParams(window.location.search).get("text");
    if (text) setContent(text.slice(0, MAX_CHARS));
  }, []);

  useEffect(() => {
    getHealth()
      .then((health) => setRemaining(health.daily_audits_remaining))
      .catch(() => setError("The verification service is not reachable right now."));
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const audit = await createAudit("text", content.trim(), fresh);
      rememberAudit({
        id: audit.audit_id,
        text: content.trim(),
        createdAt: new Date().toISOString(),
        result: null,
      });
      router.push(`/audit/${audit.audit_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The audit could not be started.");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="card space-y-4 p-5 sm:p-6">
      <textarea
        aria-label="Text to check"
        value={content}
        maxLength={MAX_CHARS}
        onChange={(event) => setContent(event.target.value)}
        rows={5}
        placeholder="India's GDP grew by 8% in FY2024-25."
        className="field"
      />

      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          checked={fresh}
          onChange={(event) => setFresh(event.target.checked)}
        />
        Search again, even if this text was checked recently
      </label>

      {error && (
        <p role="alert" className="text-sm text-red-300">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500">
          {remaining !== null && `${remaining} left today, shared with Ask. `}
          Audits are deleted after 30 days. Do not submit confidential text.
        </p>
        <button
          type="submit"
          disabled={submitting || content.trim().length < 3 || remaining === 0}
          className="primary-button"
        >
          {submitting ? "Starting..." : "Run audit"}
        </button>
      </div>
    </form>
  );
}

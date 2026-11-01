import type { Audit } from "@/lib/api";

export default function InputDetails({ audit }: { audit: Audit }) {
  return (
    <section className="card space-y-2 p-5">
      <h3 className="text-sm font-semibold text-slate-300">Text verified</h3>
      <p className="max-h-40 overflow-y-auto rounded-lg bg-black/25 p-3 break-words whitespace-pre-wrap text-slate-200">
        {audit.input_text}
      </p>
      <p className="text-xs text-slate-500">Only this text was used to find claims.</p>
    </section>
  );
}

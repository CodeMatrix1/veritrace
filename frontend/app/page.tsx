import AuditForm from "@/components/AuditForm";
import AuditList from "@/components/AuditList";

const STEPS = [
  { title: "Finds the claims", text: "Separates checkable facts from opinion." },
  { title: "Searches both sides", text: "Looks for proof and for contradictions." },
  { title: "Quotes the source", text: "Every quote is checked against the saved page." },
];

export default function HomePage() {
  return (
    <div className="space-y-10">
      <section className="space-y-4 pt-6 text-center">
        <p className="text-xs font-semibold tracking-[0.25em] text-cyan-300 uppercase">
          Evidence, not opinion
        </p>
        <h1 className="hero-title mx-auto max-w-3xl text-5xl font-semibold tracking-tight sm:text-6xl">
          Check any public claim against the source.
        </h1>
        <p className="mx-auto max-w-2xl text-lg text-slate-400">
          Veritrace searches both sides, quotes the exact text, and tells you what it could not
          establish. It works best on official statistics, monetary policy, space missions and
          public health.
        </p>
      </section>

      <AuditForm />

      <section className="grid gap-3 sm:grid-cols-3">
        {STEPS.map((step) => (
          <div key={step.title} className="card p-4">
            <h2 className="font-semibold text-white">{step.title}</h2>
            <p className="mt-1 text-sm text-slate-400">{step.text}</p>
          </div>
        ))}
      </section>

      <AuditList limit={5} />
    </div>
  );
}

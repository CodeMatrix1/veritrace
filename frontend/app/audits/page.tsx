import AuditList from "@/components/AuditList";

export default function AuditsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-white">Your audits</h1>
        <p className="mt-1 text-slate-400">
          Audits from this browser and the Veritrace extension. Each one is kept for 30 days.
        </p>
      </div>
      <AuditList />
    </div>
  );
}

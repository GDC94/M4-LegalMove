import type { ErrorEnvelope } from "@/api/contract";
import { StartOverButton } from "./start-over-button";

export function HumanReviewView({
  envelope,
  onReset,
}: {
  envelope: ErrorEnvelope;
  onReset: () => void;
}) {
  const issues = envelope.issues ?? [];
  return (
    <section
      aria-labelledby="review-title"
      className="space-y-4 rounded-3xl border border-warning/50 bg-card p-6"
    >
      <h2 id="review-title" className="text-lg font-semibold text-foreground">
        Needs human review
      </h2>
      <p className="text-sm text-foreground">
        The result could not be validated automatically, so a person must review this pair.
      </p>
      <p className="text-sm text-muted-foreground">{envelope.message}</p>
      {issues.length > 0 && (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Issues found</caption>
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th scope="col" className="py-1 pr-4 font-medium">
                Code
              </th>
              <th scope="col" className="py-1 pr-4 font-medium">
                Where
              </th>
              <th scope="col" className="py-1 font-medium">
                Why
              </th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue) => (
              <tr key={`${issue.code}-${issue.path}`} className="border-t border-border align-top">
                <td className="py-2 pr-4 font-mono text-xs">{issue.code}</td>
                <td className="py-2 pr-4 font-mono text-xs">{issue.path}</td>
                <td className="py-2">{issue.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <TraceNote runId={envelope.run_id} traceId={envelope.trace_id} />
      <StartOverButton onReset={onReset} />
    </section>
  );
}

/** Compliance finds the run in Langfuse with these ids. */
export function TraceNote({ runId, traceId }: { runId: string; traceId: string | null }) {
  return (
    <p className="font-mono text-xs text-muted-foreground">
      run {runId}
      {traceId ? ` · trace ${traceId}` : ""}
    </p>
  );
}

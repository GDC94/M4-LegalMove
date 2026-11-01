import type { ErrorEnvelope } from "@/api/contract";
import { TraceNote } from "./human-review-view";
import { StartOverButton } from "./start-over-button";

/** A `failed` body, a refusal or a contract error: what happened and a way back to the form. */
export function ErrorView({
  message,
  envelope,
  onReset,
}: {
  message: string;
  envelope?: ErrorEnvelope;
  onReset: () => void;
}) {
  return (
    <div role="alert" className="space-y-4 rounded-3xl border border-destructive/40 bg-card p-6">
      <h2 className="text-lg font-semibold text-foreground">The analysis could not finish</h2>
      <p className="text-sm text-foreground">{message}</p>
      {envelope && <TraceNote runId={envelope.run_id} traceId={envelope.trace_id} />}
      <StartOverButton onReset={onReset} />
    </div>
  );
}

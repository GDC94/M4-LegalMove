import { useAnalysis } from "@/analysis/use-analysis";
import type { ApiClient } from "@/api/client";
import { ErrorView } from "@/components/error-view";
import { HumanReviewView } from "@/components/human-review-view";
import { ResultView } from "@/components/result-view";
import { StageStepper } from "@/components/stage-stepper";
import { UploadForm } from "@/components/upload-form";

const DEFAULT_POLL_INTERVAL_MS = 2000;

/** The one screen: the container that owns the analysis and picks the view for its phase. */
export function AnalysisScreen({
  client,
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}: {
  client: ApiClient;
  pollIntervalMs?: number;
}) {
  const { state, submit, reset } = useAnalysis(client, pollIntervalMs);
  const stages = state.phase === "idle" || state.phase === "uploading" ? undefined : state.stages;

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-10 sm:px-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">LegalMove</h1>
        <p className="text-sm text-muted-foreground">
          Upload a scanned contract and its amendment to see the legal changes the amendment
          introduces.
        </p>
      </header>
      {(state.phase === "idle" || state.phase === "uploading") && (
        <UploadForm busy={state.phase === "uploading"} onSubmit={submit} />
      )}
      {stages && <StageStepper stages={stages} />}
      {state.phase === "done" && <ResultView output={state.output} onReset={reset} />}
      {state.phase === "human_review" && (
        <HumanReviewView envelope={state.envelope} onReset={reset} />
      )}
      {state.phase === "failed" && (
        <ErrorView message={state.message} envelope={state.envelope} onReset={reset} />
      )}
    </main>
  );
}

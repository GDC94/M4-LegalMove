import { AnimatePresence } from "motion/react";
import { useAnalysis } from "@/analysis/use-analysis";
import type { ApiClient } from "@/api/client";
import { ErrorView } from "@/components/error-view";
import { HumanReviewView } from "@/components/human-review-view";
import { ResultView } from "@/components/result-view";
import { describeProgress, StageStepper } from "@/components/stage-stepper";
import { UploadForm } from "@/components/upload-form";
import { ViewTransition } from "@/components/view-transition";

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
  const isForm = state.phase === "idle" || state.phase === "uploading";
  const stages = isForm ? undefined : state.stages;

  return (
    <main className="mx-auto max-w-3xl space-y-8 px-4 py-10 sm:px-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">LegalMove</h1>
        <p className="text-sm text-muted-foreground">
          Upload a scanned contract and its amendment to see the legal changes the amendment
          introduces.
        </p>
      </header>
      {/* `wait`: the form leaves before the stepper enters, so the two never share the page. */}
      <AnimatePresence mode="wait" initial={false}>
        {isForm ? (
          <ViewTransition key="form">
            <UploadForm busy={state.phase === "uploading"} onSubmit={submit} />
          </ViewTransition>
        ) : (
          <ViewTransition key="analysis" className="space-y-8">
            {stages && <StageStepper stages={stages} />}
            <AnimatePresence initial={false}>
              {state.phase === "done" && (
                <ViewTransition key="done">
                  <ResultView output={state.output} onReset={reset} />
                </ViewTransition>
              )}
              {state.phase === "human_review" && (
                <ViewTransition key="human_review">
                  <HumanReviewView envelope={state.envelope} onReset={reset} />
                </ViewTransition>
              )}
              {state.phase === "failed" && (
                <ViewTransition key="failed">
                  <ErrorView message={state.message} envelope={state.envelope} onReset={reset} />
                </ViewTransition>
              )}
            </AnimatePresence>
          </ViewTransition>
        )}
      </AnimatePresence>
      {/* Outside the animated views: with `wait` the stepper mounts after the form's exit, and a
          live region only announces changes, so one born inside it would swallow early stages. */}
      <p role="status" aria-live="polite" className="sr-only">
        {stages ? describeProgress(stages) : ""}
      </p>
    </main>
  );
}

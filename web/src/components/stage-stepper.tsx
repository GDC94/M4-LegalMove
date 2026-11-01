import { Check, Circle, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { StageBoard, StageProgress } from "@/analysis/machine";
import { STAGE_NAMES } from "@/api/contract";
import { cn } from "@/lib/utils";
import { STAGE_LABELS } from "./stage-labels";

const STATUS_TEXT: Record<StageProgress, string> = {
  pending: "Pending",
  running: "In progress",
  done: "Done",
  failed: "Failed",
};

/**
 * The five Stages in pipeline order; a polite live region reads out the latest change. The
 * stepper replaces the upload form, so it takes the focus the form's button held.
 */
export function StageStepper({ stages }: { stages: StageBoard }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);

  return (
    <section aria-labelledby="progress-title" className="space-y-4">
      <h2
        ref={heading}
        id="progress-title"
        tabIndex={-1}
        className="text-sm font-semibold text-foreground focus:outline-none"
      >
        Analysis progress
      </h2>
      <ol className="space-y-2">
        {STAGE_NAMES.map((name) => (
          <StepItem key={name} label={STAGE_LABELS[name]} progress={stages[name]} />
        ))}
      </ol>
      <p role="status" aria-live="polite" className="sr-only">
        {describeProgress(stages)}
      </p>
    </section>
  );
}

function StepItem({ label, progress }: { label: string; progress: StageProgress }) {
  return (
    <li
      aria-current={progress === "running" ? "step" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm",
        progress === "failed" && "border-destructive/40 text-destructive",
        progress === "pending" && "text-muted-foreground",
      )}
    >
      <StepIcon progress={progress} />
      <span className="flex-1 font-medium">{label}</span>
      <span data-testid="step-status" className="text-xs text-muted-foreground">
        {STATUS_TEXT[progress]}
      </span>
    </li>
  );
}

function StepIcon({ progress }: { progress: StageProgress }) {
  const className = "size-4 shrink-0";
  if (progress === "done")
    return <Check aria-hidden="true" className={cn(className, "text-success")} />;
  if (progress === "failed") return <X aria-hidden="true" className={className} />;
  if (progress === "running") {
    return (
      <LoaderCircle
        aria-hidden="true"
        className={cn(className, "animate-spin motion-reduce:animate-none")}
      />
    );
  }
  return <Circle aria-hidden="true" className={className} />;
}

/** One sentence for screen readers: a failure first, then the Stage at work, then the last one done. */
export function describeProgress(stages: StageBoard): string {
  const entries = STAGE_NAMES.map((name) => [name, stages[name]] as const);
  const failed = entries.find(([, progress]) => progress === "failed");
  if (failed) return `${STAGE_LABELS[failed[0]]}: failed`;
  const running = entries.find(([, progress]) => progress === "running");
  if (running) return `${STAGE_LABELS[running[0]]}: in progress`;
  const done = entries.filter(([, progress]) => progress === "done").at(-1);
  if (done) return `${STAGE_LABELS[done[0]]}: done`;
  return "Waiting for the analysis to start";
}

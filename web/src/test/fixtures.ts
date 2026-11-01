/** Bodies and SSE frames shaped exactly as the API sends them. */
import type { AnalysisBody, ProgressEvent, StageName } from "@/api/contract";

// Same origin as the page, like the `/api` proxy in development: no CORS preflight to mock.
export const API = `${window.location.origin}/api`;
export const KEY = "demo-key";
export const JOB_ID = "4f1c";

export function pngFile(name = "original.png", bytes = 64): File {
  return new File([new Uint8Array(bytes)], name, { type: "image/png" });
}

export function stage(seq: number, name: StageName, status: "started" | "done" | "failed") {
  return { seq, kind: "stage", stage: name, status, at: "2026-10-29T18:03:11.412Z" } as const;
}

export function finished(seq: number, status: "done" | "human_review" | "failed") {
  return { seq, kind: "job", status, at: "2026-10-29T18:03:40.009Z" } as const;
}

export function sseFrames(...events: ProgressEvent[]): string {
  return events.map((event) => `id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`).join("");
}

export function sseBody(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
}

const BASE = { id: JOB_ID, created_at: "2026-10-29T18:03:00.000Z", trace_id: "trace-1" };

export const RUNNING_BODY: AnalysisBody = { ...BASE, status: "running" };

export const DONE_BODY: AnalysisBody = {
  ...BASE,
  status: "done",
  output: {
    sections_changed: ["3", "new:5"],
    topics_touched: ["price", "term"],
    summary_of_the_change: "The monthly price rises from 100 to 120 and a renewal clause is added.",
  },
};

export const HUMAN_REVIEW_BODY: AnalysisBody = {
  ...BASE,
  status: "human_review",
  error: {
    error: "InvalidExtractionError",
    stage: "extraction",
    message: "extraction is still invalid after one retry: ungrounded_quote",
    run_id: JOB_ID,
    trace_id: "trace-1",
    issues: [
      {
        code: "ungrounded_quote",
        path: "changes[0].original_quote",
        message: "the quote is not in the original contract",
      },
    ],
  },
};

export const FAILED_BODY: AnalysisBody = {
  ...BASE,
  status: "failed",
  error: {
    error: "UpstreamError",
    stage: "parse",
    message: "vision call failed",
    run_id: JOB_ID,
    trace_id: "trace-1",
  },
};

/**
 * The one screen's state machine: idle → uploading → running → done | human_review | failed.
 * An event that does not fit the current phase returns the same state, untouched. `reset` leaves any
 * phase but idle, so a stuck upload or a slow analysis can always be abandoned.
 */
import {
  type AnalysisBody,
  type ContractChangeOutput,
  type ErrorEnvelope,
  type ProgressEvent,
  STAGE_NAMES,
  type StageName,
} from "@/api/contract";

/** `cached` is a `done` Stage the API served from its parse cache, so it finished at once. */
export type StageProgress = "pending" | "running" | "done" | "cached" | "failed";
export type StageBoard = Record<StageName, StageProgress>;

export type AnalysisState =
  | { phase: "idle" }
  | { phase: "uploading" }
  | { phase: "running"; jobId: string; stages: StageBoard }
  | { phase: "done"; stages: StageBoard; output: ContractChangeOutput }
  | { phase: "human_review"; stages: StageBoard; envelope: ErrorEnvelope }
  | { phase: "failed"; stages?: StageBoard; message: string; envelope?: ErrorEnvelope };

export type AnalysisEvent =
  | { type: "submit" }
  | { type: "created"; jobId: string }
  | { type: "progress"; event: ProgressEvent }
  | { type: "settled"; body: AnalysisBody }
  | { type: "error"; message: string }
  | { type: "reset" };

export const INITIAL_STATE: AnalysisState = { phase: "idle" };

const PROGRESS_BY_STATUS = { started: "running", done: "done", failed: "failed" } as const;

export function transition(state: AnalysisState, event: AnalysisEvent): AnalysisState {
  if (state.phase === "idle") return event.type === "submit" ? { phase: "uploading" } : state;
  if (event.type === "reset") return INITIAL_STATE;
  switch (state.phase) {
    case "uploading":
      return onUploading(state, event);
    case "running":
      return onRunning(state, event);
    default:
      return state;
  }
}

function onUploading(state: AnalysisState, event: AnalysisEvent): AnalysisState {
  if (event.type === "created") {
    return { phase: "running", jobId: event.jobId, stages: pendingStages() };
  }
  if (event.type === "error") return { phase: "failed", message: event.message };
  return state;
}

function onRunning(
  state: Extract<AnalysisState, { phase: "running" }>,
  event: AnalysisEvent,
): AnalysisState {
  switch (event.type) {
    case "progress":
      return event.event.kind === "stage"
        ? {
            ...state,
            stages: { ...state.stages, [event.event.stage]: progressOf(event.event) },
          }
        : state;
    case "settled":
      return settle(state, event.body);
    case "error":
      return { phase: "failed", stages: state.stages, message: event.message };
    default:
      return state;
  }
}

function progressOf(event: Extract<ProgressEvent, { kind: "stage" }>): StageProgress {
  return event.status === "done" && event.cached ? "cached" : PROGRESS_BY_STATUS[event.status];
}

function settle(
  state: Extract<AnalysisState, { phase: "running" }>,
  body: AnalysisBody,
): AnalysisState {
  const { stages } = state;
  if (body.status === "done" && body.output) return { phase: "done", stages, output: body.output };
  if (body.status === "human_review" && body.error) {
    return { phase: "human_review", stages, envelope: body.error };
  }
  if (body.status === "failed" && body.error) {
    return { phase: "failed", stages, message: body.error.message, envelope: body.error };
  }
  if (body.status === "pending" || body.status === "running") return state;
  return { phase: "failed", stages, message: "The API answered outside its contract." };
}

function pendingStages(): StageBoard {
  return Object.fromEntries(STAGE_NAMES.map((name) => [name, "pending"])) as StageBoard;
}

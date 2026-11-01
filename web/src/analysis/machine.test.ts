import { describe, expect, it } from "vitest";
import {
  cachedDone,
  DONE_BODY,
  FAILED_BODY,
  finished,
  HUMAN_REVIEW_BODY,
  JOB_ID,
  RUNNING_BODY,
  stage,
} from "@/test/fixtures";
import { type AnalysisEvent, type AnalysisState, INITIAL_STATE, transition } from "./machine";

function run(...events: AnalysisEvent[]): AnalysisState {
  return events.reduce(transition, INITIAL_STATE);
}

const RUNNING: AnalysisEvent[] = [{ type: "submit" }, { type: "created", jobId: JOB_ID }];

describe("analysis state machine", () => {
  it("walks idle, uploading and running with every stage pending", () => {
    expect(run({ type: "submit" }).phase).toBe("uploading");
    const running = run(...RUNNING);

    expect(running).toMatchObject({ phase: "running", jobId: JOB_ID });
    expect(running.phase === "running" && Object.values(running.stages)).toEqual(
      Array(5).fill("pending"),
    );
  });

  it("moves a stage to running, done or failed with its events", () => {
    const state = run(
      ...RUNNING,
      { type: "progress", event: stage(1, "parse_original_contract", "started") },
      { type: "progress", event: stage(2, "parse_original_contract", "done") },
      { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
      { type: "progress", event: stage(4, "parse_amendment_contract", "failed") },
    );

    expect(state.phase === "running" && state.stages).toMatchObject({
      parse_original_contract: "done",
      parse_amendment_contract: "failed",
      contextualization_agent: "pending",
    });
  });

  it("marks a done stage served from the parse cache as cached", () => {
    const state = run(
      ...RUNNING,
      { type: "progress", event: stage(1, "parse_original_contract", "started") },
      { type: "progress", event: cachedDone(2, "parse_original_contract") },
      { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
      { type: "progress", event: stage(4, "parse_amendment_contract", "done") },
    );

    expect(state.phase === "running" && state.stages).toMatchObject({
      parse_original_contract: "cached",
      parse_amendment_contract: "done",
    });
  });

  it("settles into the terminal state the body names", () => {
    expect(run(...RUNNING, { type: "settled", body: DONE_BODY })).toMatchObject({
      phase: "done",
      output: DONE_BODY.output,
      changes: DONE_BODY.changes,
      amendment_kind: "delta",
    });
    expect(run(...RUNNING, { type: "settled", body: HUMAN_REVIEW_BODY })).toMatchObject({
      phase: "human_review",
      envelope: HUMAN_REVIEW_BODY.error,
    });
    expect(run(...RUNNING, { type: "settled", body: FAILED_BODY })).toMatchObject({
      phase: "failed",
      envelope: FAILED_BODY.error,
    });
  });

  it("settles a done body without details on no changes and no kind", () => {
    const body = { ...DONE_BODY, changes: undefined, amendment_kind: undefined };

    expect(run(...RUNNING, { type: "settled", body })).toMatchObject({
      phase: "done",
      changes: [],
      amendment_kind: null,
    });
  });

  it("treats a terminal body without its payload as a contract error", () => {
    const state = run(...RUNNING, { type: "settled", body: { ...DONE_BODY, output: undefined } });

    expect(state.phase).toBe("failed");
    expect(state).not.toHaveProperty("envelope");
  });

  it("ignores a body that is not terminal yet", () => {
    const running = run(...RUNNING);

    expect(transition(running, { type: "settled", body: RUNNING_BODY })).toBe(running);
  });

  it("fails from uploading or running with a message", () => {
    expect(run({ type: "submit" }, { type: "error", message: "413" })).toMatchObject({
      phase: "failed",
      message: "413",
    });
  });

  it.each<[string, AnalysisEvent[]]>([
    ["uploading", [{ type: "submit" }]],
    ["running", RUNNING],
    ["done", [...RUNNING, { type: "settled", body: DONE_BODY }]],
    ["failed", [{ type: "submit" }, { type: "error", message: "413" }]],
  ])("resets to idle from %s", (_, before) => {
    expect(transition(run(...before), { type: "reset" })).toBe(INITIAL_STATE);
  });

  it.each<[string, AnalysisEvent[], AnalysisEvent]>([
    ["created before any submit", [], { type: "created", jobId: JOB_ID }],
    ["a second submit while uploading", [{ type: "submit" }], { type: "submit" }],
    [
      "progress before the job exists",
      [{ type: "submit" }],
      { type: "progress", event: stage(1, "parse_original_contract", "started") },
    ],
    ["a reset while idle", [], { type: "reset" }],
    ["a settled body while idle", [], { type: "settled", body: DONE_BODY }],
    [
      "progress after the end",
      [...RUNNING, { type: "settled", body: DONE_BODY }],
      { type: "progress", event: stage(9, "output_validation", "failed") },
    ],
    [
      "a second end after the first",
      [...RUNNING, { type: "settled", body: DONE_BODY }],
      { type: "settled", body: FAILED_BODY },
    ],
    [
      "an error after the end",
      [...RUNNING, { type: "settled", body: HUMAN_REVIEW_BODY }],
      { type: "error", message: "late" },
    ],
  ])("refuses %s", (_, before, event) => {
    const state = run(...before);

    expect(transition(state, event)).toBe(state);
  });

  it("does not let a job event alone end the analysis: the body does", () => {
    const running = run(...RUNNING);

    expect(transition(running, { type: "progress", event: finished(6, "done") })).toBe(running);
  });
});

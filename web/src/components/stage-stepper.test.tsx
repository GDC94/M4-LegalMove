import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  type AnalysisEvent,
  type AnalysisState,
  INITIAL_STATE,
  transition,
} from "@/analysis/machine";
import { cachedDone, JOB_ID, stage } from "@/test/fixtures";
import { describeProgress, StageStepper } from "./stage-stepper";

function stagesAfter(...events: AnalysisEvent[]) {
  const state: AnalysisState = [
    { type: "submit" } as const,
    { type: "created", jobId: JOB_ID } as const,
    ...events,
  ].reduce(transition, INITIAL_STATE);
  if (state.phase !== "running") throw new Error("expected a running analysis");
  return state.stages;
}

function statusOf(label: string): string {
  const item = screen.getByText(label).closest("li");
  if (!item) throw new Error(`no step for ${label}`);
  return within(item).getByTestId("step-status").textContent ?? "";
}

describe("StageStepper", () => {
  it("advances with the stage events", () => {
    const { rerender } = render(<StageStepper stages={stagesAfter()} />);
    expect(statusOf("Read the original contract")).toBe("Pending");

    rerender(
      <StageStepper
        stages={stagesAfter(
          { type: "progress", event: stage(1, "parse_original_contract", "started") },
          { type: "progress", event: stage(2, "parse_original_contract", "done") },
          { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
        )}
      />,
    );

    expect(statusOf("Read the original contract")).toBe("Done");
    expect(statusOf("Read the amendment")).toBe("In progress");
    expect(screen.getByText("Read the amendment").closest("li")).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("labels a stage served from the parse cache as Cached, and says so to screen readers", () => {
    const stages = stagesAfter(
      { type: "progress", event: stage(1, "parse_original_contract", "started") },
      { type: "progress", event: cachedDone(2, "parse_original_contract") },
      { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
      { type: "progress", event: cachedDone(4, "parse_amendment_contract") },
    );
    render(<StageStepper stages={stages} />);

    const item = screen.getByText("Read the original contract").closest("li");
    if (!item) throw new Error("no step for the original contract");
    expect(within(item).getByText("Cached")).toHaveAttribute("aria-hidden", "true");
    expect(within(item).getByText("Done (from cache)")).toHaveClass("sr-only");
    expect(within(item).queryByText("Done")).not.toBeInTheDocument();
    expect(describeProgress(stages)).toBe("Read the amendment: done (from cache)");
  });

  it("marks the stage that failed and says so", () => {
    const stages = stagesAfter(
      { type: "progress", event: stage(1, "parse_original_contract", "started") },
      { type: "progress", event: stage(2, "parse_original_contract", "done") },
      { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
      { type: "progress", event: stage(4, "parse_amendment_contract", "done") },
      { type: "progress", event: stage(5, "contextualization_agent", "started") },
      { type: "progress", event: stage(6, "contextualization_agent", "failed") },
    );
    render(<StageStepper stages={stages} />);

    expect(statusOf("Map the sections")).toBe("Failed");
    expect(statusOf("Extract the changes")).toBe("Pending");
    expect(describeProgress(stages)).toBe("Map the sections: failed");
  });

  it("describes the stage at work for the screen's live region", () => {
    const stages = stagesAfter(
      { type: "progress", event: stage(1, "parse_original_contract", "started") },
      { type: "progress", event: stage(2, "parse_original_contract", "done") },
      { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
    );

    expect(describeProgress(stages)).toBe("Read the amendment: in progress");
    expect(describeProgress(stagesAfter())).toBe("Waiting for the analysis to start");
  });
});

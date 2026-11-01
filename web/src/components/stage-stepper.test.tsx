import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  type AnalysisEvent,
  type AnalysisState,
  INITIAL_STATE,
  transition,
} from "@/analysis/machine";
import { JOB_ID, stage } from "@/test/fixtures";
import { StageStepper } from "./stage-stepper";

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
  it("advances with the stage events and announces each change politely", () => {
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
    const live = screen.getByRole("status");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(live).toHaveTextContent("Read the amendment: in progress");
  });

  it("marks the stage that failed and says so", () => {
    render(
      <StageStepper
        stages={stagesAfter(
          { type: "progress", event: stage(1, "parse_original_contract", "started") },
          { type: "progress", event: stage(2, "parse_original_contract", "done") },
          { type: "progress", event: stage(3, "parse_amendment_contract", "started") },
          { type: "progress", event: stage(4, "parse_amendment_contract", "done") },
          { type: "progress", event: stage(5, "contextualization_agent", "started") },
          { type: "progress", event: stage(6, "contextualization_agent", "failed") },
        )}
      />,
    );

    expect(statusOf("Map the sections")).toBe("Failed");
    expect(statusOf("Extract the changes")).toBe("Pending");
    expect(screen.getByRole("status")).toHaveTextContent("Map the sections: failed");
  });
});

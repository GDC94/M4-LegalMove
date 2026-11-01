import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DONE_BODY, PRICE_CHANGE } from "@/test/fixtures";
import { ResultView } from "./result-view";

const OUTPUT = DONE_BODY.output ?? {
  sections_changed: [],
  topics_touched: [],
  summary_of_the_change: "",
};

describe("ResultView", () => {
  it("shows the change details under the summary and the amendment kind badge", () => {
    render(
      <ResultView
        output={OUTPUT}
        changes={[PRICE_CHANGE]}
        amendmentKind="delta"
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Change details" })).toBeInTheDocument();
    expect(screen.getByText("delta")).toBeInTheDocument();
    expect(
      screen.getAllByRole("listitem").some((item) => item.textContent?.includes("Price")),
    ).toBe(true);
  });

  it("hides the change details section when there are no changes", () => {
    render(<ResultView output={OUTPUT} changes={[]} amendmentKind={null} onReset={vi.fn()} />);

    expect(screen.queryByRole("heading", { name: "Change details" })).not.toBeInTheDocument();
    expect(screen.queryByText("delta")).not.toBeInTheDocument();
  });
});

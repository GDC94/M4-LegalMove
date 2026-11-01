import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ChangeDetail } from "@/api/contract";
import { PRICE_CHANGE, RENEWAL_CHANGE } from "@/test/fixtures";
import { ChangeDiffList } from "./change-diff-list";

const REPEAL: ChangeDetail = {
  section_id: "7",
  section_title: "Subletting",
  change_type: "removed",
  topic: "assignment",
  description: "Subletting is no longer allowed.",
  original_quote: "The tenant may sublet.",
  amendment_quote: "Clause 7 is hereby deleted.",
};

function cards() {
  return screen.getAllByRole("listitem");
}

describe("ChangeDiffList", () => {
  it("renders one card per change, two for the same section", () => {
    const second = { ...PRICE_CHANGE, description: "A second price edit." };

    render(
      <ChangeDiffList changes={[PRICE_CHANGE, second, RENEWAL_CHANGE]} amendmentKind="delta" />,
    );

    expect(cards()).toHaveLength(3);
    expect(within(cards()[0] as HTMLElement).getByText("3")).toBeInTheDocument();
    expect(within(cards()[1] as HTMLElement).getByText("A second price edit.")).toBeInTheDocument();
  });

  it("shows the section title, or only the id when there is none, with the type badge", () => {
    render(<ChangeDiffList changes={[PRICE_CHANGE, RENEWAL_CHANGE]} amendmentKind="delta" />);

    const [titled, untitled] = cards() as [HTMLElement, HTMLElement];
    expect(within(titled).getByText("Price")).toBeInTheDocument();
    expect(within(titled).getByText("modified")).toBeInTheDocument();
    expect(within(untitled).getByText("new:5")).toBeInTheDocument();
    expect(within(untitled).getByText("added")).toBeInTheDocument();
    expect(untitled.textContent).not.toContain("null");
  });

  it("marks the changed words with del and ins", () => {
    const { container } = render(
      <ChangeDiffList changes={[PRICE_CHANGE]} amendmentKind="restated" />,
    );

    expect(container.querySelector("del")).toHaveTextContent("100");
    expect(container.querySelector("ins")).toHaveTextContent("120");
  });

  it("shows the topic and the description under the header", () => {
    render(<ChangeDiffList changes={[PRICE_CHANGE]} amendmentKind="restated" />);

    expect(screen.getByText("price payment")).toBeInTheDocument();
    expect(screen.getByText(PRICE_CHANGE.description)).toBeInTheDocument();
  });

  it("shows a delta removal's repeal instruction as a labelled neutral line", () => {
    render(<ChangeDiffList changes={[REPEAL]} amendmentKind="delta" />);

    expect(screen.getByText("Amendment instruction")).toBeInTheDocument();
    expect(screen.getByText("Clause 7 is hereby deleted.")).toBeInTheDocument();
    expect(screen.getByText("The tenant may sublet.")).toBeInTheDocument();
  });

  it("does not show an instruction line for a restated removal", () => {
    render(<ChangeDiffList changes={[REPEAL]} amendmentKind="restated" />);

    expect(screen.queryByText("Amendment instruction")).not.toBeInTheDocument();
  });

  it("starts open and folds and unfolds with aria-expanded", async () => {
    const user = userEvent.setup();
    render(<ChangeDiffList changes={[PRICE_CHANGE]} amendmentKind="restated" />);
    const toggle = screen.getByRole("button", { name: "Section 3 details" });

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });
});

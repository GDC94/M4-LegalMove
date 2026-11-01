import { describe, expect, it } from "vitest";
import type { ChangeDetail } from "@/api/contract";
import { PRICE_CHANGE, RENEWAL_CHANGE } from "@/test/fixtures";
import { changeLines } from "./change-lines";

const REMOVAL: ChangeDetail = {
  ...PRICE_CHANGE,
  change_type: "removed",
  original_quote: "The tenant may sublet.",
  amendment_quote: "Clause 7 is hereby deleted.",
};

describe("changeLines", () => {
  it("shows a modification as a removed and an added line with word-level fragments", () => {
    const [removed, added] = changeLines(PRICE_CHANGE, "restated");

    expect(removed).toEqual({
      kind: "removed",
      fragments: [
        { text: "The monthly price is USD ", changed: false },
        { text: "100", changed: true },
        { text: ".", changed: false },
      ],
    });
    expect(added).toEqual({
      kind: "added",
      fragments: [
        { text: "The monthly price is USD ", changed: false },
        { text: "120", changed: true },
        { text: ".", changed: false },
      ],
    });
  });

  it("ignores whitespace differences between the quotes", () => {
    const spaced = { ...PRICE_CHANGE, amendment_quote: "The  monthly price is\nUSD 120." };

    const [removed] = changeLines(spaced, "restated");

    expect(removed?.kind === "removed" && removed.fragments.filter((f) => f.changed)).toEqual([
      { text: "100", changed: true },
    ]);
  });

  it("shows an addition as a single added line", () => {
    expect(changeLines(RENEWAL_CHANGE, "delta")).toEqual([
      { kind: "added", fragments: [{ text: RENEWAL_CHANGE.amendment_quote, changed: false }] },
    ]);
  });

  it("shows a removal in a delta amendment with the repeal instruction as a neutral line", () => {
    expect(changeLines(REMOVAL, "delta")).toEqual([
      { kind: "removed", fragments: [{ text: "The tenant may sublet.", changed: false }] },
      { kind: "instruction", text: "Clause 7 is hereby deleted." },
    ]);
  });

  it("shows a removal in a restated amendment as the removed line only", () => {
    expect(changeLines({ ...REMOVAL, amendment_quote: null }, "restated")).toEqual([
      { kind: "removed", fragments: [{ text: "The tenant may sublet.", changed: false }] },
    ]);
    expect(changeLines(REMOVAL, "restated")).toHaveLength(1);
  });
});

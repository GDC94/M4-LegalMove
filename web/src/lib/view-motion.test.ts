import { describe, expect, it } from "vitest";
import { listVariants, rowVariants, VIEW_TRANSITION, viewVariants } from "./view-motion";

describe("view motion presets", () => {
  it("fade and lift a view a few pixels in about a fifth of a second", () => {
    const variants = viewVariants(false);

    expect(variants.hidden).toMatchObject({ opacity: 0, y: expect.any(Number) });
    expect(variants.shown).toMatchObject({ opacity: 1, y: 0 });
    expect(variants.gone).toMatchObject({ opacity: 0 });
    expect(VIEW_TRANSITION.duration).toBeGreaterThanOrEqual(0.2);
    expect(VIEW_TRANSITION.duration).toBeLessThanOrEqual(0.25);
  });

  it("only fade under reduced motion: no state moves anything", () => {
    for (const variants of [viewVariants(true), rowVariants(true)]) {
      for (const target of Object.values(variants)) {
        expect(target).not.toHaveProperty("y");
      }
    }
  });

  it("stagger the stepper rows, except under reduced motion", () => {
    expect(listVariants(false).shown).toMatchObject({
      transition: { staggerChildren: expect.any(Number) },
    });
    expect(listVariants(true).shown).toMatchObject({ transition: { staggerChildren: 0 } });
    expect(rowVariants(false).hidden).toMatchObject({ opacity: 0, y: expect.any(Number) });
  });
});

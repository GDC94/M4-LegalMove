/**
 * Motion presets for the screen's view swaps and the stepper rows. Under reduced motion every
 * preset only fades: nothing moves, so the swap stays readable without vestibular triggers.
 */
import type { Transition, Variants } from "motion/react";
import { EASE_OUT } from "./ease";

const VIEW_OFFSET_PX = 8;
const ROW_OFFSET_PX = 4;
const ROW_STAGGER_S = 0.04;

export const VIEW_TRANSITION = { duration: 0.22, ease: EASE_OUT } as const satisfies Transition;

function lift(reduceMotion: boolean, y: number): { y?: number } {
  return reduceMotion ? {} : { y };
}

/** `hidden` → `shown` on enter, `shown` → `gone` on exit (the old view rises as it fades). */
export function viewVariants(reduceMotion: boolean): Variants {
  return {
    hidden: { opacity: 0, ...lift(reduceMotion, VIEW_OFFSET_PX) },
    shown: { opacity: 1, ...lift(reduceMotion, 0) },
    gone: { opacity: 0, ...lift(reduceMotion, -VIEW_OFFSET_PX / 2) },
  };
}

export function listVariants(reduceMotion: boolean): Variants {
  return {
    hidden: {},
    shown: { transition: { staggerChildren: reduceMotion ? 0 : ROW_STAGGER_S } },
  };
}

export function rowVariants(reduceMotion: boolean): Variants {
  return {
    hidden: { opacity: 0, ...lift(reduceMotion, ROW_OFFSET_PX) },
    shown: { opacity: 1, ...lift(reduceMotion, 0), transition: VIEW_TRANSITION },
  };
}

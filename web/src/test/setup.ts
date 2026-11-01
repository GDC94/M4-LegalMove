import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { MotionGlobalConfig } from "motion/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./server";

// happy-dom answers no media query. Answering "reduce" for prefers-reduced-motion makes the beUI blocks
// skip their springs and layout animations, which the test DOM cannot measure anyway.
function matchMedia(query: string): MediaQueryList {
  return {
    matches: query.includes("prefers-reduced-motion: reduce"),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  };
}

// Motion still starts WAAPI animations under reduced motion, and happy-dom rejects their `finished`
// promise with an AbortError when a view unmounts mid-animation. Skipping animations entirely keeps
// the test run free of those unhandled rejections without hiding any real error.
MotionGlobalConfig.skipAnimations = true;

beforeAll(() => {
  window.matchMedia = matchMedia;
  server.listen({ onUnhandledFrame: "error" });
});
afterEach(() => {
  cleanup();
  server.resetHandlers();
});
afterAll(() => server.close());

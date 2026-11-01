import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";
import { VIEW_TRANSITION, viewVariants } from "@/lib/view-motion";

/**
 * One view of the screen, faded in and out by the AnimatePresence around it. The parent keys it
 * by view, so a swap plays the old view's exit before the new one enters.
 */
export function ViewTransition({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduceMotion = useReducedMotion() ?? false;
  return (
    <motion.div
      variants={viewVariants(reduceMotion)}
      initial="hidden"
      animate="shown"
      exit="gone"
      transition={VIEW_TRANSITION}
      className={className}
    >
      {children}
    </motion.div>
  );
}

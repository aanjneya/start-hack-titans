"use client";

import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";

interface HandWrittenTitleProps {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  /** The wrapper (size and spacing). Defaults to the big centred hero from the original component. */
  className?: string;
  titleClassName?: string;
  subtitleClassName?: string;
  /** Hover text for the title, e.g. the exact deadline behind a countdown. */
  titleTooltip?: string;
}

/**
 * A title with a hand-drawn loop that draws itself around it (KokonutUI "hand-writing-text"), light mode only.
 * The loop stretches to the wrapper and keeps an even pen width at any size, so the same component works as a page
 * hero or as a compact page title.
 */
function HandWrittenTitle({
  title = "Hand Written",
  subtitle = "Optional subtitle",
  className,
  titleClassName,
  subtitleClassName,
  titleTooltip,
}: HandWrittenTitleProps) {
  const still = useReducedMotion();
  const draw = {
    hidden: { pathLength: still ? 1 : 0, opacity: still ? 0.9 : 0 },
    visible: {
      pathLength: 1,
      opacity: 0.9,
      transition: still
        ? { duration: 0 }
        : {
            pathLength: { duration: 2.5, ease: [0.43, 0.13, 0.23, 0.96] as const },
            opacity: { duration: 0.5 },
          },
    },
  };

  return (
    <div className={cn("relative mx-auto w-full max-w-4xl py-24", className)}>
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <motion.svg
          width="100%"
          height="100%"
          viewBox="0 0 1200 600"
          preserveAspectRatio="none"
          initial="hidden"
          animate="visible"
          className="h-full w-full overflow-visible"
        >
          <motion.path
            d="M 950 90
               C 1250 300, 1050 480, 600 520
               C 250 520, 150 480, 150 300
               C 150 120, 350 80, 600 80
               C 850 80, 950 180, 950 180"
            fill="none"
            strokeWidth="3"
            vectorEffect="non-scaling-stroke"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            variants={draw}
            className="text-ink"
          />
        </motion.svg>
      </div>
      <div className="relative z-10 flex flex-col items-center justify-center text-center">
        <motion.h1
          title={titleTooltip}
          className={cn("flex items-center gap-2 text-4xl tracking-tighter text-ink md:text-6xl", titleClassName)}
          initial={still ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5, duration: 0.8 }}
        >
          {title}
        </motion.h1>
        {subtitle && (
          <motion.p
            className={cn("text-xl text-ink/80", subtitleClassName)}
            initial={still ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 1, duration: 0.8 }}
          >
            {subtitle}
          </motion.p>
        )}
      </div>
    </div>
  );
}

export { HandWrittenTitle };

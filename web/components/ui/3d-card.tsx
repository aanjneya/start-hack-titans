"use client";

// 3D tilt card (pasted component, 21st.dev "Interactive Travel Card").
// Local change: the tilt mechanics live in `TiltCard`, so other cards (the control room's action cards)
// reuse the exact same motion; `InteractiveTravelCard` keeps its original API and look on top of it.
// Tilt is skipped for people who prefer reduced motion.

import * as React from "react";
import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "framer-motion";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils"; // Your utility for merging class names

const SPRING = { damping: 15, stiffness: 150 };

export interface TiltCardProps extends Omit<React.ComponentProps<typeof motion.div>, "style" | "children"> {
  /** Largest tilt in degrees at the card's edges. */
  maxTilt?: number;
  children?: React.ReactNode;
}

/**
 * A container that tilts towards the pointer with a spring (3D, preserve-3d). Put it inside an element with a
 * CSS `perspective`, and lift children with `transform: translateZ(...)` for depth.
 */
export const TiltCard = React.forwardRef<HTMLDivElement, TiltCardProps>(
  ({ maxTilt = 10.5, className, children, onMouseMove, onMouseLeave, ...props }, ref) => {
    const reduce = useReducedMotion();
    const mouseX = useMotionValue(0);
    const mouseY = useMotionValue(0);
    const springX = useSpring(mouseX, SPRING);
    const springY = useSpring(mouseY, SPRING);
    const rotateX = useTransform(springY, [-0.5, 0.5], [`${maxTilt}deg`, `-${maxTilt}deg`]);
    const rotateY = useTransform(springX, [-0.5, 0.5], [`-${maxTilt}deg`, `${maxTilt}deg`]);

    return (
      <motion.div
        ref={ref}
        onMouseMove={(e) => {
          onMouseMove?.(e);
          if (reduce) return;
          const { width, height, left, top } = e.currentTarget.getBoundingClientRect();
          mouseX.set((e.clientX - left) / width - 0.5);
          mouseY.set((e.clientY - top) / height - 0.5);
        }}
        onMouseLeave={(e) => {
          onMouseLeave?.(e);
          mouseX.set(0);
          mouseY.set(0);
        }}
        style={reduce ? { transformStyle: "preserve-3d" } : { rotateX, rotateY, transformStyle: "preserve-3d" }}
        className={cn("relative", className)}
        {...props}
      >
        {children}
      </motion.div>
    );
  },
);
TiltCard.displayName = "TiltCard";

/**
 * Props for the InteractiveTravelCard component.
 */
export interface InteractiveTravelCardProps {
  /** The main title for the card, e.g., "Sapa Valley" */
  title: string;
  /** A subtitle or location, e.g., "Vietnam" */
  subtitle: string;
  /** The URL for the background image. */
  imageUrl: string;
  /** The text for the primary action button, e.g., "Book your trip" */
  actionText: string;
  /** The destination URL for the top-right link. */
  href: string;
  /** Callback function when the primary action button is clicked. */
  onActionClick: () => void;
  /** Optional additional class names for custom styling. */
  className?: string;
}

/**
 * A responsive and theme-adaptive travel card with a 3D tilt effect on hover.
 */
export const InteractiveTravelCard = React.forwardRef<HTMLDivElement, InteractiveTravelCardProps>(
  ({ title, subtitle, imageUrl, actionText, href, onActionClick, className }, ref) => {
    return (
      <TiltCard
        ref={ref}
        className={cn(
          // Base styles for the card container, using theme variables for border
          "h-[26rem] w-80 rounded-2xl border border-border/30 bg-transparent shadow-2xl",
          className,
        )}
      >
        <div
          style={{ transform: "translateZ(50px)", transformStyle: "preserve-3d" }}
          className="absolute inset-4 grid h-[calc(100%-2rem)] w-[calc(100%-2rem)] grid-rows-[1fr_auto] rounded-xl shadow-lg"
        >
          {/* Background Image */}
          {/* eslint-disable-next-line @next/next/no-img-element -- remote demo image, no next/image domain config */}
          <img
            src={imageUrl}
            alt={`${title}, ${subtitle}`}
            className="absolute inset-0 h-full w-full rounded-xl object-cover"
          />

          {/* Darkening overlay for better text contrast over the image */}
          <div className="absolute inset-0 h-full w-full rounded-xl bg-gradient-to-b from-black/20 via-transparent to-black/60" />

          {/* Card Content (Header & Footer) */}
          <div className="relative flex flex-col justify-between rounded-xl p-4 text-white">
            {/* Header section with text and link */}
            <div className="flex items-start justify-between">
              <div>
                <motion.h2 style={{ transform: "translateZ(50px)" }} className="text-2xl font-bold">
                  {title}
                </motion.h2>
                <motion.p style={{ transform: "translateZ(40px)" }} className="text-sm font-light text-white/80">
                  {subtitle}
                </motion.p>
              </div>
              <motion.a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                whileHover={{ scale: 1.1, rotate: "2.5deg" }}
                whileTap={{ scale: 0.9 }}
                aria-label={`Learn more about ${title}`}
                style={{ transform: "translateZ(60px)" }}
                className="flex h-10 w-10 items-center justify-center rounded-full bg-white/20 ring-1 ring-inset ring-white/30 backdrop-blur-sm transition-colors hover:bg-white/30"
              >
                <ArrowUpRight className="h-5 w-5 text-white" />
              </motion.a>
            </div>

            {/* Footer Button */}
            <motion.button
              onClick={onActionClick}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              style={{ transform: "translateZ(40px)" }}
              className={cn(
                "w-full rounded-lg py-3 text-center font-semibold text-white transition-colors",
                // Glassmorphism styling for the button, designed to work over a dark image overlay
                "bg-white/10 ring-1 ring-inset ring-white/20 backdrop-blur-md hover:bg-white/20",
              )}
            >
              {actionText}
            </motion.button>
          </div>
        </div>
      </TiltCard>
    );
  },
);
InteractiveTravelCard.displayName = "InteractiveTravelCard";

export default InteractiveTravelCard;

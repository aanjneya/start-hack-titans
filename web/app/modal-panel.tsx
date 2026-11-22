"use client";

import { useEffect, useRef, type ReactNode } from "react";

export default function ModalPanel({ label, onClose, children, className }: {
  label: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const downOnBackdrop = useRef(false);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    const root = document.documentElement;
    const before = root.style.overflow;
    root.style.overflow = "hidden";
    return () => {
      root.style.overflow = before;
      // Leaving the page (Next keeps it alive, hidden) must not leave a modal open. Safe under Strict Mode too:
      // the re-run reopens it before the queued close event arrives, and onClose ignores a dialog that is open.
      if (dialog.open) dialog.close();
      if (trigger?.isConnected && trigger !== document.body) trigger.focus({ preventScroll: true });
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-label={label}
      onClose={() => {
        if (!ref.current?.open) onClose();
      }}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      // Close on a backdrop click only when the press also started there (not when a text selection is dragged out).
      onMouseDown={(e) => {
        downOnBackdrop.current = e.target === e.currentTarget;
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && downOnBackdrop.current) onClose();
      }}
      className={className ?? "m-auto max-h-[calc(100dvh-2rem)] w-[min(1060px,calc(100vw-2rem))] max-w-none overflow-y-auto rounded-2xl border border-rule bg-dock p-0 text-ink shadow-2xl animate-in fade-in zoom-in-95 duration-200 backdrop:bg-[rgba(8,24,16,0.45)] backdrop:backdrop-blur-[3px] motion-reduce:animate-none"}
    >
      {children}
    </dialog>
  );
}

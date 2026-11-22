// Minimal stand-in for Ballmac UI's message hook: returns the English fallback with {placeholders} filled.
// Swap in a real catalogue later by looking `key` up before falling back.
import { useCallback } from "react";

export type MessageVars = Record<string, string | number>;

export type MessageFn = (key: string, fallback: string, vars?: MessageVars) => string;

export function formatMessage(fallback: string, vars?: MessageVars): string {
  if (!vars) return fallback;
  return fallback.replace(/\{(\w+)\}/g, (match, name: string) =>
    vars[name] === undefined ? match : String(vars[name]),
  );
}

export function useMessages(): MessageFn {
  return useCallback<MessageFn>((_key, fallback, vars) => formatMessage(fallback, vars), []);
}

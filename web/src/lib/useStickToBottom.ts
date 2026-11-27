"use client";

import { type RefObject, useEffect } from "react";

/** How close to the bottom (px) still counts as "following" the stream. */
export const STICK_THRESHOLD_PX = 48;

/**
 * Keeps a scrollable element pinned to its end as content arrives, like a chat window,
 * but only while the reader is already at (or near) the bottom. Scrolling up to read
 * earlier content pauses following, and scrolling back down resumes it.
 *
 * It watches the element's content directly (new steps, streamed text), so callers
 * don't need to pass anything that changes.
 */
export function useStickToBottom<T extends HTMLElement>(ref: RefObject<T | null>): void {
  // Runs once per mounted element; the observer handles every later content change.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let following = true;
    const toBottom = () => {
      el.scrollTop = el.scrollHeight;
    };
    const onScroll = () => {
      following = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_THRESHOLD_PX;
    };
    const observer = new MutationObserver(() => {
      if (following) toBottom();
    });
    toBottom();
    el.addEventListener("scroll", onScroll, { passive: true });
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", onScroll);
    };
  }, [ref]);
}

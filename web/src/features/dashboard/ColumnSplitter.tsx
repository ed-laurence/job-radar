"use client";

import {
  type KeyboardEvent,
  type PointerEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import styles from "./Dashboard.module.css";

/** The campaign column's share of the layout width, in percent. */
export const DEFAULT_SPLIT = 41.7; // job-seeker's 5fr : 7fr
export const MIN_SPLIT = 25;
export const MAX_SPLIT = 60;
const STEP = 2;
export const SPLIT_KEY = "jobradar.dashboard.split";

export function clampSplit(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_SPLIT;
  return Math.round(Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, value)) * 10) / 10;
}

export function readSplit(stored: string | null): number {
  if (stored === null) return DEFAULT_SPLIT;
  const n = Number(stored);
  return Number.isFinite(n) ? clampSplit(n) : DEFAULT_SPLIT;
}

function load(): number {
  try {
    return readSplit(window.localStorage.getItem(SPLIT_KEY));
  } catch {
    return DEFAULT_SPLIT;
  }
}

function save(value: number): void {
  try {
    window.localStorage.setItem(SPLIT_KEY, String(value));
  } catch {
    // Storage blocked (private mode): the width just isn't remembered.
  }
}

/** The split, remembered per browser. Starts at the default so server and client render alike. */
export function useColumnSplit(): [number, (value: number) => void] {
  const [split, setSplit] = useState(DEFAULT_SPLIT);
  useEffect(() => setSplit(load()), []);
  const update = useCallback((value: number) => {
    const next = clampSplit(value);
    setSplit(next);
    save(next);
  }, []);
  return [split, update];
}

export interface ColumnSplitterProps {
  value: number;
  onChange: (value: number) => void;
  /** The grid whose width the split is measured against. */
  container: HTMLElement | null;
}

/** The draggable bar between the campaign and main columns (arrow keys, Home/End, double-click resets). */
export function ColumnSplitter({ value, onChange, container }: ColumnSplitterProps) {
  const dragging = useRef<number | null>(null);

  const toSplit = (clientX: number): number | null => {
    const box = container?.getBoundingClientRect();
    if (!box || box.width <= 0) return null;
    return ((clientX - box.left) / box.width) * 100;
  };

  const onPointerDown = (e: PointerEvent<HTMLHRElement>) => {
    if (e.button !== 0) return;
    dragging.current = e.pointerId;
    e.currentTarget.setPointerCapture?.(e.pointerId);
    e.preventDefault();
  };
  const onPointerMove = (e: PointerEvent<HTMLHRElement>) => {
    if (dragging.current !== e.pointerId) return;
    const next = toSplit(e.clientX);
    if (next !== null) onChange(next);
  };
  const stop = (e: PointerEvent<HTMLHRElement>) => {
    if (dragging.current !== e.pointerId) return;
    dragging.current = null;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLHRElement>) => {
    const next =
      e.key === "ArrowLeft"
        ? value - STEP
        : e.key === "ArrowRight"
          ? value + STEP
          : e.key === "Home"
            ? MIN_SPLIT
            : e.key === "End"
              ? MAX_SPLIT
              : null;
    if (next === null) return;
    e.preventDefault();
    onChange(next);
  };

  return (
    <hr
      aria-orientation="vertical"
      aria-label="Resize the campaign column"
      aria-valuemin={MIN_SPLIT}
      aria-valuemax={MAX_SPLIT}
      aria-valuenow={value}
      aria-valuetext={`Campaign column ${Math.round(value)}% wide`}
      tabIndex={0}
      title="Drag to resize. Double-click to reset."
      className={styles.splitter}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={stop}
      onPointerCancel={stop}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onChange(DEFAULT_SPLIT)}
    />
  );
}

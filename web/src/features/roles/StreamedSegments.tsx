import { Markdown } from "@/components/Markdown";
import { REDACTED, type Segment } from "@/lib/sse";
import styles from "./Roles.module.css";

type Block = { kind: "text"; key: string; text: string } | { kind: "redacted"; key: string };

/**
 * Consecutive released segments are joined and rendered as one sanitized markdown block, so
 * formatting that spans segments still works. A retracted segment is its own marked block and
 * always reads "[redacted]", whatever text it had.
 */
function toBlocks(segments: Segment[]): Block[] {
  const blocks: Block[] = [];
  for (const s of segments) {
    if (s.redacted) {
      blocks.push({ kind: "redacted", key: s.segmentId });
      continue;
    }
    const last = blocks.at(-1);
    if (last?.kind === "text") last.text += s.text;
    else blocks.push({ kind: "text", key: s.segmentId, text: s.text });
  }
  return blocks;
}

export interface StreamedSegmentsProps {
  segments: Segment[];
}

export function StreamedSegments({ segments }: StreamedSegmentsProps) {
  return (
    <>
      {toBlocks(segments).map((b) =>
        b.kind === "text" ? (
          <Markdown key={b.key}>{b.text}</Markdown>
        ) : (
          <p key={b.key}>
            <span className={styles.redacted}>{REDACTED}</span>
          </p>
        ),
      )}
    </>
  );
}

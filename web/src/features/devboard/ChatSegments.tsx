import { Markdown } from "@/components/Markdown";
import { REDACTED, type Segment } from "@/lib/sse";
import styles from "./DevBoard.module.css";

type Block = { kind: "text"; key: string; text: string } | { kind: "redacted"; key: string };

/**
 * Consecutive released segments are joined into one sanitized markdown block, so formatting
 * that spans segments still works. A retracted segment is its own block and always reads
 * "[redacted]", whatever text it had. (Same rendering as the role chat; features can't share.)
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

export interface ChatSegmentsProps {
  segments: Segment[];
}

export function ChatSegments({ segments }: ChatSegmentsProps) {
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

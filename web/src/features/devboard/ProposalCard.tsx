import { useId } from "react";
import { ErrorState } from "@/components/ErrorState";
import type { ErrorView } from "@/lib/api";
import { CriteriaDiff } from "./CriteriaDiff";
import type { TaskChatProposal } from "./chatApi";
import styles from "./DevBoard.module.css";
import { TextDiff } from "./TextDiff";

export interface ProposalCardProps {
  proposal: TaskChatProposal;
  /** A later proposal in the conversation replaces this one. */
  superseded: boolean;
  busy: boolean;
  error?: ErrorView | undefined;
  onAccept: () => void;
  onReject: () => void;
}

function resolution(proposal: TaskChatProposal, superseded: boolean): string | null {
  if (proposal.status === "accepted") return "Accepted. The edit is on the card.";
  if (proposal.status === "rejected") return "Rejected. The card wasn't changed.";
  if (superseded) return "Superseded by a later proposal.";
  return null;
}

/** An edit Claude proposed: exactly what would change, why, and Accept / Reject while pending. */
export function ProposalCard({
  proposal,
  superseded,
  busy,
  error,
  onAccept,
  onReject,
}: ProposalCardProps) {
  const reasonId = useId();
  const { title, description, acceptance_criteria: criteria } = proposal.changes;
  const resolved = resolution(proposal, superseded);
  return (
    <fieldset
      className={resolved ? `${styles.proposal} ${styles.proposalResolved}` : styles.proposal}
      aria-describedby={reasonId}
    >
      <legend className={styles.proposalLegend}>Proposed edit</legend>
      <p id={reasonId} className={styles.proposalReason}>
        <span className={styles.diffTag}>Reason: </span>
        {proposal.reason}
      </p>
      {title ? <TextDiff label="Title" change={title} /> : null}
      {description ? <TextDiff label="Description" change={description} /> : null}
      {criteria ? <CriteriaDiff change={criteria} /> : null}
      {resolved ? (
        <p className={styles.hint}>{resolved}</p>
      ) : (
        <>
          {error ? <ErrorState {...error} /> : null}
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.button}
              aria-label="Accept proposed edit"
              disabled={busy}
              onClick={onAccept}
            >
              Accept
            </button>
            <button
              type="button"
              className={styles.secondary}
              aria-label="Reject proposed edit"
              disabled={busy}
              onClick={onReject}
            >
              Reject
            </button>
          </div>
        </>
      )}
    </fieldset>
  );
}

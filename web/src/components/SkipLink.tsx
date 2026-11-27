import styles from "./SkipLink.module.css";

export interface SkipLinkProps {
  targetId: string;
}

export function SkipLink({ targetId }: SkipLinkProps) {
  return (
    <a className={styles.skip} href={`#${targetId}`}>
      Skip to main content
    </a>
  );
}

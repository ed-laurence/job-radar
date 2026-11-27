import styles from "./States.module.css";

export interface ComingSoonProps {
  title: string;
}

export function ComingSoon({ title }: ComingSoonProps) {
  return (
    <section aria-labelledby="page-title">
      <h1 id="page-title">{title}</h1>
      <p className={styles.text}>Coming soon.</p>
    </section>
  );
}

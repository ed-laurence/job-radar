"use client";

import { ErrorState } from "@/components/ErrorState";

export default function SectionError({ reset }: { error: Error; reset: () => void }) {
  return <ErrorState description="This page failed to load. Nothing was lost." onRetry={reset} />;
}

/** One `text/event-stream` frame. */
export function sseFrame(event: string, id: string, data: unknown): string {
  return `event: ${event}\nid: ${id}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** An SSE body that sends `before`, waits for `gate`, then sends `after`. */
export function gatedStream(before: string[], gate: Promise<void>, after: string[]) {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const chunk of before) controller.enqueue(encoder.encode(chunk));
      await gate;
      for (const chunk of after) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
}

export const SSE_HEADERS = { "Content-Type": "text/event-stream" };

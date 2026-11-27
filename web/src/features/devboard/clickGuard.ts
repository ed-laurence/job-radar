/** Pointer travel (px) that turns a press into a drag; matches the board's mouse sensor. */
export const DRAG_DISTANCE_PX = 5;

/**
 * Call on mousedown inside a draggable card. If the press ends as a drag, the click the browser
 * fires afterwards is cancelled so a link under the pointer isn't followed. dnd-kit only stops
 * that click's propagation (in the document's capture phase), which leaves a native link's
 * navigation running, so this listens earlier, in the window's capture phase.
 */
export function guardClickAfterDrag(
  card: HTMLElement,
  startX: number,
  startY: number,
  threshold = DRAG_DISTANCE_PX,
): void {
  const onClick = (event: MouseEvent) => {
    const moved = Math.hypot(event.clientX - startX, event.clientY - startY) >= threshold;
    if (moved && event.target instanceof Node && card.contains(event.target)) {
      event.preventDefault();
      event.stopPropagation();
    }
    remove();
  };
  // The click (if any) is dispatched straight after mouseup; drop the guard once it has had its turn.
  const onMouseUp = () => {
    window.setTimeout(remove, 0);
  };
  const remove = () => {
    window.removeEventListener("click", onClick, true);
    window.removeEventListener("mouseup", onMouseUp, true);
  };
  window.addEventListener("click", onClick, true);
  window.addEventListener("mouseup", onMouseUp, true);
}

import { stateStyle } from "@/lib/stateStyle";

/** State is never conveyed by color alone: the icon and the French label always come with it. */
export function StateBadge({
  state,
  commLost,
  size = "md",
}: {
  state: number | null;
  commLost: boolean;
  size?: "md" | "lg";
}) {
  const style = stateStyle(state, commLost);
  const text = size === "lg" ? "text-2xl" : "text-base";
  const dot = size === "lg" ? "h-4 w-4" : "h-2.5 w-2.5";

  return (
    <span className={`inline-flex items-center gap-2 font-medium ${text}`}>
      <span
        className={`inline-block rounded-full ${dot}`}
        style={{ backgroundColor: style.color }}
        aria-hidden
      />
      <span aria-hidden>{style.icon}</span>
      <span>{style.label}</span>
    </span>
  );
}

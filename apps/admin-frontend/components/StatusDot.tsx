const MAP: Record<string, string> = {
  online:  "dot-on",
  ready:   "dot-on",
  success: "dot-on",
  done:    "dot-on",
  running: "dot-run",
  pending: "dot-off",
  warn:    "dot-warn",
  offline: "dot-off",
  failed:  "dot-fail",
};

export default function StatusDot({ status }: { status: string }) {
  const cls = MAP[status] ?? "dot-off";
  return <span className={"dot " + cls} />;
}

/* ============================================================
   PAIZIQ DOCS — animated audit-pipeline graph (signature element)
   Agent → Rules → 4-Way Match → Gateway → Audit
   ============================================================ */
const TRACE_NODES = [
  { id: "agent", label: "AGENT", sub: "intent formed" },
  { id: "rules", label: "RULES", sub: "policy evaluated" },
  { id: "fourway", label: "4-WAY MATCH", sub: "tamper checked" },
  { id: "gateway", label: "GATEWAY", sub: "charge executed" },
  { id: "audit", label: "AUDIT", sub: "record appended" },
];
const TX0 = 70, TX1 = 690, TNY = 58, TNR = 7;
const TNODE_X = TRACE_NODES.map((_, i) => TX0 + (TX1 - TX0) * (i / (TRACE_NODES.length - 1)));

function TraceGraph() {
  const [p, setP] = React.useState(0);
  const [cycle, setCycle] = React.useState(0);
  const raf = React.useRef(0);
  const start = React.useRef(0);
  const DUR = 4200, HOLD = 1200;

  React.useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setP(1); return; }
    const loop = (t) => {
      if (!start.current) start.current = t;
      const el = t - start.current;
      if (el <= DUR) setP(Math.min(1, el / DUR));
      else if (el <= DUR + HOLD) setP(1);
      else { start.current = t; setP(0); setCycle(c => c + 1); }
      raf.current = requestAnimationFrame(loop);
    };
    raf.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf.current);
  }, []);

  const ease = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
  const pulseX = TX0 + (TX1 - TX0) * ease;
  const activeIdx = Math.round(ease * (TRACE_NODES.length - 1));
  const done = p >= 1;

  const agents = ["procurement-agent", "checkout-bot-7", "refund-agent-1", "payroll-bot-4"];
  const amounts = ["$49.99", "$128.50", "$9.99", "$1,200.00"];
  const merchants = ["acme corp", "cloud-host inc", "acme corp", "payroll co"];
  const agent = agents[cycle % agents.length];
  const amount = amounts[cycle % amounts.length];
  const merchant = merchants[cycle % merchants.length];

  return (
    <div className="trace-stage">
      <div className="trace-stage-head">
        <span className="ts-dot" />
        <span className="ts-label">Live audit pipeline</span>
        <span className="ts-id">trace_{(0x4a91 + cycle * 7).toString(16)}</span>
      </div>

      <div className="trace-graph">
        <svg className="trace-svg" viewBox="0 0 760 130" preserveAspectRatio="xMidYMid meet">
          <line x1={TX0} y1={TNY} x2={TX1} y2={TNY} stroke="var(--border)" strokeWidth="2" />
          <line x1={TX0} y1={TNY} x2={pulseX} y2={TNY} stroke="var(--accent)" strokeWidth="2"
            opacity="0.55" strokeLinecap="round" />
          {TRACE_NODES.map((n, i) => {
            const reached = ease * (TRACE_NODES.length - 1) >= i - 0.15;
            const isActive = i === activeIdx && !done;
            const cx = TNODE_X[i];
            return (
              <g key={n.id}>
                {reached && (
                  <circle cx={cx} cy={TNY} r={TNR + 7}
                    fill="none" stroke="var(--accent)"
                    strokeWidth={isActive ? 1.5 : 1}
                    opacity={isActive ? 0.5 : 0.18} />
                )}
                <circle cx={cx} cy={TNY} r={TNR}
                  fill={reached ? "var(--accent)" : "var(--bg-raised)"}
                  stroke={reached ? "var(--accent)" : "var(--border)"}
                  strokeWidth="2"
                  style={{ filter: isActive ? "drop-shadow(0 0 7px var(--accent))" : "none", transition: "fill .2s, stroke .2s" }} />
                <text x={cx} y={TNY + 28} textAnchor="middle" className="tnode-label">{n.label}</text>
                <text x={cx} y={TNY + 42} textAnchor="middle" className="tnode-sub">{n.sub}</text>
              </g>
            );
          })}
          {!done && (
            <g>
              <circle cx={pulseX} cy={TNY} r="4.5" fill="var(--accent)"
                style={{ filter: "drop-shadow(0 0 6px var(--accent))" }} />
              <circle cx={pulseX} cy={TNY} r="9" fill="none" stroke="var(--accent)" opacity="0.3" />
            </g>
          )}
        </svg>
      </div>

      <div className="trace-readout">
        <span className="ro"><b>agent</b> <span className="v">{agent}</span></span>
        <span className="ro"><b>intent</b> charge · <span className="v">{amount}</span></span>
        <span className="ro"><b>merchant</b> {merchant}</span>
        <span className="ro"><b>risk flags</b> 0</span>
        <span style={{ marginLeft: "auto" }}>
          {done ? <VChip kind="allow" /> : <span className="ro" style={{ opacity: 0.5 }}>auditing…</span>}
        </span>
      </div>
    </div>
  );
}
Object.assign(window, { TraceGraph });

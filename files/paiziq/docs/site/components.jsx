/* ============================================================
   PAIZIQ DOCS — shared UI components
   ============================================================ */
const { useState, useEffect, useRef, useMemo } = React;

/* ---------- code block with tabs + copy ---------- */
function CodeBlock({ tabs, file, lang, code }) {
  const list = tabs || [{ label: file, lang, file, code }];
  const [active, setActive] = useState(0);
  const [copied, setCopied] = useState(false);
  const cur = list[active];
  const copy = () => {
    navigator.clipboard.writeText(cur.code).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 1400);
    });
  };
  return (
    <div className="codeblock">
      <div className="code-head">
        {list.length > 1 ? (
          <div className="code-tabs">
            {list.map((t, i) => (
              <div key={i} className={"code-tab" + (i === active ? " active" : "")}
                onClick={() => setActive(i)}>{t.label}</div>
            ))}
          </div>
        ) : (
          <span className="code-file">{cur.file || ""}</span>
        )}
        <div className="code-actions">
          <span className="code-lang">{cur.lang}</span>
          <button className={"copybtn" + (copied ? " done" : "")} onClick={copy}>
            <Icon name={copied ? "check" : "copy"} />
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
      <pre><code dangerouslySetInnerHTML={{ __html: highlight(cur.lang, cur.code) }} /></pre>
    </div>
  );
}

/* ---------- callout ---------- */
function Callout({ type = "info", children }) {
  const ico = type === "warn" ? "warn" : type === "danger" ? "warn" : "info";
  return (
    <div className={"callout" + (type !== "info" ? " " + type : "")}>
      <Icon name={ico} className="co-ico" />
      <div>{children}</div>
    </div>
  );
}

/* ---------- collapsible param row ---------- */
function Param({ name, type, required, desc, children, defaultOpen }) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className={"param" + (open ? " open" : "")}>
      <div className="param-head" onClick={() => children && setOpen(o => !o)}
        style={{ cursor: children ? "pointer" : "default" }}>
        {children ? <Icon name="chev" className="pchev" /> : <span style={{ width: 14 }} />}
        <span className="param-name">{name}</span>
        <span className="param-type">{type}</span>
        {required ? <span className="param-req">required</span> : <span className="param-opt">optional</span>}
        <span className="param-desc">{desc}</span>
      </div>
      {open && children && <div className="param-body">{children}</div>}
    </div>
  );
}

/* ---------- decision status chip ---------- */
const CHIP_TEXT = { allow: "approved", hold: "needs_review", block: "rejected" };
function VChip({ kind, label }) {
  return <span className={"vchip " + kind}>{label || CHIP_TEXT[kind]}</span>;
}

Object.assign(window, { CodeBlock, Callout, Param, VChip });

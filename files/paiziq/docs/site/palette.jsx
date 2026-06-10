/* ============================================================
   PAIZIQ DOCS — ⌘K command palette (fuzzy search)
   ============================================================ */
function CommandPalette({ onClose, onNavigate }) {
  const [q, setQ] = React.useState("");
  const [sel, setSel] = React.useState(0);
  const inputRef = React.useRef(null);

  const all = React.useMemo(() => {
    const out = [];
    PAGE_ORDER.forEach(p => {
      out.push({ type: "page", page: p.id, title: p.title, icon: p.icon, sub: "Page", hash: "" });
      (SECTIONS[p.id] || []).forEach(([title, hash]) => {
        out.push({ type: "section", page: p.id, title, icon: "hash", sub: p.title, hash });
      });
    });
    return out;
  }, []);

  const results = React.useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return all;
    return all
      .map(r => {
        const hay = (r.title + " " + r.sub).toLowerCase();
        let score = -1;
        if (hay.includes(t)) score = 100 - hay.indexOf(t);
        else {
          let qi = 0;
          for (let i = 0; i < hay.length && qi < t.length; i++) if (hay[i] === t[qi]) qi++;
          if (qi === t.length) score = 10;
        }
        return { r, score };
      })
      .filter(x => x.score >= 0)
      .sort((a, b) => b.score - a.score)
      .map(x => x.r);
  }, [q, all]);

  React.useEffect(() => { inputRef.current && inputRef.current.focus(); }, []);
  React.useEffect(() => { setSel(0); }, [q]);

  const go = (r) => { onNavigate(r.page, r.hash); onClose(); };

  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel(s => Math.min(s + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel(s => Math.max(s - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); results[sel] && go(results[sel]); }
    else if (e.key === "Escape") { onClose(); }
  };

  const listRef = React.useRef(null);
  React.useEffect(() => {
    const el = listRef.current && listRef.current.querySelector(".palette-item.active");
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [sel]);

  return (
    <div className="palette-backdrop" onMouseDown={onClose}>
      <div className="palette" onMouseDown={e => e.stopPropagation()} onKeyDown={onKey}>
        <div className="palette-input">
          <Icon name="search" />
          <input ref={inputRef} value={q} onChange={e => setQ(e.target.value)}
            placeholder="Search pages, methods, concepts…" />
          <span className="kbd">esc</span>
        </div>
        <div className="palette-results" ref={listRef}>
          {results.length === 0 && <div className="palette-empty">No matches for “{q}”.</div>}
          {results.map((r, i) => (
            <div key={i} className={"palette-item" + (i === sel ? " active" : "")}
              onMouseEnter={() => setSel(i)} onClick={() => go(r)}>
              <span className="pi-ico"><Icon name={r.icon} /></span>
              <span className="pi-title">{r.title}</span>
              <span className="pi-sub">{r.sub}</span>
            </div>
          ))}
        </div>
        <div className="palette-foot">
          <span className="pf"><span className="kbd">↑</span><span className="kbd">↓</span> navigate</span>
          <span className="pf"><span className="kbd">↵</span> open</span>
          <span className="pf"><span className="kbd">esc</span> close</span>
        </div>
      </div>
    </div>
  );
}
Object.assign(window, { CommandPalette });

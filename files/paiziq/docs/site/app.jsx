/* ============================================================
   PAIZIQ DOCS — app shell: routing, theme, palette, toc, pager
   Markup mirrors styles.css: .shell > (.topbar, .sidebar, .main)
   ============================================================ */
const PAGES = {
  overview: PageOverview,
  quickstart: PageQuickstart,
  authentication: PageAuth,
  concepts: PageConcepts,
  webhooks: PageWebhooks,
  recipes: PageRecipes,
  api: PageApi,
  changelog: PageChangelog,
};

function routeFromHash() {
  const raw = (window.location.hash || "").replace(/^#\//, "");
  const id = raw.split("~")[0];
  return PAGES[id] ? id : "overview";
}

function Toc({ route }) {
  const items = SECTIONS[route] || [];
  if (!items.length) return <div />;
  return (
    <aside className="toc">
      <div className="toc-label">On this page</div>
      {items.map(([title, anchor]) => (
        <a key={anchor} className="toc-item" href={"#/" + route + "~" + anchor}>
          {title}
        </a>
      ))}
    </aside>
  );
}

function App() {
  const [route, setRoute] = React.useState(routeFromHash);
  const [theme, setTheme] = React.useState(
    () => localStorage.getItem("pzq-theme") || "dark"
  );
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [menuOpen, setMenuOpen] = React.useState(false);

  React.useEffect(() => {
    const onHash = () => {
      setRoute(routeFromHash());
      setMenuOpen(false);
      const anchor = window.location.hash.split("~")[1] || "";
      if (anchor) {
        requestAnimationFrame(() => {
          const el = document.getElementById(anchor);
          if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      } else {
        window.scrollTo(0, 0);
      }
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("pzq-theme", theme);
  }, [theme]);

  React.useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(v => !v);
      }
      if (e.key === "Escape") setPaletteOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const navigate = (id) => { window.location.hash = "#/" + id; };
  const idx = PAGE_ORDER.findIndex(p => p.id === route);
  const prev = idx > 0 ? PAGE_ORDER[idx - 1] : null;
  const next = idx < PAGE_ORDER.length - 1 ? PAGE_ORDER[idx + 1] : null;
  const Page = PAGES[route];

  return (
    <div className="shell">
      <TopBar
        theme={theme}
        onTheme={() => setTheme(t => (t === "dark" ? "light" : "dark"))}
        onSearch={() => setPaletteOpen(true)}
        onMenu={() => setMenuOpen(v => !v)}
      />
      <Sidebar route={route} onNavigate={navigate} open={menuOpen} />
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} />}
      <div className="main">
        <main className="content">
          <Page />
          <div className="page-nav">
            {prev ? (
              <a href={"#/" + prev.id}>
                <div className="pn-dir">← Previous</div>
                <div className="pn-title">{prev.title}</div>
              </a>
            ) : <span />}
            {next ? (
              <a className="next" href={"#/" + next.id}>
                <div className="pn-dir">Next →</div>
                <div className="pn-title">{next.title}</div>
              </a>
            ) : <span />}
          </div>
        </main>
        <Toc route={route} />
      </div>
      {paletteOpen && (
        <CommandPalette
          onClose={() => setPaletteOpen(false)}
          onNavigate={(id) => { setPaletteOpen(false); window.location.hash = "#/" + id; }}
        />
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);

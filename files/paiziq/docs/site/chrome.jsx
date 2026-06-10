/* ============================================================
   PAIZIQ DOCS — navigation, top bar, sidebar
   ============================================================ */
const NAV = [
  {
    label: "Getting started",
    items: [
      { id: "overview", title: "Overview", icon: "book" },
      { id: "quickstart", title: "Quickstart", icon: "zap" },
      { id: "authentication", title: "Authentication", icon: "key" },
    ],
  },
  {
    label: "Guides",
    items: [
      { id: "concepts", title: "How auditing works", icon: "layers" },
      { id: "webhooks", title: "Notifications & events", icon: "hook" },
      { id: "recipes", title: "Recipes", icon: "code" },
    ],
  },
  {
    label: "Reference",
    items: [
      { id: "api", title: "API reference", icon: "term" },
      { id: "changelog", title: "Changelog", icon: "hist" },
    ],
  },
];

const PAGE_ORDER = NAV.flatMap(g => g.items);

// section index used by the command palette
const SECTIONS = {
  overview: [["What it does", "what"], ["The audit pipeline", "pipeline"], ["Why teams use it", "why"], ["First audit in 60 seconds", "first"]],
  quickstart: [["Install the SDK", "install"], ["Initialize the SDK", "init"], ["Review a payment", "review"], ["Execute with the 4-way match", "execute"]],
  authentication: [["SDK API key", "keys"], ["Environment variables", "envvars"], ["Ingest service keys", "ingest"], ["Handling secrets", "secrets"]],
  concepts: [["Request → Decision → Audit", "model"], ["The decision engine", "rules"], ["The 4-Way Match", "fourway"], ["Spans & the wire contract", "spans"]],
  webhooks: [["Notification severities", "types"], ["Payload shape", "payload"], ["Custom notifiers", "notifier"], ["Ingest endpoints", "ingestapi"]],
  recipes: [["Guard a framework tool", "toolguard"], ["Scrub PII before export", "scrub"], ["Share budgets across replicas", "redis"]],
  api: [["PaiziqSDK", "sdk"], ["PaymentRequest", "request"], ["PaymentPolicy", "policy"], ["Decision", "decision"], ["Errors", "errors"]],
  changelog: [],
};

/* ---------- top bar ---------- */
function TopBar({ theme, onTheme, onSearch, onMenu }) {
  return (
    <header className="topbar">
      <button className="iconbtn menu-toggle" onClick={onMenu} aria-label="Menu">
        <Icon name="menu" />
      </button>
      <a className="brand" href="#/overview">
        <span className="brand-mark">
          <svg viewBox="0 0 26 26" fill="none">
            <circle cx="13" cy="13" r="11" stroke="var(--accent)" strokeWidth="1.5" />
            <circle cx="13" cy="13" r="4.2" fill="var(--accent)" />
            <path d="M13 2.2v6.4M13 17.4v6.4M2.2 13h6.4M17.4 13h6.4" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </span>
        <span className="brand-name">PAIZIQ <b>SDK</b></span>
        <span className="brand-tag">v0.2.0</span>
      </a>
      <div className="topbar-spacer" />
      <button className="searchbtn" onClick={onSearch}>
        <Icon name="search" />
        <span className="stxt">Search the docs</span>
        <span className="kbd">⌘K</span>
      </button>
      <button className="iconbtn" onClick={onTheme} aria-label="Toggle theme">
        <Icon name={theme === "dark" ? "sun" : "moon"} />
      </button>
      <a className="iconbtn" href="#/overview" aria-label="GitHub" onClick={e => e.preventDefault()}>
        <Icon name="gh" />
      </a>
    </header>
  );
}

/* ---------- sidebar ---------- */
function Sidebar({ route, onNavigate, open }) {
  return (
    <nav className={"sidebar" + (open ? " open" : "")}>
      {NAV.map((group, gi) => (
        <div className="nav-group" key={gi}>
          <div className="nav-group-label">{group.label}</div>
          {group.items.map((it) => {
            const num = PAGE_ORDER.findIndex(p => p.id === it.id) + 1;
            return (
              <a key={it.id}
                href={"#/" + it.id}
                className={"nav-item" + (route === it.id ? " active" : "")}
                onClick={() => onNavigate(it.id)}>
                <span className="nav-num">{String(num).padStart(2, "0")}</span>
                {it.title}
              </a>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

Object.assign(window, { NAV, PAGE_ORDER, SECTIONS, TopBar, Sidebar });

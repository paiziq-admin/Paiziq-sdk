/* ============================================================
   PAIZIQ DOCS — syntax highlighting helpers
   ============================================================ */
function escHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function scanTokens(code, patterns) {
  let out = "", i = 0;
  while (i < code.length) {
    const rest = code.slice(i);
    let matched = false;
    for (const [re, cls] of patterns) {
      const m = re.exec(rest);
      if (m) {
        out += `<span class="tok-${cls}">${escHtml(m[0])}</span>`;
        i += m[0].length; matched = true; break;
      }
    }
    if (!matched) { out += escHtml(rest[0]); i++; }
  }
  return out;
}
const PY_PAT = [
  [/^#[^\n]*/, "com"],
  [/^"""[\s\S]*?"""/, "str"],
  [/^[fr]?"(?:\\.|[^"\\])*"/, "str"],
  [/^[fr]?'(?:\\.|[^'\\])*'/, "str"],
  [/^\b(def|class|return|import|from|if|elif|else|for|while|with|as|try|except|finally|raise|lambda|pass|break|continue|global|nonlocal|yield|async|await|not|and|or|in|is|del|assert|match|case)\b/, "key"],
  [/^\b(True|False|None|self|cls)\b/, "bool"],
  [/^\b0x[0-9a-fA-F]+|^\b\d[\d_]*(\.\d+)?\b/, "num"],
  [/^[A-Za-z_][\w]*(?=\s*\()/, "fn"],
  [/^\.[A-Za-z_][\w]*/, "prop"],
  [/^[{}()[\];,.:]/, "punc"],
];
const JSON_PAT = [
  [/^"(?:\\.|[^"\\])*"(?=\s*:)/, "prop"],
  [/^"(?:\\.|[^"\\])*"/, "str"],
  [/^\b(true|false|null)\b/, "bool"],
  [/^-?\d[\d_]*(\.\d+)?([eE][+-]?\d+)?/, "num"],
  [/^[{}()[\],:]/, "punc"],
];
const SH_PAT = [
  [/^#[^\n]*/, "com"],
  [/^\$(?=\s)/, "prompt"],
  [/^"(?:\\.|[^"\\])*"/, "str"],
  [/^'[^']*'/, "str"],
  [/^--?[\w-]+/, "flag"],
  [/^\b(pip3|python3|curl|cd|export|git|make|uvicorn|pytest|brew|mkdir|source)\b/, "fn"],
];
function highlight(lang, code) {
  if (lang === "json") return scanTokens(code, JSON_PAT);
  if (lang === "bash" || lang === "http" || lang === "sh") return scanTokens(code, SH_PAT);
  return scanTokens(code, PY_PAT);
}
Object.assign(window, { escHtml, highlight });

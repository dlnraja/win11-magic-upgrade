from pathlib import Path

repl = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2192": "->",
    "\u201c": '"',
    "\u201d": '"',
    "\u2019": "'",
}
for p in Path(r"C:\Users\Dell\win11 updater\python").rglob("*.py"):
    t = p.read_text(encoding="utf-8")
    n = t
    for a, b in repl.items():
        n = n.replace(a, b)
    if n != t:
        p.write_text(n, encoding="utf-8")
        print("fixed", p)

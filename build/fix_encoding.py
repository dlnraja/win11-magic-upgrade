from pathlib import Path

root = Path(r"C:\Users\Dell\win11 updater")
repl = {
    "\u2014": "-",
    "\u2013": "-",
    "\u2192": "->",
    "\u201c": '"',
    "\u201d": '"',
    "\u2019": "'",
    "\u00a0": " ",
}
for p in list(root.rglob("*.ps1")) + list(root.rglob("*.cmd")):
    if "vendor" in p.parts:
        continue
    text = p.read_bytes().decode("utf-8-sig")
    for a, b in repl.items():
        text = text.replace(a, b)
    p.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    print("fixed", p)

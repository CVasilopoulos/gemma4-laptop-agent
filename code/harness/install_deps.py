import re, subprocess, sys, zipfile
from pathlib import Path

sys.path.insert(0, "/sandbox")
import setup as S

WS = Path("/workspace")
WHEELS = Path("/wheels")
PIP = [sys.executable, "-m", "pip", "install", "--no-index", f"--find-links={WHEELS}", "--no-build-isolation", "-q"]


def site_packages():
    import site
    for p in site.getsitepackages():
        if Path(p).exists():
            return Path(p)
    return None


def requires(wheel):
    try:
        with zipfile.ZipFile(wheel) as z:
            meta = next((n for n in z.namelist() if n.endswith(".dist-info/METADATA")), None)
            if not meta:
                return []
            text = z.read(meta).decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for line in text.split("\n"):
        if not line.startswith("Requires-Dist:"):
            continue
        spec = line.split(":", 1)[1].strip()
        if ";" in spec:
            head, marker = spec.split(";", 1)
            if "extra" in marker:
                continue
            spec = head
        name = re.split(r"[<>=!~\[\s(]", spec.strip())[0]
        if name:
            out.append(S.normalize_pkg_name(name))
    return out


def closure(seed, avail):
    seen, stack = set(), list(seed)
    while stack:
        n = stack.pop()
        if n in seen or n not in avail:
            continue
        seen.add(n)
        stack.extend(requires(WHEELS / avail[n]))
    return seen


def main():
    avail = S.get_available_packages()
    deps = []
    pp = WS / "pyproject.toml"
    if pp.exists():
        deps += S.collect_from_pyproject(pp)
    deps += S.collect_from_requirements_files(WS)
    seed = [S.normalize_pkg_name(d) for d in set(deps)]
    names = closure(seed, avail)
    wheels = [WHEELS / avail[n] for n in sorted(names)]
    sp = site_packages()
    ok, streamed, failed = [], [], []
    for w in wheels:
        if subprocess.run(PIP + ["--no-deps", str(w)], capture_output=True, text=True).returncode == 0:
            ok.append(w.name)
        elif sp is not None:
            try:
                with zipfile.ZipFile(w) as z:
                    z.extractall(sp)
                streamed.append(w.name)
            except Exception:
                failed.append(w.name)
        else:
            failed.append(w.name)
    print(f"deps seed={len(seed)} closure={len(wheels)} installed={len(ok)} streamed={len(streamed)} failed={len(failed)}")
    if failed:
        print("failed: " + ", ".join(failed[:8]))


if __name__ == "__main__":
    main()

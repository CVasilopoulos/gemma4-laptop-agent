import ast, re, subprocess, sys, zipfile
from pathlib import Path

sys.path.insert(0, "/sandbox")
import setup as S
from pip._vendor.packaging.requirements import Requirement, InvalidRequirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.version import Version, InvalidVersion

try:
    import tomllib
except ImportError:
    tomllib = None

WS = Path("/workspace")
WHEELS = Path("/wheels")
PIP = [sys.executable, "-m", "pip", "install", "--no-index", f"--find-links={WHEELS}", "--no-build-isolation", "-q", "--no-deps"]
KEEP = {"pytest", "pluggy", "iniconfig", "packaging", "setuptools", "wheel", "pip", "pytest-timeout"}
TAG_OK = ("py3-none-any", "py2.py3-none-any", "cp313-cp313", "abi3")
ENV = {"extra": ""}


def norm(n):
    return S.normalize_pkg_name(n)


def parse_req(text):
    text = text.strip()
    if not text or text.startswith(("#", "-", "git+", "http://", "https://")):
        return None
    text = re.split(r"\s+#", text)[0].strip()
    try:
        r = Requirement(text)
    except InvalidRequirement:
        return None
    if r.marker is not None:
        try:
            if not r.marker.evaluate(ENV):
                return None
        except Exception:
            return None
    return norm(r.name), r.specifier


def wheel_index():
    idx = {}
    for f in WHEELS.glob("*.whl"):
        info = S.parse_wheel_filename(f.name)
        if not info:
            continue
        if not any(t in f.name for t in TAG_OK) and "none-any" not in f.name:
            continue
        try:
            v = Version(info["version"])
        except InvalidVersion:
            continue
        idx.setdefault(info["normalized"], []).append((v, f))
    for k in idx:
        idx[k].sort(key=lambda x: (x[0], "none-any" not in x[1].name), reverse=True)
    return idx


def pick(cands, spec):
    ok = [c for c in cands if spec.contains(c[0], prereleases=True)]
    return (ok or cands)[0], bool(ok)


def requires_of(wheel):
    try:
        with zipfile.ZipFile(wheel) as z:
            meta = next((n for n in z.namelist() if n.endswith(".dist-info/METADATA")), None)
            text = z.read(meta).decode("utf-8", "replace") if meta else ""
    except Exception:
        return []
    out = []
    for line in text.split("\n"):
        if line.startswith("Requires-Dist:"):
            r = parse_req(line.split(":", 1)[1])
            if r:
                out.append(r)
    return out


def literal_lists(src, names):
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            if isinstance(node.value, (ast.List, ast.Tuple)):
                out += [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if isinstance(node, ast.keyword) and node.arg in names and isinstance(node.value, (ast.List, ast.Tuple)):
            out += [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return out


def seeds():
    reqs = []
    pp = WS / "pyproject.toml"
    if pp.exists() and tomllib:
        try:
            data = tomllib.loads(pp.read_text())
        except Exception:
            data = {}
        proj = data.get("project", {})
        reqs += proj.get("dependencies", [])
        for v in proj.get("optional-dependencies", {}).values():
            reqs += v
        for v in data.get("dependency-groups", {}).values():
            reqs += [x for x in v if isinstance(x, str)]
    for rf in list(WS.glob("**/requirements*.txt")) + list(WS.glob("**/test-requirements*.txt")):
        if "docker/" in str(rf) or ".git" in str(rf):
            continue
        try:
            reqs += rf.read_text(errors="replace").splitlines()
        except Exception:
            pass
    sp = WS / "setup.py"
    if sp.exists():
        reqs += literal_lists(sp.read_text(errors="replace"), {"requires", "install_requires", "test_requirements", "tests_require"})
    cfg = WS / "setup.cfg"
    if cfg.exists():
        m = re.search(r"install_requires\s*=\s*\n((?:[ \t]+.+\n)+)", cfg.read_text(errors="replace"))
        if m:
            reqs += m.group(1).splitlines()
    out = {}
    for t in reqs:
        r = parse_req(t)
        if r:
            out.setdefault(r[0], SpecifierSet())
            out[r[0]] &= r[1]
    return out


def main():
    idx = wheel_index()
    specs = seeds()
    seed_names = set(specs)
    chosen = {}
    changed = True
    rounds = 0
    while changed and rounds < 20:
        changed = False
        rounds += 1
        for name in list(specs):
            if name not in idx:
                continue
            (v, f), _ = pick(idx[name], specs[name])
            if chosen.get(name) != (v, f):
                chosen[name] = (v, f)
                changed = True
                for dn, ds in requires_of(f):
                    before = str(specs.get(dn, ""))
                    specs.setdefault(dn, SpecifierSet())
                    specs[dn] &= ds
                    if str(specs[dn]) != before:
                        changed = True
    missing = sorted(n for n in seed_names if n not in idx)
    unsat = sorted(n for n, (v, f) in chosen.items() if not specs[n].contains(v, prereleases=True))
    ok, failed = [], []
    for name, (v, f) in sorted(chosen.items()):
        if name in KEEP:
            continue
        if subprocess.run(PIP + [str(f)], capture_output=True, text=True).returncode == 0:
            ok.append(f"{name}=={v}")
        else:
            failed.append(f.name)
    print(f"deps seed={len(seed_names)} chosen={len(chosen)} installed={len(ok)} failed={len(failed)} unsat={unsat[:8]}")
    print("missing_from_wheelhouse: " + ",".join(missing[:40]))
    print("installed: " + ",".join(ok))


if __name__ == "__main__":
    main()

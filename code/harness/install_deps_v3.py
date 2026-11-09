import argparse, importlib.metadata as md, json, re, subprocess, sys, site, zipfile
from pathlib import Path

from pip._vendor.packaging.requirements import Requirement, InvalidRequirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.version import Version, InvalidVersion

try:
    import tomllib
except ImportError:
    tomllib = None

WS = Path("/workspace")
DIRS = {"comp": Path("/wheels"), "overlay": Path("/wheels_overlay")}
SHIMS = Path("/sandbox/shims")
PIP = [sys.executable, "-m", "pip", "install", "-q", "--no-index", "--no-deps", "--upgrade", "--ignore-requires-python"]
OWN = {"fastapi/fastapi": "fastapi", "Textualize/rich": "rich", "psf/requests": "requests", "encode/httpx": "httpx"}
ONLY_FOR = {"pytest-httpbin": {"psf/requests"}, "httpbin": {"psf/requests"}, "flasgger": {"psf/requests"},
            "brotlicffi": {"psf/requests", "encode/httpx"}, "brotli": {"encode/httpx"}, "zstandard": {"encode/httpx"}}
BASE_FIXED = {"pytest": "8.4.2"}
BASE_SKIP = {"pytest-timeout"}
CALIBRATION = {
    "rich_3064": {"pygments": "2.15.1"}, "rich_3130": {"pygments": "2.15.1"}, "rich_3296": {"pygments": "2.16.1"},
    "rich_3469": {"pygments": "2.18.0"}, "rich_3782": {"pygments": "2.19.2"}, "rich_4070": {"pygments": "2.19.2"},
    "rich_4079": {"pygments": "2.20.0"},
    "requests_6589": {"werkzeug": "2.3.8", "flask": "2.3.3"}, "requests_6592": {"werkzeug": "2.3.8", "flask": "2.3.3"},
    "requests_6629": {"werkzeug": "2.3.8", "flask": "2.3.3"},
}
ASYNC_SHIM = {"fastapi_15745", "fastapi_15763", "fastapi_15785"}
UNROUTABLE_SHIM_REPOS = {"psf/requests"}


def norm(n):
    return re.sub(r"[-_.]+", "-", n).lower()


def fits(fname):
    parts = fname[:-4].split("-")
    py, abi, plat = parts[-3], parts[-2], parts[-1]
    if plat != "any" and "x86_64" not in plat:
        return False
    if abi == "none":
        return py.startswith("py3") or "py3" in py.split(".")
    if abi == "abi3":
        return True
    return abi == "cp313"


def index():
    idx = {}
    for src, d in DIRS.items():
        if not d.exists():
            continue
        for f in d.glob("*.whl"):
            if not fits(f.name):
                continue
            name, ver = f.name.split("-")[:2]
            try:
                v = Version(ver)
            except InvalidVersion:
                continue
            idx.setdefault(norm(name), []).append((v, src, f))
    return idx


def installed(name):
    try:
        return Version(md.version(name))
    except Exception:
        return None


def allowed(name, repo):
    return name not in ONLY_FOR or repo in ONLY_FOR[name]


def base_selection(repo):
    own = OWN.get(repo)
    sel = {}
    for name, opts in index().items():
        if name == own or name in BASE_SKIP or not allowed(name, repo):
            continue
        if name in BASE_FIXED:
            hit = [o for o in opts if str(o[0]) == BASE_FIXED[name]]
            if hit:
                sel[name] = hit[0]
            continue
        comp = [o for o in opts if o[1] == "comp"]
        sel[name] = max(comp or opts, key=lambda o: (o[0], o[1] == "comp"))
    return sel


def poetry_spec(spec):
    spec = spec if isinstance(spec, str) else (spec.get("version", "") if isinstance(spec, dict) else "")
    s = spec.strip()
    m = re.fullmatch(r"\^(\d+)(\.\d+)?(\.\d+)?", s)
    if m:
        return f">={s[1:]},<{int(m.group(1)) + 1}"
    m = re.fullmatch(r"~(\d+)\.(\d+)(\.\d+)?", s)
    if m:
        return f">={s[1:]},<{m.group(1)}.{int(m.group(2)) + 1}"
    return "" if s in ("*", "") else s


def wheel_requires(f):
    try:
        with zipfile.ZipFile(f) as z:
            meta = next(n for n in z.namelist() if n.endswith(".dist-info/METADATA"))
            text = z.read(meta).decode("utf-8", "replace")
    except Exception:
        return []
    return [l.split(":", 1)[1].strip() for l in text.splitlines() if l.startswith("Requires-Dist:")]


def task_picks(repo):
    data = {}
    pp = WS / "pyproject.toml"
    if pp.exists() and tomllib:
        try:
            data = tomllib.loads(pp.read_text())
        except Exception:
            data = {}
    reqs = list(data.get("project", {}).get("dependencies", []))
    for n, s in data.get("tool", {}).get("poetry", {}).get("dependencies", {}).items():
        if n != "python":
            reqs.append(n + poetry_spec(s))
    idx = index()
    own = OWN.get(repo)
    picks, queue, seen = {}, list(reqs), set()
    while queue:
        try:
            req = Requirement(queue.pop(0))
        except InvalidRequirement:
            continue
        key = norm(req.name)
        if key in seen or key == own:
            continue
        try:
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
        except Exception:
            continue
        seen.add(key)
        opts = [o for o in idx.get(key, []) if req.specifier.contains(o[0], prereleases=True) and allowed(key, repo)]
        if not opts:
            continue
        best = max(opts, key=lambda o: (o[0], o[1] == "comp"))
        if installed(key) != best[0]:
            picks[key] = best
        queue += wheel_requires(best[2])
    return picks, reqs


def calibration(task_id):
    idx = index()
    out = {}
    for name, ver in CALIBRATION.get(task_id, {}).items():
        hit = [o for o in idx.get(norm(name), []) if str(o[0]) == ver]
        if hit:
            out[norm(name)] = hit[0]
        else:
            print(f"calibration wheel missing: {name}=={ver}")
    return out


def site_dir():
    return Path(next(p for p in site.getsitepackages() if Path(p).exists()))


def install_shims(task_id, repo):
    sp = site_dir()
    done = []
    if task_id in ASYNC_SHIM:
        (sp / "swe_async_shim.py").write_text((SHIMS / "swe_async_shim.py").read_text())
        di = sp / "swe_async_shim-0.1.dist-info"
        di.mkdir(exist_ok=True)
        (di / "METADATA").write_text("Metadata-Version: 2.1\nName: swe-async-shim\nVersion: 0.1\n")
        (di / "entry_points.txt").write_text("[pytest11]\nswe_async_shim = swe_async_shim\n")
        done.append("async")
    if repo in UNROUTABLE_SHIM_REPOS:
        (sp / "swe_unroutable_shim.py").write_text((SHIMS / "swe_unroutable_shim.py").read_text())
        (sp / "zz_swe_unroutable.pth").write_text("import swe_unroutable_shim\n")
        done.append("unroutable")
    return done


def pip_install(wheels):
    if not wheels:
        return True
    r = subprocess.run(PIP + [str(w) for w in wheels], capture_output=True, text=True)
    if r.returncode != 0:
        print("pip failed: " + r.stderr[-600:])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", action="store_true")
    ap.add_argument("--repo", default="")
    ap.add_argument("--task", default="")
    a = ap.parse_args()
    if a.base:
        sel = base_selection(a.repo)
        ok = pip_install([o[2] for o in sel.values()])
        Path("/opt/swe_base.json").write_text(json.dumps({k: [str(v[0]), v[1]] for k, v in sorted(sel.items())}, indent=1))
        print(f"base repo={a.repo} packages={len(sel)} ok={ok}")
        return
    picks, reqs = task_picks(a.repo)
    cal = calibration(a.task)
    picks.update(cal)
    ok = pip_install([o[2] for o in picks.values()])
    shims = install_shims(a.task, a.repo)
    print(f"deps v3 task={a.task} seed={len(reqs)} picks={len(picks)} calibrated={sorted(cal)} shims={shims} ok={ok}")
    print("picks: " + ",".join(f"{k}=={v[0]}" for k, v in sorted(picks.items())))


if __name__ == "__main__":
    main()

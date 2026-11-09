import json, os, re, shlex, subprocess, tarfile, tempfile, time, uuid
from pathlib import Path

ROOT = Path(os.environ.get("GEMMA_ROOT", Path.home() / "competitions/gemma-4-agent"))
RAW = ROOT / "data/raw"
IMAGE = os.environ.get("SWE_IMAGE", "swebench-sandbox-local:latest")
MEM = os.environ.get("SANDBOX_MEM", "4g")
CPUS = os.environ.get("SANDBOX_CPUS", "2")
DEPS = os.environ.get("INSTALL_DEPS", "install_deps.py")
IMAGE_PREFIX = os.environ.get("SWE_IMAGE_PREFIX", "")
OWN = {"fastapi/fastapi": "fastapi", "Textualize/rich": "rich", "psf/requests": "requests", "encode/httpx": "httpx"}


def image_for(task):
    return f"{IMAGE_PREFIX}-{OWN[task['repo']]}:latest" if IMAGE_PREFIX else IMAGE

EXCLUDES = ["__pycache__/", "*.pyc", ".pytest_cache/", "*.egg-info/", "build/", "dist/", ".coverage"]


def load_tasks():
    return {json.loads(l)["instance_id"]: json.loads(l) for l in open(RAW / "tasks.jsonl")}


class Sandbox:
    def __init__(self, name=None, mem=MEM, cpus=CPUS, image=None):
        self.name = name or f"swe_{uuid.uuid4().hex[:10]}"
        self.image = image or IMAGE
        self.mem, self.cpus = mem, cpus
        self.cid = None

    def start(self):
        cmd = ["docker", "run", "-d", "--rm", "--name", self.name, "--network", "none",
               "--memory", self.mem, "--cpus", self.cpus, "-w", "/workspace",
               "-e", "TEST_TMPDIR=/tmp", self.image, "sleep", "infinity"]
        self.cid = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
        self.sh("mkdir -p /workspace /sandbox")
        subprocess.run(["docker", "cp", str(RAW / "sandbox/setup.py"), f"{self.name}:/sandbox/setup.py"], check=True)
        subprocess.run(["docker", "cp", str(Path(__file__).parent / DEPS), f"{self.name}:/sandbox/install_deps.py"], check=True)
        subprocess.run(["docker", "cp", str(Path(__file__).parent / "shims"), f"{self.name}:/sandbox/shims"], check=True)
        return self

    def sh(self, command, timeout=300, workdir="/workspace"):
        p = subprocess.run(["docker", "exec", "-w", workdir, self.name, "bash", "-lc", command],
                           capture_output=True, text=True, timeout=timeout)
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

    def put(self, local, remote):
        subprocess.run(["docker", "cp", str(local), f"{self.name}:{remote}"], check=True)

    def put_text(self, text, remote):
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".tmp") as f:
            f.write(text); tmp = f.name
        self.put(tmp, remote); os.unlink(tmp)

    def get_text(self, remote, limit=None):
        p = subprocess.run(["docker", "exec", self.name, "cat", remote], capture_output=True, text=True)
        return p.stdout if p.returncode == 0 else ""

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


def bootstrap(sb, task, commit_msg):
    snap = RAW / "snapshots" / f"{task['instance_id']}.tgz"
    sb.put(snap, "/tmp/snap.tgz")
    r = sb.sh("mkdir -p /workspace && tar xzf /tmp/snap.tgz -C /workspace --strip-components=0 && ls /workspace | head", timeout=600)
    if r["exit_code"] != 0:
        return r
    listing = sb.sh("ls -A /workspace")["stdout"].split()
    if ".git" not in listing and len(listing) == 1:
        sb.sh(f"shopt -s dotglob && mv /workspace/{listing[0]}/* /workspace/ && rmdir /workspace/{listing[0]}", timeout=120)
    sb.sh("mkdir -p /workspace/.git/info && printf '%s\\n' " + " ".join(shlex.quote(e) for e in EXCLUDES) + " >> /workspace/.git/info/exclude")
    setup = sb.sh(f"python3 /sandbox/setup.py --repo {shlex.quote(task['repo'])} --fast-path 2>&1 | tail -10", timeout=1800)
    dep = sb.sh(f"python3 /sandbox/install_deps.py --repo {shlex.quote(task['repo'])} --task {shlex.quote(task['instance_id'])} 2>&1 | tail -6", timeout=1800)
    setup["stdout"] += "\n" + dep["stdout"]
    sb.sh("git config --global --add safe.directory /workspace")
    sb.sh(f"cd /workspace && git add -A && git commit -m {shlex.quote(commit_msg)} --allow-empty -q")
    return setup


def test_targets(test_patch):
    return sorted(set(re.findall(r"^\+\+\+ b/(\S+)", test_patch, re.M)))


def apply_patch(sb, patch_text, label="patch"):
    if not patch_text.endswith("\n"):
        patch_text += "\n"
    sb.put_text(patch_text, f"/tmp/{label}.diff")
    passes = [
        f"git apply --unsafe-paths -p1 /tmp/{label}.diff",
        f"git apply --unsafe-paths -p1 -3 /tmp/{label}.diff",
        f"git apply --unsafe-paths -p1 --ignore-space-change --ignore-whitespace /tmp/{label}.diff",
        f"git apply --unsafe-paths -p1 --recount /tmp/{label}.diff",
        f"git apply --unsafe-paths -p0 /tmp/{label}.diff",
        f"patch -p1 --batch --forward -i /tmp/{label}.diff",
        f"patch -p0 --batch --forward -l -i /tmp/{label}.diff",
    ]
    for i, c in enumerate(passes):
        r = sb.sh(f"cd /workspace && {c}", timeout=300)
        if r["exit_code"] == 0:
            return True, i, r
    return False, -1, r


def verify(task, agent_patch, keep=False, log_dir=None):
    t0 = time.time()
    sb = Sandbox(image=image_for(task)).start()
    out = {"instance_id": task["instance_id"], "repo": task["repo"]}
    try:
        boot = bootstrap(sb, task, "eval_baseline")
        out["setup_tail"] = boot["stdout"][-400:]
        if agent_patch.strip():
            ok, npass, r = apply_patch(sb, agent_patch, "agent")
            out["patch_applied"], out["patch_pass"] = ok, npass
            if not ok:
                out["resolved"] = False
                out["error"] = "Failed to apply agent_patch: " + r["stderr"][-300:]
                return out
        else:
            out["patch_applied"], out["patch_pass"] = False, None
        targets = test_targets(task["test_patch"])
        out["test_targets"] = targets
        q = " ".join(shlex.quote(t) for t in targets)
        sb.sh(f"cd /workspace && git checkout HEAD -- {q} 2>/dev/null || true")
        sb.sh(f"cd /workspace && git clean -f -- {q} 2>/dev/null || true")
        ok, npass, r = apply_patch(sb, task["test_patch"], "test")
        out["test_patch_applied"] = ok
        if not ok:
            out["resolved"] = False
            out["error"] = "Failed to apply test_patch: " + r["stderr"][-300:]
            return out
        cmd = (f"cd /workspace && PYTHONSAFEPATH=1 python3 -m pytest {q} "
               f"-p no:anyio -o timeout=0 -o norecursedirs='.* build dist venv' "
               f"-o python_classes='Test* *Test' -q")
        try:
            tr = sb.sh(cmd, timeout=int(os.environ.get("SWE_PYTEST_TIMEOUT", "600")))
        except Exception as ex:
            out["resolved"] = False
            out["pytest_exit"] = None
            out["error"] = f"pytest timed out or failed: {type(ex).__name__}"
            return out
        out["pytest_exit"] = tr["exit_code"]
        out["resolved"] = tr["exit_code"] == 0
        out["pytest_tail"] = (tr["stdout"] + tr["stderr"])[-1200:]
        if log_dir:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            (Path(log_dir) / f"{task['instance_id']}.log").write_text(tr["stdout"] + "\n" + tr["stderr"])
    finally:
        out["seconds"] = round(time.time() - t0, 1)
        if not keep:
            sb.stop()
        else:
            out["container"] = sb.name
    return out

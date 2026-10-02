"""Remove a task's Docker image once its trials are graded, while a benchmark runs.

    tbench/clean_images.py <pid of the run> <its job folder> <tries per task>

DeepSWE gives every task its own prebuilt image (about 2.7 GB each, 113 of them), and Harbor only
removes images it built itself: a whole run would fill the disk a third of the way in. fair.sh
starts this next to Harbor; every minute it finds the run's graded trials and removes their
task's image, and stops when the run does. `docker image rm` (never -f) refuses an image a
container still uses, so a try still running keeps its image and it goes on a later round.
Plain Python (no packages).
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

JOBS = Path.home() / ".local" / "state" / "purr" / "tbench"
TASKS = Path.home() / ".cache" / "harbor" / "tasks" / "packages"


def image_of(task_name):
    """The prebuilt image a package task runs in (its task.toml), or None (built from a Dockerfile)."""
    for toml in sorted(TASKS.glob(f"{task_name}/*/task.toml")):
        for line in toml.read_text().splitlines():
            if line.strip().startswith("docker_image"):
                return line.split("=", 1)[1].strip().strip('"')
    return None


def graded_images(job, attempts, final=False):
    """Images of the tasks in this job whose tries are all graded (any graded try once the run
    has ended): removing one sooner would make the next try download it again."""
    graded = {}
    for result in Path(job).glob("*/result.json"):
        try:
            name = json.loads(result.read_text()).get("task_name")
        except (OSError, ValueError):
            continue
        if name:
            graded[name] = graded.get(name, 0) + 1
    images = set()
    for name, n in graded.items():
        image = image_of(name) if (final or n >= attempts) else None
        if image:
            images.add(image)
    return images


def remove(image):
    """True once the image is gone (removed now, or already)."""
    res = subprocess.run(["docker", "image", "rm", image], capture_output=True, text=True)
    return res.returncode == 0 or "No such image" in res.stderr


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def main(argv):
    # q in the bench window (or ctrl+c) interrupts the whole process group: keep going until Harbor
    # has stopped, then make the last pass, or the images of the last graded tasks stay behind
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    pid, job, attempts = int(argv[0]), Path(argv[1]), int(argv[2])
    gone = set()
    while True:
        running = alive(pid)
        for image in sorted(graded_images(job, attempts, final=not running) - gone):
            if remove(image):
                gone.add(image)
        if not running:
            return 0
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

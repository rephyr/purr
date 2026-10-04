"""Every test run keeps its temporary files in one folder of its own and deletes it at the end.
Without this, each run left a few hundred folders in /tmp (small git repos among them): 36,000
of them filled /tmp's inode table one day, and programs on the desktop couldn't write files.

Run the tests as  python3 -m unittest discover -s tests -t .  so this runs first."""

import atexit
import os
import shutil
import tempfile

_RUN = tempfile.mkdtemp(prefix="purr-tests-")
tempfile.tempdir = _RUN
os.environ["TMPDIR"] = _RUN  # commands the tests start (git, sh, harbor fakes) use it too
atexit.register(shutil.rmtree, _RUN, ignore_errors=True)

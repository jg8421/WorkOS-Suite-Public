import concurrent.futures
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import psutil
from suite.runtime import ComponentSpec, RuntimeManager


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.manager = RuntimeManager(self.root / "runtime")

    def tearDown(self):
        self.manager.close()
        # Windows can retain a just-closed file handle briefly after the kernel
        # job reports zero members. A real leaked 60-second child still fails.
        deadline=time.monotonic()+2
        while True:
            try:self.tmp.cleanup();break
            except PermissionError:
                if time.monotonic()>=deadline:raise
                time.sleep(.05)

    def worker(self, name="worker", **kwargs):
        return ComponentSpec(name, (sys.executable, "-c", "import time; print('private worker log',flush=True); time.sleep(60)"), self.root, **kwargs)

    def test_real_start_stop_restart_and_private_logs(self):
        self.manager.register(self.worker())
        first = self.manager.ensure("worker")
        self.assertEqual(first["status"], "running")
        self.assertTrue(first["owned"])
        self.assertTrue(psutil.pid_exists(first["pid"]))
        second = self.manager.restart("worker")
        self.assertNotEqual(second["pid"], first["pid"])
        self.assertFalse(psutil.pid_exists(first["pid"]))
        stopped = self.manager.stop("worker")
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(psutil.pid_exists(second["pid"]))
        public = str(self.manager.status()) + str(self.manager.events())
        self.assertNotIn("private worker log", public)
        self.assertNotIn(sys.executable, public)
        self.assertNotIn(str(self.root), public)

    def test_parallel_ensure_is_one_owned_process(self):
        self.manager.register(self.worker())
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            states = list(pool.map(lambda _: self.manager.ensure("worker"), range(5)))
        self.assertEqual(len({s["pid"] for s in states}), 1)
        self.assertEqual(sum(e["action"] == "starting" for e in self.manager.events()), 1)

    def test_borrowed_component_is_never_signalled(self):
        external = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self.manager.register(self.worker(healthcheck=lambda: external.poll() is None))
            self.assertEqual(self.manager.ensure("worker")["status"], "borrowed")
            self.assertEqual(self.manager.restart("worker")["status"], "borrowed")
            self.manager.stop("worker"); self.manager.close()
            self.assertIsNone(external.poll())
            self.assertFalse(self.manager.status("worker")["can_stop"])
        finally:
            external.terminate(); external.wait(timeout=5)

    def test_creation_identity_mismatch_never_signals_live_pid(self):
        self.manager.register(self.worker())
        started = self.manager.start("worker")
        record = self.manager._records["worker"]
        identity = record["identity"]
        record["identity"] = identity + 1
        with patch.object(psutil.Process, "terminate") as terminate, patch.object(psutil.Process, "kill") as kill:
            self.manager.stop("worker")
            terminate.assert_not_called(); kill.assert_not_called()
        self.assertTrue(psutil.pid_exists(started["pid"]))
        record.update(identity=identity, owned=True)

    def test_stop_cleans_only_verified_owned_descendants(self):
        receipt = self.root / "child.pid"
        script = "import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(60)"
        self.manager.register(ComponentSpec("tree", (sys.executable, "-c", script, str(receipt)), self.root))
        parent = self.manager.start("tree")
        deadline = time.monotonic() + 5
        while not receipt.exists() and time.monotonic() < deadline: time.sleep(.03)
        self.assertTrue(receipt.exists())
        child = int(receipt.read_text())
        unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            protected=self.manager.protected_pids()
            self.assertIn(parent['pid'],protected)
            self.assertIn(child,protected)
            self.assertNotIn(unrelated.pid,protected)
            self.manager.stop("tree")
            self.assertFalse(psutil.pid_exists(parent["pid"]))
            deadline = time.monotonic() + 2
            while psutil.pid_exists(child) and time.monotonic() < deadline: time.sleep(.03)
            self.assertFalse(psutil.pid_exists(child))
            self.assertIsNone(unrelated.poll())
            self.assertEqual(self.manager.protected_pids(),[])
        finally:
            unrelated.terminate(); unrelated.wait(timeout=5)

    def test_timeout_cleans_owned_process_and_reports_safe_reason(self):
        self.manager.register(self.worker(healthcheck=lambda: False, start_timeout=.15))
        result = self.manager.start("worker")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["owned"])
        self.assertIsNotNone(self.manager._records["worker"]["process"].poll())

    def test_fixed_registration_and_missing_dependency(self):
        self.manager.register(self.worker(dependency_reason="请安装并登录千问官方客户端"))
        with patch("suite.runtime.subprocess.Popen") as start:
            self.assertEqual(self.manager.start("worker")["status"], "needs_setup")
            start.assert_not_called()
        with self.assertRaises(ValueError): self.manager.start("not-registered")
        with self.assertRaises(ValueError): self.manager.register(self.worker())
        with self.assertRaises(ValueError): self.manager.register(ComponentSpec("../evil", (sys.executable,), self.root))
        self.manager.close()
        with self.assertRaises(RuntimeError): self.manager.start("worker")

    def test_identity_inspection_failure_cleans_only_new_popen(self):
        self.manager.register(self.worker())
        actual_popen = subprocess.Popen
        created=[]
        def capture(*args,**kwargs):
            process=actual_popen(*args,**kwargs);created.append(process);return process
        with patch('suite.runtime.subprocess.Popen',side_effect=capture),patch('suite.runtime.psutil.Process',side_effect=psutil.AccessDenied):
            state=self.manager.start('worker')
        self.assertEqual(state['status'],'failed')
        self.assertEqual(len(created),1)
        self.assertIsNotNone(created[0].poll())


if __name__ == "__main__": unittest.main()

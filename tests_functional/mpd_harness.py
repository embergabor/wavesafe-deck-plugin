"""Boot a real, throwaway MPD daemon for functional tests.

Renders the repo's actual `daemon/mpd.conf.tmpl` (so the shipped template is what
gets validated), with exactly one test-only substitution: the `pipewire` audio
output becomes `null` (macOS has no PipeWire; `null` is built into every MPD and
plays at realtime speed, so playback/seek/elapsed behave like a real output).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time

MPD_BIN = shutil.which("mpd")

# Plugin repo root is one level up from tests_functional/ (flat standalone layout).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATE = os.path.join(_REPO_ROOT, "daemon", "mpd.conf.tmpl")


class MPDHarness:
    """start() → a real mpd on a unix socket in a short-lived tmp dir."""

    def __init__(
        self,
        output_type: str = "null",
        music_dir: str | None = None,
        state_file_interval: str | None = None,
    ):
        # Short base dir: macOS unix-socket paths are capped at 104 bytes and
        # tempfile defaults under /var/folders/... get close to that.
        # output_type: "null" for tests (silent, realtime), "osx" for an audible
        # demo on macOS speakers.
        # state_file_interval: override the template's value (the shipped 60s is
        # too slow for tests that verify autosave-before-a-power-cut).
        self.output_type = output_type
        self.state_file_interval = state_file_interval
        self.root = tempfile.mkdtemp(prefix="wsf", dir="/tmp")
        self.music_dir = music_dir or os.path.join(self.root, "music")
        self.data_dir = os.path.join(self.root, "data")
        self.socket = os.path.join(self.root, "mpd.sock")
        self.conf_path = os.path.join(self.root, "mpd.conf")
        self.proc: subprocess.Popen | None = None
        os.makedirs(self.music_dir, exist_ok=True)
        os.makedirs(os.path.join(self.data_dir, "playlists"), exist_ok=True)

    def render_conf(self) -> str:
        with open(TEMPLATE, "r", encoding="utf-8") as f:
            conf = f.read()
        output_block = (
            'audio_output {\n'
            f'    type "{self.output_type}"\n'
            '    name "test output"\n'
            '    mixer_type "software"\n'
            '}'
        )
        conf = (
            conf.replace("{{MUSIC_DIR}}", self.music_dir)
            .replace("{{DATA_DIR}}", self.data_dir)
            .replace("{{SOCKET}}", self.socket)
            .replace("{{AUDIO_OUTPUT}}", output_block)
        )
        # Drop the TCP bind so parallel test runs can't collide on port 6600.
        conf = re.sub(r'bind_to_address\s+"127\.0\.0\.1"\n', "", conf)
        conf = re.sub(r'port\s+"6600"\n', "", conf)
        if self.state_file_interval is not None:
            conf = re.sub(
                r'state_file_interval\s+"\d+"',
                f'state_file_interval "{self.state_file_interval}"',
                conf,
            )
        with open(self.conf_path, "w", encoding="utf-8") as f:
            f.write(conf)
        return conf

    def start(self, timeout: float = 30.0) -> None:
        # Generous timeout: macOS Gatekeeper can stall the first exec of a
        # freshly installed binary for several seconds.
        assert MPD_BIN, "mpd binary not installed"
        self.render_conf()
        self._stderr_path = os.path.join(self.root, "mpd.stderr")
        self._stderr_file = open(self._stderr_path, "wb")
        self.proc = subprocess.Popen(
            [MPD_BIN, "--no-daemon", self.conf_path],
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_file,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.socket):
                return
            if self.proc.poll() is not None:
                log = self._read_log()
                raise RuntimeError(f"mpd exited at startup (rc={self.proc.returncode}):\n{log}")
            time.sleep(0.05)
        raise RuntimeError(f"mpd socket did not appear within {timeout}s:\n{self._read_log()}")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None
        if getattr(self, "_stderr_file", None) is not None:
            self._stderr_file.close()
            self._stderr_file = None

    def kill_hard(self) -> None:
        """SIGKILL — simulates a power cut: MPD gets NO chance to write state."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc = None
        if getattr(self, "_stderr_file", None) is not None:
            self._stderr_file.close()
            self._stderr_file = None

    def restart(self) -> None:
        """SIGTERM (lets MPD write state_file/stickers) then boot again."""
        self.stop()
        # Socket file lingers after shutdown; remove so start() waits for the new one.
        if os.path.exists(self.socket):
            os.unlink(self.socket)
        self.start()

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _read_log(self) -> str:
        parts = []
        for label, path in (
            ("mpd.log", os.path.join(self.data_dir, "mpd.log")),
            ("stderr", getattr(self, "_stderr_path", "")),
        ):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    parts.append(f"--- {label} ---\n" + f.read()[-2000:])
            except OSError:
                parts.append(f"--- {label}: (missing) ---")
        return "\n".join(parts)

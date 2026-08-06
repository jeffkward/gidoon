"""Shared test plumbing: loads bin/gidoon as a module, fakes the Telegram
transport, and builds throwaway instance configs. No network anywhere."""
import importlib.machinery
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gidoon  # noqa: E402


def load_daemon_module():
    """Import bin/gidoon (no .py extension) as a module for testing."""
    path = os.path.join(ROOT, "bin", "gidoon")
    loader = importlib.machinery.SourceFileLoader("gidoon_daemon", path)
    spec = importlib.util.spec_from_loader("gidoon_daemon", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


DAEMON = load_daemon_module()


class FakeTg:
    """Records every outbound call; serves canned updates. Never touches
    the network."""

    def __init__(self, pending_updates=None):
        self.sent = []          # (chat_id, text)
        self.edits = []         # (chat_id, message_id, text)
        self.reactions = []     # (chat_id, message_id, emoji)
        self.actions = []       # chat_id
        self.menus = []         # commands payloads
        self.get_updates_calls = []   # (offset, long)
        self.pending_updates = list(pending_updates or [])
        self._msg_id = 100

    def send(self, chat_id, text):
        self._msg_id += 1
        self.sent.append((chat_id, text))
        return {"message_id": self._msg_id}

    def edit(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))
        return {}

    def react(self, chat_id, message_id, emoji="👀"):
        self.reactions.append((chat_id, message_id, emoji))
        return True

    def chat_action(self, chat_id):
        self.actions.append(chat_id)
        return True

    def set_my_commands(self, commands):
        self.menus.append(commands)
        return True

    def get_updates(self, offset, long=True):
        self.get_updates_calls.append((offset, long))
        updates, self.pending_updates = self.pending_updates, []
        return updates


def write_config(tmpdir, name="testbot", **overrides):
    """Write a minimal instance TOML into tmpdir and load it with state
    files also rooted in tmpdir. overrides are raw TOML lines' values."""
    env_path = os.path.join(tmpdir, f"{name}.env")
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("GIDOON_BOT_TOKEN=000:fake\nGIDOON_CHAT_ID=42\n")
    fields = {
        "label": f'"{name}"',
        "env_file": f'"{env_path}"',
        "cwd": f'"{tmpdir}"',
    }
    fields.update(overrides)
    path = os.path.join(tmpdir, f"{name}.toml")
    with open(path, "w", encoding="utf-8") as f:
        for key, value in fields.items():
            f.write(f"{key} = {value}\n")
    return gidoon.load_config(path, state_dir=tmpdir)


def make_daemon(cfg, tg=None, chat_id=42):
    return DAEMON.Daemon(tg or FakeTg(), cfg, chat_id)


def text_msg(text, chat_id=42, message_id=1):
    return {"message_id": message_id, "text": text, "chat": {"id": chat_id}}

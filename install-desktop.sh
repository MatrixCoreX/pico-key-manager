#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$task_root/.tmp"
export TMPDIR="$task_root/.tmp"
if [[ ! -x "$task_root/.venv/bin/python" ]]; then
    uv venv --system-site-packages --python /usr/bin/python3 "$task_root/.venv"
fi
uv pip install --python "$task_root/.venv/bin/python" -r "$task_root/requirements-desktop.txt"
"$task_root/.venv/bin/python" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw; import fido2'
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
python3 - "$task_root" <<'PY'
from pathlib import Path
import os,shlex,sys
root=Path(sys.argv[1]); home=Path.home()
launcher=home/'.local/bin/pico-key-manager'
launcher.write_text('#!/usr/bin/env bash\nset -e\ncd -- '+shlex.quote(str(root))+'\nexec '+shlex.quote(str(root/'.venv/bin/python'))+' -m pico_key_manager "$@"\n')
launcher.chmod(0o755)
icon=home/'.local/share/icons/hicolor/scalable/apps/io.github.guagua.PicoKeyManager.svg'
icon.write_bytes((root/'assets/pico-key.svg').read_bytes())
desktop=home/'.local/share/applications/io.github.guagua.PicoKeyManager.desktop'
desktop.write_text('[Desktop Entry]\nType=Application\nVersion=1.0\nName=Pico Key 管理器\nName[en]=Pico Key Manager\nComment=管理 Pico 安全密钥的 PIN、Passkey、签名测试和固件\nExec='+str(launcher)+'\nTryExec='+str(root/'.venv/bin/python')+'\nIcon=io.github.guagua.PicoKeyManager\nTerminal=false\nCategories=Utility;\nKeywords=Pico;FIDO2;Passkey;YubiKey;YunbiKey;PIN;安全密钥;\nStartupNotify=true\nStartupWMClass=io.github.guagua.PicoKeyManager\n')
print('Installed:',desktop)
PY
desktop-file-validate "$HOME/.local/share/applications/io.github.guagua.PicoKeyManager.desktop"
update-desktop-database "$HOME/.local/share/applications"
if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" || true
fi
echo '应用菜单中搜索：Pico Key 管理器'

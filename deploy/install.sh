#!/usr/bin/env bash
# Install or update the NKNU facts MCP on the lab machine (Ubuntu 24.04, systemd). Idempotent: run it again to pull
# the code, re-sync the virtualenv and reinstall the units. Never run it on the development Mac.
#
#   first time:  sudo git clone https://github.com/loptio/nknu-mcp.git /opt/nknu && sudo bash /opt/nknu/deploy/install.sh
#   update:      sudo bash /opt/nknu/deploy/install.sh
#
# Optional environment: NKNU_REPO_URL (clone source when /opt/nknu is missing), NKNU_BRANCH (default: the checkout's
# current branch, else main), UV_BIN (path to uv when it is not on root's PATH).
set -euo pipefail

APP_DIR=/opt/nknu
PY_DIR=/opt/nknu-python # uv-managed CPython lives outside /root and /home, so user nknu can run it under ProtectHome
DATA_DIR=/var/lib/nknu
ENV_DIR=/etc/nknu
ENV_FILE=$ENV_DIR/env
UNIT_DIR=/etc/systemd/system
DROPIN_DIR=$UNIT_DIR/nknu-backup.service.d
WRAPPER=/usr/local/bin/nknu
SVC_USER=nknu
TIMERS=(nknu-crawl.timer nknu-build.timer nknu-backup.timer)

say() { printf '==> %s\n' "$*"; }
warn() { printf '注意：%s\n' "$*" >&2; }
die() {
  printf 'install.sh：%s\n' "$*" >&2
  exit 1
}

[[ $(uname -s) == Linux ]] || die "只能在实验室机器（Ubuntu 24.04）上执行，不要在开发用的 Mac 上执行"
[[ -d /run/systemd/system ]] || die "这台机器不是用 systemd 开机的"
[[ $EUID -eq 0 ]] || die "请用 sudo 执行：sudo bash $0"
command -v git >/dev/null || die "缺少 git：sudo apt install git"
command -v runuser >/dev/null || die "缺少 runuser（util-linux 套件）"

git_() { git -c safe.directory="$APP_DIR" -C "$APP_DIR" "$@"; }

find_uv() {
  local home p
  if [[ -n ${UV_BIN:-} ]]; then
    printf '%s\n' "$UV_BIN"
    return 0
  fi
  if command -v uv >/dev/null; then
    command -v uv
    return 0
  fi
  if [[ -n ${SUDO_USER:-} ]]; then # sudo drops ~/.local/bin from PATH
    home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
    if [[ -n $home && -x $home/.local/bin/uv ]]; then
      printf '%s\n' "$home/.local/bin/uv"
      return 0
    fi
  fi
  for p in /usr/local/bin/uv /root/.local/bin/uv /snap/bin/uv; do
    if [[ -x $p ]]; then
      printf '%s\n' "$p"
      return 0
    fi
  done
  return 1
}

UV=$(find_uv) || die "找不到 uv。照 https://docs.astral.sh/uv/getting-started/installation/ 安装后，执行：sudo UV_BIN=\"\$(command -v uv)\" bash $0"
[[ -x $UV ]] || die "UV_BIN 不是可执行档：$UV"

# 1. system user: no login shell, home is the data directory
if ! id -u "$SVC_USER" >/dev/null 2>&1; then
  say "建立系统用户 $SVC_USER"
  useradd --system --user-group --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin \
    --comment "NKNU facts MCP" "$SVC_USER"
fi

# 2. code: fast-forward only, so local edits on this machine stop the script instead of being overwritten
if [[ -d $APP_DIR/.git ]]; then
  BRANCH=${NKNU_BRANCH:-$(git_ rev-parse --abbrev-ref HEAD)}
  [[ $BRANCH != HEAD ]] || die "$APP_DIR 不在任何分支上；用 sudo NKNU_BRANCH=main bash $0 指定"
  say "更新 $APP_DIR（分支 $BRANCH）"
  git_ fetch --quiet origin
  git_ switch --quiet "$BRANCH"
  git_ merge --ff-only --quiet "origin/$BRANCH"
else
  [[ ! -e $APP_DIR ]] || die "$APP_DIR 已存在但不是 git 仓库，请先移走"
  BRANCH=${NKNU_BRANCH:-main}
  REPO_URL=${NKNU_REPO_URL:-https://github.com/loptio/nknu-mcp.git}
  say "下载程式码 $REPO_URL（分支 $BRANCH）到 $APP_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

# 3. virtualenv from uv.lock (root-owned and read-only to the services, which run with ProtectSystem=strict)
say "安装 Python 环境（uv sync --frozen --no-dev）"
(cd "$APP_DIR" && UV_PYTHON_INSTALL_DIR="$PY_DIR" UV_LINK_MODE=copy "$UV" sync --frozen --no-dev --compile-bytecode --quiet)
[[ -x $APP_DIR/.venv/bin/nknu ]] || die "uv sync 之后找不到 $APP_DIR/.venv/bin/nknu"
py_real=$(readlink -f "$APP_DIR/.venv/bin/python")
case $py_real in
/root/* | /home/*) die "虚拟环境的 Python 在 $py_real，用户 $SVC_USER 读不到；删掉 $APP_DIR/.venv 后重跑" ;;
esac
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/src" >/dev/null
chmod -R go+rX,go-w "$APP_DIR"
if [[ -d $PY_DIR ]]; then chmod -R go+rX,go-w "$PY_DIR"; fi

# 4. data directory and the environment file (copied once, never overwritten)
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA_DIR"
install -d -o root -g "$SVC_USER" -m 0750 "$ENV_DIR"
if [[ ! -f $ENV_FILE ]]; then
  install -o root -g "$SVC_USER" -m 0640 "$APP_DIR/deploy/env.example" "$ENV_FILE"
  say "已建立 $ENV_FILE（从 deploy/env.example 复制）"
fi
bash -n "$ENV_FILE" || die "$ENV_FILE 有语法错误（值里有空格或特殊字元时要加双引号）"

# 5. backup directory: created for user nknu and made writable to nknu-backup.service through a drop-in
backup_dir=$(
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE" >/dev/null
  printf '%s' "${NKNU_BACKUP_DIR:-}"
)
if [[ -z $backup_dir ]]; then
  warn "$ENV_FILE 没有设定 NKNU_BACKUP_DIR，每天的备份会失败"
  rm -f "$DROPIN_DIR/backup-dir.conf"
else
  [[ $backup_dir == /* && $backup_dir != *[[:space:]]* ]] || die "NKNU_BACKUP_DIR 必须是绝对路径而且不能有空格：$backup_dir"
  if [[ ! -d $backup_dir ]]; then
    install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$backup_dir"
  fi
  runuser -u "$SVC_USER" -- test -w "$backup_dir" ||
    warn "用户 $SVC_USER 不能写入 $backup_dir，备份会失败（NAS 请检查挂载的权限）"
  install -d -m 0755 "$DROPIN_DIR"
  printf '# written by deploy/install.sh from NKNU_BACKUP_DIR in %s\n[Service]\nReadWritePaths=%s\n' \
    "$ENV_FILE" "$backup_dir" >"$DROPIN_DIR/backup-dir.conf"
fi

# 6. `nknu` on PATH: the CLI as user nknu with the services' environment, so a manual crawl or review
#    reads and writes the same /var/lib/nknu as the timers (without the env file NKNU_DATA_DIR would be ./data)
say "安装 $WRAPPER"
cat >"$WRAPPER" <<'WRAPPER_EOF'
#!/bin/sh
# Installed by /opt/nknu/deploy/install.sh: run the nknu CLI as user nknu with /etc/nknu/env (same data as the timers).
set -eu
inner='set -a; . /etc/nknu/env; set +a; cd /var/lib/nknu; exec /opt/nknu/.venv/bin/nknu "$@"'
if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u nknu -- /bin/sh -c "$inner" nknu "$@"
fi
exec sudo -u nknu -- /bin/sh -c "$inner" nknu "$@"
WRAPPER_EOF
chmod 0755 "$WRAPPER"

# 7. systemd units
say "安装 systemd unit"
install -m 0644 "$APP_DIR"/deploy/systemd/nknu-*.service "$APP_DIR"/deploy/systemd/nknu-*.timer "$UNIT_DIR/"
systemctl daemon-reload

# 8. facts.db schema and the place vocabulary (config/entities.yaml); safe to repeat
say "初始化 facts.db 并载入地点词汇"
"$WRAPPER" init

# 9. timers now; the MCP service starts once serve.db exists (ConditionPathExists in the unit)
systemctl enable --quiet --now "${TIMERS[@]}"
systemctl enable --quiet nknu-mcp.service
first_run=0
if [[ -f $DATA_DIR/serve.db ]]; then
  say "用新程式重建 serve.db，并重启 nknu-mcp"
  "$WRAPPER" build
  systemctl restart nknu-mcp.service
  sleep 3
  systemctl is-active --quiet nknu-mcp.service ||
    warn "nknu-mcp 没有起来：sudo journalctl -u nknu-mcp -n 50"
else
  first_run=1
fi

if grep -q 'REPLACE-ME' "$ENV_FILE"; then
  warn "对外主机名还没设定：编辑 $ENV_FILE 的 NKNU_ALLOWED_HOSTS（见 $APP_DIR/deploy/tunnel/README.md），再 sudo systemctl restart nknu-mcp"
fi
if [[ $first_run == 1 ]]; then
  cat <<'NEXT_EOF'

第一次安装，接下来依序执行：
  1. sudo systemctl start nknu-crawl.service   # 第一次抓取，约 4 分钟；进度：sudo journalctl -u nknu-crawl -f
  2. nknu review                               # 第一次约 87 条都是「新增」，看完后按 y 全部核准
  3. sudo systemctl start nknu-mcp.service
  4. curl -s http://127.0.0.1:8765/health      # 应该看到 "status":"ok"
  5. systemctl list-timers 'nknu-*'            # 应该看到 3 个 timer
NEXT_EOF
fi
say "完成"

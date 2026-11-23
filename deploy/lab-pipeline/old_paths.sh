#!/usr/bin/env bash
# 11-19：这批程序搬进仓库（deploy/lab-pipeline）以前，放在 research/pipeline_2026-11-02/lab_pc/ 等几个地方。很多旧的
# 命令、笔记、别的脚本还写着旧路径，所以在旧位置各留一个很小的「转接档」：它只做一件事，去跑新位置的同名程序。
# 这样旧命令照样能跑，而且两个地方不会各有一份会慢慢走样的程序。
#   bash deploy/lab-pipeline/old_paths.sh [项目根目录]      （实验室电脑上是 /work；install.sh 装完会提醒要不要跑）
# 旧位置上原来的程序档先存进 research/pipeline_2026-11-02/lab_pc/previous/old-paths-<时间>.tar 再换成转接档。
# research/ 不进 git，转接档和备份也不进。
set -eu
ROOT=${1:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)}
cd "$ROOT"
[ -d deploy/lab-pipeline ] || { echo "这里没有 deploy/lab-pipeline：$ROOT"; exit 2; }
P=research/pipeline_2026-11-02
PY=("$P/lab_pc/nightly_ingest.py:nightly_ingest.py" "$P/lab_pc/build_helpers.py:build_helpers.py"
    "$P/lab_pc/site_report.py:site_report.py" "$P/lab_pc/retry_cut_members.py:retry_cut_members.py"
    "$P/totext_progress.py:totext_progress.py" "research/eval_2026-11-01/offline_eval.py:eval/offline_eval.py"
    "research/skeleton_2026-11-01/check_realdata.py:eval/check_realdata.py")
SH=("$P/lab_pc/build.sh:build.sh" "$P/lab_pc/lanes.sh:lanes.sh" "$P/lab_pc/check.sh:check.sh")
# the old copies of the tests and of the host files: kept in the tar below and taken away (the tests are
# tests/pipeline/ now; compose.yaml, Dockerfile, nightly_ingest.cmd are deploy/lab-pipeline/'s)
GONE=("$P/lab_pc/test_kit_1108.py" "$P/lab_pc/test_ingest_1116.py" "$P/lab_pc/compose.yaml" "$P/lab_pc/Dockerfile"
      "$P/lab_pc/nightly_ingest.cmd" "$P/lab_pc/README.md")
keep=()
for pair in "${PY[@]}" "${SH[@]}"; do
  f=${pair%%:*}
  [ -f "$f" ] && ! head -6 "$f" | grep -q "转接档" && keep+=("$f")
done
for f in "${GONE[@]}"; do [ -f "$f" ] && keep+=("$f"); done
if ((${#keep[@]})); then
  # kept beside the state, under research/ (in no commit): the old files are research/'s, not the repository's
  mkdir -p "$P/lab_pc/previous"
  tar -cf "$P/lab_pc/previous/old-paths-$(TZ=CST-8 date '+%Y%m%d-%H%M%S').tar" "${keep[@]}"
  echo "旧位置原来的 ${#keep[@]} 个档存进了 $P/lab_pc/previous/"
fi
for f in "${GONE[@]}"; do
  [ -f "$f" ] && rm -f "$f"
done
if [ -d "$P/lab_pc" ] && [ ! -e "$P/lab_pc/README.md" ]; then
  printf '%s\n' "# 这个资料夹现在只放状态" "" \
    "程序 11-19 起在仓库的 \`deploy/lab-pipeline/\`（说明看那里的 README.md）。这里留的是程序写的东西：锁、日志、" \
    "\`build.status\`、换库前留下的旧报告 \`previous/\`、冒烟测试，以及照旧路径转去新程序的转接档。" > "$P/lab_pc/README.md"
fi
for pair in "${PY[@]}"; do
  f=${pair%%:*}; to=${pair##*:}
  mkdir -p "$(dirname "$f")"
  cat > "$f" <<PYEOF
"""转接档（11-19）：这支程序搬到仓库的 deploy/lab-pipeline/$to 了；这里只是照旧路径叫它。要改程序，改那一份。"""
import runpy
import sys
from pathlib import Path

_root = next(d for d in Path(__file__).resolve().parents if (d / "deploy" / "lab-pipeline").is_dir())
_real = _root / "deploy" / "lab-pipeline" / "$to"
sys.path.insert(0, str(_real.parent))
if __name__ == "__main__":  # run as a program
    sys.argv[0] = str(_real)
    runpy.run_path(str(_real), run_name="__main__")
else:  # imported as a module: the real one takes this name
    import importlib.util

    _spec = importlib.util.spec_from_file_location(__name__, _real)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[__name__] = _module
    _spec.loader.exec_module(_module)
PYEOF
done
for pair in "${SH[@]}"; do
  f=${pair%%:*}; to=${pair##*:}
  mkdir -p "$(dirname "$f")"
  cat > "$f" <<SHEOF
#!/usr/bin/env bash
# 转接档（11-19）：这支脚本搬到仓库的 deploy/lab-pipeline/$to 了；这里只是照旧路径叫它。要改脚本，改那一份。
d=\$(cd -- "\$(dirname -- "\${BASH_SOURCE[0]}")" && pwd)
while [ "\$d" != / ] && [ ! -d "\$d/deploy/lab-pipeline" ]; do d=\$(dirname -- "\$d"); done
exec bash "\$d/deploy/lab-pipeline/$to" "\$@"
SHEOF
done
echo "旧位置的转接档写好了：$((${#PY[@]} + ${#SH[@]})) 个"

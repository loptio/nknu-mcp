#!/usr/bin/env bash
# Lab-PC readiness check (lab_pc kit, 11-03), run in the nknu-pipeline container:
#   docker compose -f C:\nknu-transfer\build\compose.yaml run --rm check
# About half a minute; it loads no model, converts nothing and writes nothing to /work or /models (no bytecode).
#   1. the image: Python; torch and the GPU it sees through CUDA (with a small computation on the GPU); Docling's
#      device; LibreOffice (soffice --version); bsdtar (lane C's RAR files); the Chinese fonts pdfium draws with
#   2. the volumes: the code in /work/src; the 教务处 export; the to-text cache; the lanes' files; both retrieval
#      models at their pins (python -m nknu.core.models check) with their weight files complete; Docling's layout
#      and table models (every file complete); RapidOCR's three models inside its wheel
#   3. room: free space on /work, shared memory, the memory and CPUs the container gets
# Each line is [通过] (fine), [提醒] (worth knowing, not a failure) or [没通过] (fix it first).
# Exit 0: nothing failed; 1: something did (the [没通过] lines and the last line say what).
# Elsewhere, for testing: NKNU_CHECK_WORK (default /work), NKNU_CHECK_PYTHON (default python), e.g. on the Mac
#   NKNU_CHECK_WORK=$PWD NKNU_CHECK_PYTHON="uv run --no-sync python" bash research/pipeline_2026-11-02/lab_pc/check.sh
set -u
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORK=${NKNU_CHECK_WORK:-/work}
read -r -a PY <<< "${NKNU_CHECK_PYTHON:-python}"
EXPORT=archive-export/aca-20261102
P=research/pipeline_2026-11-02
PROG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CACHE=$P/totext/cache
COMPOSE='docker compose -f C:\nknu-transfer\build\compose.yaml'
FAILS=()

pass() { printf '  [通过] %s\n' "$*"; }
note() { printf '  [提醒] %s\n' "$*"; }
fail() { local what=$1; shift; printf '  [没通过] %s\n' "$*"; FAILS+=("$what"); }
limit() {  # limit SECONDS CMD...: the command, stopped after SECONDS when coreutils timeout is there
  local s=$1; shift
  if command -v timeout > /dev/null 2>&1; then timeout "$s" "$@"; else "$@"; fi
}
py() { limit 600 "${PY[@]}" "$@"; }

echo "== 实验室电脑检查 $(date '+%F %T')：只看环境和档案，不载入模型、不转文字（约半分钟）"
if [[ ! -f /.dockerenv ]]; then note "不在 Docker 容器里跑（测试用？）：GPU 和路径的结果只代表这台机器"; fi
if ! cd "$WORK" 2> /dev/null; then
  echo "  [没通过] 找不到 ${WORK}：nknu-work 磁碟区没挂上"
  echo "== 结果：没通过。先建两个磁碟区、跑 extract，再跑 check"
  exit 1
fi

echo "-- 1. 映像里的程序"
py - <<'PYEOF'
import platform, sys
def say(tag, msg):
    print(f"  [{tag}] {msg}", flush=True)
bad = 0
if sys.version_info[:2] == (3, 13):
    say("通过", f"Python {platform.python_version()}（{sys.executable}）")
else:
    bad += 1; say("没通过", f"Python {platform.python_version()}：这个项目要 3.13（{sys.executable}）")
try:
    import torch
except Exception as e:
    say("没通过", f"torch 载入失败：{type(e).__name__}: {e}")
    sys.exit(1)
if not torch.cuda.is_available():
    say("没通过", f"torch {torch.__version__}（CUDA {torch.version.cuda}）看不到 GPU：显卡驱动要 580 版以上（这个 torch "
        "用 CUDA 13），Docker Desktop 要用 WSL 2 引擎；容器根本建不起来的话，错误讯息里会有 nvidia 或 gpu")
    sys.exit(1)
props = torch.cuda.get_device_properties(0)
sm = f"sm_{props.major}{props.minor}"
archs = torch.cuda.get_arch_list()
free, total = torch.cuda.mem_get_info(0)
try:
    v = torch._C._cuda_getDriverVersion()  # what the driver supports, e.g. 13020 = CUDA 13.2
    drv = f"驱动支持到 CUDA {v // 1000}.{v % 1000 // 10}"
except Exception:
    drv = "驱动版本读不到"
try:
    n = 1000
    x = torch.arange(n, device="cuda")
    s = int((x * 2 + 1).sum().item())  # torch's own kernels
    a = torch.ones(256, 256, device="cuda")
    m = float((a @ a)[0, 0].item())  # cuBLAS
    torch.cuda.synchronize()
    ran = s == n * n and m == 256.0
    why = "" if ran else f"结果不对（{s}、{m}）"
except Exception as e:
    ran, why = False, f"{type(e).__name__}: {e}"
devs = torch.cuda.device_count()
line = (f"torch {torch.__version__}（CUDA {torch.version.cuda}，{drv}）；GPU：{props.name}，"
        f"{total / 2**30:.1f} GiB（空着 {free / 2**30:.1f} GiB），{sm}" + (f"；共 {devs} 张卡" if devs > 1 else ""))
if ran:
    say("通过", line + "；在 GPU 上算了一小段，结果对")
else:
    bad += 1
    say("没通过", line + f"；在 GPU 上算不出来：{why}（这个 torch 编进去的架构：{' '.join(archs)}）")
try:
    from docling.utils.accelerator_utils import decide_device
    dev = decide_device("auto")
    if dev.startswith("cuda"):
        say("通过", f"Docling 用 {dev}：版面模型和表格模型（TableFormer）都在 GPU 上跑")
    else:
        bad += 1; say("没通过", f"Docling 选了 {dev}，不是 GPU")
except Exception as e:
    bad += 1; say("没通过", f"Docling 载入失败：{type(e).__name__}: {e}")
sys.exit(1 if bad else 0)
PYEOF
[[ $? -eq 0 ]] || FAILS+=("GPU 或 Python")

if command -v nvidia-smi > /dev/null 2>&1; then
  smi=$(limit 60 nvidia-smi --query-gpu=name,driver_version,memory.used,memory.total --format=csv,noheader 2>&1 | head -2)
  note "nvidia-smi：${smi//$'\n'/；}"
else
  note "容器里没有 nvidia-smi（只是看 GPU 状态用的，不影响转文字；Windows 那边可以开 PowerShell 打 nvidia-smi）"
fi

# the soffice the converter itself will start (nknu.totext.libreoffice.SOFFICE), else the one on PATH
soffice=$(py -c 'from nknu.totext.libreoffice import SOFFICE; print(SOFFICE)' 2> /dev/null | tail -1)
[[ -n $soffice ]] || soffice=$(command -v soffice 2> /dev/null)
if [[ -n $soffice && -x $soffice ]]; then
  lo_profile=$(mktemp -d)
  lo=$(limit 180 "$soffice" "-env:UserInstallation=file://$lo_profile" --headless --version < /dev/null 2>&1 \
       | grep -m1 -i libreoffice)
  rm -rf -- "$lo_profile"
  if [[ -n $lo ]]; then pass "${lo}（${soffice}；旧 Office 档先用它转成新格式）"
  else fail "LibreOffice" "$soffice 跑不起来（--version 没有输出）"; fi
else
  fail "LibreOffice" "没有 soffice（${soffice:-PATH 里找不到}；映像里应该装了 LibreOffice）"
fi

if command -v bsdtar > /dev/null 2>&1; then
  pass "$(bsdtar --version 2>&1 | head -1)（C 线解 RAR 压缩档用）"
else
  fail "bsdtar" "没有 bsdtar（映像里应该装了 libarchive-tools；C 线解 RAR 要用）"
fi

if command -v fc-list > /dev/null 2>&1; then
  cjk=$(fc-list :lang=zh-tw family 2> /dev/null | head -1)
  if [[ -n $cjk ]]; then pass "中文字型：${cjk%%,*} 等（PDF 没嵌字型时，画页面用）"
  else note "没有中文字型：PDF 没嵌字型的页面画出来会缺字（映像里应该装了 fonts-noto-cjk）"; fi
else
  note "没有 fc-list，看不出有没有中文字型"
fi

echo "-- 2. 磁碟区里的资料（/work 是程序码和资料，/models 是模型）"
if [[ -f src/nknu/totext/run.py ]]; then
  where=$(py -c 'import nknu, os; print(os.path.dirname(os.path.abspath(nknu.__file__)))' 2>&1 | tail -1)
  if [[ $where == "$PWD/src/nknu" ]]; then pass "程序码：$where"
  else fail "程序码" "nknu 不是从 $PWD/src/nknu 载入的：$where"; fi
else
  fail "程序码" "没有 $PWD/src/nknu：update.tar 解开了吗？（$COMPOSE run --rm extract update.tar）"
fi

if [[ -f $EXPORT/manifest.json ]]; then
  items=$(py -c 'import json, sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))["项目"]))' \
          "$EXPORT/manifest.json" 2>&1 | tail -1)
  if [[ $items =~ ^[0-9]+$ ]] && ((items > 0)); then pass "教务处导出：${EXPORT}，${items} 个项目"
  else fail "教务处导出" "$EXPORT/manifest.json 读不出来：$items"; fi
else
  fail "教务处导出" "没有 $EXPORT/manifest.json：base.tar 解开了吗？（$COMPOSE run --rm extract base.tar）"
fi

if [[ -d $CACHE ]]; then
  done_n=$(find "$CACHE" -name result.json -type f 2> /dev/null | wc -l | tr -d ' ')
  if ((done_n > 0)); then pass "转文字缓存：${CACHE}，已有 ${done_n} 份结果（Mac 转好的加上这台转的）"
  else fail "转文字缓存" "$CACHE 是空的：update.tar 解开了吗？"; fi
else
  fail "转文字缓存" "没有 ${CACHE}：update.tar 解开了吗？（$COMPOSE run --rm extract update.tar）"
fi

missing=()
# 11-19: the programs are in the repository (deploy/lab-pipeline, beside this script); the smoke test and the data
# files stay in the pipeline's own folder
for f in "$PROG/lanes.sh" "$PROG/build.sh" "$PROG/retry_cut_members.py" "$PROG/nightly_ingest.py" \
         "$PROG/totext_progress.py" "$P/totext_defer_urls.txt" "$P/totext_jobs.json" "$P/server_dates.json"; do
  [[ -e $f ]] || missing+=("$f")
done
if ((${#missing[@]} == 0)); then pass "转文字线、建库、每晚进库要用的档都在（$PROG/ 和 $P/）"
else fail "套件的档" "缺：${missing[*]}（update.tar 解开了吗？）"; fi

echo "   检索用的两个模型（python -m nknu.core.models check）："
py -m nknu.core.models check 2>&1 | sed 's/^/     /'
if [[ ${PIPESTATUS[0]} -eq 0 ]]; then
  pass "bge-m3、bge-reranker-v2-m3 都在 ${HF_HUB_CACHE:-~/.cache/huggingface/hub}"
else
  fail "检索模型" "检索模型不全：base.tar 解开了吗？还是不行就连网重新下载（$COMPOSE run --rm models-download）"
fi

py - <<'PYEOF'
import json, os, sys, zipfile
from pathlib import Path
def say(tag, msg):
    print(f"  [{tag}] {msg}", flush=True)
def whole(path: Path) -> str:
    """'' when the file is complete as far as its own header can tell, else why not."""
    try:
        size = path.stat().st_size
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
            return ""
        if path.suffix == ".safetensors":
            with path.open("rb") as f:
                n = int.from_bytes(f.read(8), "little")
                if not 0 < n <= 100_000_000 or 8 + n > size:
                    return "档头坏了"
                head = json.loads(f.read(n))
            end = max((v["data_offsets"][1] for k, v in head.items() if k != "__metadata__"), default=0)
            return "" if 8 + n + end == size else f"大小不对（{size} bytes，档头说 {8 + n + end}）"
        if path.suffix == ".bin":
            with zipfile.ZipFile(path) as z:
                return "" if z.namelist() else "里面是空的"
        return "" if size > 0 else "是空档"
    except FileNotFoundError:
        return "没有这个档"
    except Exception as e:
        return f"读不了（{type(e).__name__}: {e}）"
bad = 0
try:
    from huggingface_hub import try_to_load_from_cache
    from nknu.core.models import PINS, WEIGHT_FILES
    for repo, rev in PINS:
        found = [p for p in (try_to_load_from_cache(repo, f, revision=rev) for f in WEIGHT_FILES) if isinstance(p, str)]
        problems = [f"{Path(p).name}：{w}" for p in found if (w := whole(Path(p)))]
        if found and not problems:
            mb = sum(Path(p).stat().st_size for p in found) / 1e6
            say("通过", f"{repo.split('/')[-1]} 的权重档完整（{mb:,.0f} MB）")
        elif found:
            bad += 1; say("没通过", f"{repo.split('/')[-1]} 的权重档坏了：{'；'.join(problems)}")
except Exception as e:
    bad += 1; say("没通过", f"检索模型的权重档看不了：{type(e).__name__}: {e}")
try:
    from nknu.totext.docling_convert import DOCLING_ARTIFACTS as root
    need = {
        "版面模型（layout heron）": ("docling-project--docling-layout-heron",
                                   ("config.json", "preprocessor_config.json", "model.safetensors")),
        "表格模型（TableFormer accurate）": ("docling-project--docling-models/model_artifacts/tableformer/accurate",
                                         ("tm_config.json", "tableformer_accurate.safetensors")),
    }
    for label, (folder, files) in need.items():
        problems = [f"{name}：{w}" for name in files if (w := whole(root / folder / name))]
        if problems:
            bad += 1; say("没通过", f"Docling {label}在 {root / folder}：{'；'.join(problems)}（开线以前重解 base.tar；"
                          "不从网上下载：网上最新版可能和 Mac 用的不一样）")
        else:
            mb = sum((root / folder / name).stat().st_size for name in files) / 1e6
            say("通过", f"Docling {label}完整（{mb:,.0f} MB，{root / folder}）")
except Exception as e:
    bad += 1; say("没通过", f"Docling 模型看不了：{type(e).__name__}: {e}")
try:
    import onnxruntime
    from nknu.totext.ocr import rapidocr_model
    paths = [rapidocr_model(t) for t in ("det", "rec", "cls")]
    gone = [str(p) for p in paths if not p.is_file()]
    if gone:
        bad += 1; say("没通过", f"RapidOCR 的模型不在：{'、'.join(gone)}")
    else:
        say("通过", f"RapidOCR 的三个模型在套件里（{paths[0].parent}）；onnxruntime {onnxruntime.__version__}，"
            f"用 CPU（{', '.join(onnxruntime.get_available_providers())}）")
except Exception as e:
    bad += 1; say("没通过", f"RapidOCR 看不了：{type(e).__name__}: {e}")
sys.exit(1 if bad else 0)
PYEOF
[[ $? -eq 0 ]] || FAILS+=("模型档")

echo "-- 3. 空间"
# Under Docker Desktop (WSL 2) this is the virtual disk's own room up to its size limit, not the C: drive's free
# space (Microsoft: the VHD always shows its maximum size); the owner checks C: with Get-PSDrive C (README).
free_gb=$(df -Pk . 2> /dev/null | awk 'NR == 2 { printf "%d", $4 / 1048576 }')
if [[ -z $free_gb ]]; then note "看不出 /work 还剩多少空间"
elif ((free_gb < 10)); then fail "空间" "/work（Docker 的虚拟硬盘）只剩 ${free_gb} GB（至少要 10 GB）"
else note "/work 在 Docker 的虚拟硬盘里，最多还能长 ${free_gb} GB；这不是 C 盘真的剩多少：C 盘在 PowerShell 用 Get-PSDrive C 看"; fi
shm_mb=$(df -Pk /dev/shm 2> /dev/null | awk 'NR == 2 { printf "%d", $2 / 1024 }')
if [[ -n $shm_mb ]] && ((shm_mb < 1024)); then note "共享内存 /dev/shm 只有 ${shm_mb} MB（compose.yaml 的 shm_size 没生效？）"
elif [[ -n $shm_mb ]]; then pass "共享内存 /dev/shm ${shm_mb} MB"; fi
mem_gb=$(awk '/^MemTotal:/ { printf "%d", $2 / 1048576 }' /proc/meminfo 2> /dev/null)
cpus=$(nproc 2> /dev/null || echo '?')
if [[ -n $mem_gb ]] && ((mem_gb < 24)); then
  note "容器只拿到 ${mem_gb} GB 内存、${cpus} 个 CPU：两条线一起跑可能不够（Docker Desktop 的 WSL 2 默认给一半内存）"
elif [[ -n $mem_gb ]]; then pass "容器拿到 ${mem_gb} GB 内存、${cpus} 个 CPU"; fi

if ((${#FAILS[@]} == 0)); then
  echo "== 结果：全部通过。下一步：冒烟测试（$COMPOSE run --rm smoke）"
  exit 0
fi
echo "== 结果：${#FAILS[@]} 项没通过（${FAILS[*]}）：看上面标 [没通过] 的行，解决后再跑一次 check"
exit 1

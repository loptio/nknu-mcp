# 实验室电脑：转文字、建库、离线检查

这份说明给你在实验室电脑（Windows 11）上照着做。每个命令都是 PowerShell 的一行：复制、贴上、按 Enter。输出看不懂，就把整屏文字复制给 Claude。

## 程序在哪里（11-19 起）

这条线的程序（转文字线、建库脚本、每晚进库、每站报告、量测和真资料检查）11-19 起放在仓库的 `deploy/lab-pipeline/`，和别的程序一起进 git。实验室电脑上它们在 `/work/deploy/lab-pipeline/`；Windows 上的 `C:\nknu-transfer\build\compose.yaml` 和 `nightly_ingest.cmd` 是这个资料夹里同名档的副本。

程序跑出来的东西不在仓库里，照旧在 `/work/research/pipeline_2026-11-02/`：导出清单、转文字缓存、各站语料、库（`aca.db`、`aca.prev.db`）、状态档；其中 `lab_pc/` 放锁、日志、`build.status` 和每次换库前留下的旧报告。题库和基准库照旧在 `/work/research/eval_2026-11-01/`、`/work/research/skeleton_2026-11-01/`。

- 换程序：第 12 节。
- 这台电脑坏了、要在另一台重架：第 13 节。

## 0. 这是什么、不是什么

**是**：一套 Docker 设定，让这台电脑跑三件「批次工作」（一次跑完就结束，不对外服务）：

1. 把网站导出的档转成文字（网页、图片、PDF、Office、压缩档）：一开始只有教务处，11-08 起可以一次放好几个网站（第 10 节「导出清单」）；
2. 第二阶段：用转好的文字，把清单里的网站建成同一个资料库 `aca.db`；
3. 离线检查：用题库量新资料库好不好用，不叫 AI、不花 token（题库只问教务处）。

为什么搬过来：Mac 只有 16 GB 内存，而且 Docling 的表格模型在 Mac 上只能用 CPU（有表格的页一页要 10 秒以上）；这台的 NVIDIA 显卡能让它用 GPU 跑。

**不是**：不是给学生用的正式服务（MCP 服务的部署以后另外做），不抓学校网站，也不跑要花 token 的真 agent 测试。

几个词：

- **Docker**：把程序连同它需要的一切装在一起跑的工具。**映像**（image）是装好 Python、套件、LibreOffice 的模板，建一次就好；**容器**（container）是按映像开出来、真正在跑的那一份。
- **磁碟区**（volume）：Docker 管的一块硬盘空间。`nknu-work` 放程序码、教务处导出和转好的文字，`nknu-models` 放模型。容器删掉，磁碟区还在。
- **WSL 2**：Windows 里跑 Linux 的功能。Docker Desktop 靠它，显卡也经它交给容器。
- **GPU、CUDA**：GPU 是显卡；CUDA 是 NVIDIA 让程序用显卡计算的那一套。
- **SHA-256**：档案的「指纹」。复制前后指纹一样，档就没坏。
- **退出码**：程序结束时报的数字，0 是正常。PowerShell 里上一个命令的退出码存在 `$LASTEXITCODE`。

顺序：Windows 准备（第 1 节）→ 搬档（2）→ 解开、建映像、检查（3）→ 冒烟测试（4）→ 开转文字线（5）→ 转完、Claude 说可以，再跑第二阶段（7）。要转哪些网站，看第 10 节的导出清单。

**现况（11-08）**：这套设定 11-03 起在实验室电脑上真的跑过：映像建好、冒烟测试 5/5 过，转文字线、建库（`build.sh`）、离线检查都跑完过。11-08 把 `C:\nknu-transfer\build\compose.yaml` 换成新版（有 B 线和导出清单 `EXPORTS`），新加的部分第一次用时留意。出错时把整屏文字复制给 Claude。

## 1. Windows 准备（只做一次）

11-03 下午已经确认、不用再做的：显卡驱动是 597.06（够新）、Docker 的显卡测试通过了、SSH 设好了（第 8 节）、插电时不睡眠。还要确认的：Windows 更新暂停了、Docker Desktop 登入就自动开、Microsoft Store 不自动更新（下面都有写）。

用「以系统管理员身份执行」开 PowerShell。

**显卡驱动**：到 nvidia.com 的驱动下载页，找 NVIDIA RTX 4000 Ada Generation、Windows 11，装最新版。装好后看版本：

```powershell
nvidia-smi
```

表头的 `Driver Version` 要 **580 以上**（最好 595 以上），`CUDA Version` 要 13.0 以上：这套程序的 torch 用 CUDA 13.2。

只装 Windows 的驱动。**不要在 WSL 或 Ubuntu 里面装 NVIDIA 驱动或 CUDA**：Windows 的驱动会自动给 Linux 用，里面再装会把它盖掉。

**更新 WSL**：

```powershell
wsl --update
```

**Docker Desktop**：从 docker.com 下载安装，安装时用 WSL 2（默认就是）。装好后到 Settings → General，勾上：

- Use the WSL 2 based engine
- Start Docker Desktop when you sign in to your computer（登入 Windows 就自动开 Docker）

转文字跑完以前，Docker Desktop 跳出更新提示先别按：更新会重开 Docker，正在跑的线会停。

**永远不睡眠**：设定 → 系统 → 电源，睡眠选「从不」。也可以打这一行（插电时不睡眠、不休眠）：

```powershell
powercfg /change standby-timeout-ac 0; powercfg /change hibernate-timeout-ac 0
```

萤幕关掉、锁定画面都没关系，但**不要登出 Windows**：登出时 Docker Desktop 会跟着关掉。

**暂停 Windows 更新**：设定 → Windows Update → 暂停更新，选「暂停 1 周」；转文字还没完就再延长。更新自动重开机会把线停掉。

**关掉 Microsoft Store 的自动更新**：WSL 是从 Microsoft Store 装的。Store 自动更新 WSL 时会直接关掉 WSL，Docker 和正在跑的线会跟着停（有人回报要重开机才恢复）；上面暂停 Windows 更新管不到它。打开 Microsoft Store → 右上角的头像 → 设置（Settings）→ 关掉「应用更新」（App updates）。第二阶段跑完再打开。

**C 盘空间**：至少空出 **60 GB**（11-03 下午空 108 GB，够）。这是估计，留了余量：传过来的档约 10 GB，映像约 7.3 GB，建映像的暂存约 6 GB，两个磁碟区约 10～12 GB，再加转出来的东西。Docker 的资料都在 C 盘。

**测 Docker 能不能用显卡**（11-03 已经通过；改过驱动或 WSL 以后再测）：重开机一次、登入，等 Docker Desktop 显示 Engine running，再打：

```powershell
docker run --rm -it --gpus=all nvcr.io/nvidia/k8s/cuda-sample:nbody nbody -gpu -benchmark
```

好的样子：有一行 `> Compute 8.9 CUDA device: [NVIDIA RTX 4000 Ada Generation]`，最后几行是 `… billion interactions per second` 和 `… GFLOP/s …`（11-03 测到约 14,289 GFLOP/s）。这个测试程序比这张卡旧，所以 `MapSMtoCores … is undefined`、`MapSMtoArchName … is undefined. Default to use Ampere`、`GPU Device 0: "Ampere" with compute capability 8.9` 这几行（架构名字也可能是别的）都是正常的，不用管。出错（尤其错误里有 nvidia 或 gpu）：回头看驱动版本、`wsl --update`、WSL 2 那一项。

## 2. 把档从 Mac 搬过来

Mac 上 `mvp-lite-lab-transfer` 资料夹（在 `mvp-lite` 旁边）里的东西，整个复制到这台的 `C:\nknu-transfer\`：两个 tar、两个 `.sha256` 和 `README.md` 放在最上层，`build` 是它里面的资料夹。名字和位置都别改，命令是照这个写的。

| 档 | 大小 | 里面是什么 |
|---|---|---|
| `base.tar` | 约 9.85 GB（档案总管按 1024 算，显示约 9.17 GB） | 不会再变的大东西：教务处导出（转文字的原料）、11-01 的骨架资料库和题库（离线检查拿来对照）、两个检索模型和 Docling 的模型 |
| `update.tar` | 约 130 MB | 程序码、设定、这套工具，和 Mac 已经转好的文字（4,111 份） |
| `base.tar.sha256`、`update.tar.sha256` | 很小 | 两个 tar 的指纹，解开前会自动核对 |
| `build\`：`Dockerfile`、`compose.yaml`、`pyproject.toml`、`uv.lock` | 不到 1 MB | 建映像用 |
| `README.md` | 很小 | 这份说明 |

**只在你自己的两台电脑之间传：U 盘，或 SSH**（SSH 已经设好，Claude 可以从 Mac 直接传过来）。不放云端硬盘（Google Drive、OneDrive 等），不传 GitHub（这个仓库是公开的），不用聊天软件传。原因：导出、骨架资料库和转好的文字里有学生的姓名和学号。

- U 盘要格式化成 **exFAT**：FAT32 放不下 4 GB 以上的档，Mac 又写不了 NTFS。Mac 往 U 盘复制时会多出 `._` 开头的小档（例如 `._base.tar`，放 Mac 的附加资讯），不用管，删掉也可以。
- 从 U 盘**直接**复制到 `C:\nknu-transfer\`。tar、`.sha256` 和以后拿出来的结果都**不要经过「桌面」「文件」「图片」**：Windows 11 常把这三个资料夹自动备份到 OneDrive（就是云端）。`C:\nknu-transfer\` 和 Docker 的磁碟区不会被同步。
- 复制完，U 盘上那份可以删掉。

## 3. 解开、建映像、检查

普通 PowerShell 就行。3.1 和 3.2 互不相干，可以开两个视窗同时跑。

### 3.1 建两个磁碟区，解开两个 tar

```powershell
docker volume create nknu-work; docker volume create nknu-models
```

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm extract
```

它先核对指纹再解开，base.tar 然后 update.tar。要读两遍约 10 GB，估计 10～30 分钟（没量过，看硬盘）。

- 好：每个 tar 都有 `base.tar: OK`（或 `update.tar: OK`）、一排点（每点 100 MB）、`解开了：…`；最后一行 `== 解开好了（EXTRACT OK）`。
- `SHA-256 不符：…`：那个 tar 复制时坏了，从 Mac 重新复制一次。
- `缺 base.tar：…`：档没放在 `C:\nknu-transfer\`。

**update.tar 只解这一次**：开线以后不要再跑 extract（原因在第 5 节最后）。真的打了也不会坏：开过线以后 extract 会拒绝再解 update.tar（`不解 update.tar：这台已经开过转文字线…`），什么都不解。

### 3.2 建映像

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml build
```

要下载约 3.6 GB（大多是 torch 和 CUDA），建好的映像约 7.3 GB。估计 10～45 分钟，看网速。最后没有 `ERROR` 就好了（通常最后一行有 `Built`）。中途断网就再打同一行，已经下载的不会重下。

### 3.3 检查

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm check
```

不到一分钟。每行是 `[通过]`、`[提醒]`（知道就好）或 `[没通过]`（要先解决）。好的样子：

- `[通过] torch 2.15.0+cu132（CUDA 13.2，…）；GPU：NVIDIA RTX 4000 Ada Generation，…；在 GPU 上算了一小段，结果对`
- `[通过] Docling 用 cuda:0：…`
- 最后一行 `== 结果：全部通过。下一步：冒烟测试（…）`

有 `[没通过]`：照那一行说的做，再跑一次 check；看不懂就整屏传给 Claude。

## 4. 冒烟测试

先拿 5 个 Mac 已经转过的档（表格多的 PDF、整份扫描的 PDF、普通 PDF、docx、旧版 doc）在这台重转一次，和 Mac 的结果逐字比对，确认这台转出来的一样。主要的转文字缓存不会被动到。

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm smoke
```

估计 1～3 分钟（Mac 上 89～94 秒，表格那份就占 57～60 秒；这台用 GPU 应该快很多）。超过 15 分钟，或 10 分钟画面都没动：按 Ctrl+C，把画面传给 Claude。

好的样子：

- `设备：Docling cuda:0，TableFormer cuda:0；NVIDIA RTX 4000 Ada Generation…`
- 5 行（2298、2311、2335、2360、2607），每行最后是 `same` 或 `close`
- `RESULT: PASS  files 5/5 match …`，然后 `结论：通过。…`

`-` 开头的行是备注，没关系（2311 底下说两边 LibreOffice 版本不同，是正常的）。再看退出码，应该是 0：

```powershell
$LASTEXITCODE
```

不好的样子：`RESULT: FAIL` 或 `RESULT: CANNOT-COMPARE`、`!` 开头的行、`结论：不通过：…`。这时**先别开转文字线**。

**传给 Claude**：从 `== 冒烟测试` 到最后一行的整屏文字（选取、复制、贴上），加上 `$LASTEXITCODE` 的数字。通过也要传：Claude 要看速度，估计剩下的要转多久。Claude 要细节时再打：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm shell cat research/pipeline_2026-11-02/lab_pc/smoke_out/compare.json
```

冒烟测试可以重跑，每次只删它自己的结果资料夹。

## 5. 开三条转文字线

冒烟测试通过以后再开。

- **A 线**：PDF 和 Office 档；教务处的学报和考题（605 个）排在最后。
- **B 线**：网页和图片。11-08 起这台也开：读网页的规则改了，Mac 转好的教务处网页（约 1,990 页）都要重读一次（几分钟）；新网站的网页和图片也走这条线（图片要认字，比较慢）。B 线不用显卡。
- **C 线**：压缩档，多是教务会议纪录。

三条线都照导出清单（第 10 节）一份一份转，在背景跑，关掉 PowerShell 视窗也照跑。

**第一次开线**（这一整行一起打）：

```powershell
$env:RETRY_FIRST="1"; docker compose -f C:\nknu-transfer\build\compose.yaml up -d lane-a lane-b lane-c; Remove-Item Env:RETRY_FIRST
```

为什么第一次要 `RETRY_FIRST=1`：Mac 的线是硬停的，有几个档转到一半被打断，留下「开始转了」的记号。程序看到这种记号，会当成「这个档会让程序崩溃」而跳过它。线第一次开时会自动跑一个小修正，把这些档（包括压缩档里被打断的附件）放回「还没转」，日志那行是 `先把 Mac 停线时打断的档放回「还没转」（只做一次）`，不用你管；`RETRY_FIRST=1` 是多一层保险：这次开线以前留下记号的档，这次开线都会再转一次（真的让程序崩溃过的也再试一次；几个网站共用的档也只试一次，再崩溃就不再试）。前后两段只是在这个视窗临时设、用完拿掉这个设定。

**看进度**（很快，随时可以打）：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm progress
```

先印三行，是清单里所有导出加起来：`线 A 其余：完成 …，还剩 …`（**这里的「线 A」包括 C 线的压缩档**）、`线 A 学报考题：…`、`线 B 其余：…`。接着一行说读的是哪份导出清单、有几份导出，还有档没转的导出各一行。11-08 换程序以后，`线 B 其余` 会先显示教务处的网页还剩约 1,990 个，B 线重读完就会变成 0。最后一行是 `还没到建库的时候` 或 `可以建第一版库`；看到 `可以建第一版库` 就告诉 Claude。`线 A 其余` 只剩几个、很久都不再变少，也告诉 Claude：让程序崩溃过的档没有结果，progress 一直把它们算成「还剩」。第一次看一份新的导出时要先把它读一遍（教务处那样大的约一分钟），之后就快了。

**看日志**（各看最后 30 行）：

```powershell
docker logs --tail 30 nknu-lane-A
```

```powershell
docker logs --tail 30 nknu-lane-B
```

```powershell
docker logs --tail 30 nknu-lane-C
```

Docker Desktop 视窗的 Containers 页也看得到；日志另外存在磁碟区的 `research/pipeline_2026-11-02/lab_pc/laneA.log`、`laneB.log`、`laneC.log`。开线后 10 分钟左右，把 progress 和三条线日志的最后 30 行传给 Claude，它按实际速度估计要转多久。怎么读：

- 开得好的话，开头有 `torch 2.15.0+cu132（CUDA 13.2），GPU：NVIDIA RTX 4000 Ada Generation`、`[lane A] 开线：路线 pdf,office,office_old；导出 1 份；重转这次开线以前被打断的档（各一次）：是 …`、`[lane A] 导出 1/1：c.nknu.edu.tw_aca（archive-export/aca-20261102）` 和 `[lane A] run 1 start …`（C 线一样，路线是 `archive`；B 线没有 torch 那行，路线是 `page,image,data,unsupported`）。
- 每一轮前面有一行 `导出 第几份/共几份：网站（资料夹）`，说这一轮在转哪个网站。
- `[lane A] run 3 start …`、`[lane A] run 3 exit 4 …`：一轮开始、结束。退出码 4 是跑满 40 分钟、自动换一个新程序（把内存还回去），3 是「看门狗」把卡太久的档结束、自动重开，都正常。139 这类别的数字是某个档让程序崩溃了，线会自动重开、跳过那个档。退出码 0 是这份导出走完了，换下一份。
- 每轮结束前有一行 `转文字：导出里 6012 个档，选了 … 个；这次转了 … 个（ok …）…`。
- `MatchingPostProcessor WARNING`、RapidOCR 的 `WARNING`（例如说没有 CUDAExecutionProvider：文字辨识本来就用 CPU）都是正常的。
- 一条线全部转完：`这条线的档都走过一遍了（导出 N 份…）`，然后 `[lane A] done …`。A 线最后转学报和考题，所以会比较晚结束。线开着时清单里加了网站，它走完原来的会接着转新的（日志有 `清单里多了 N 份导出…`）。

**哪些线还在跑**：

```powershell
docker ps -a --filter name=nknu-lane
```

`Up …` 是在跑；`Exited (0)` 是跑完了；`Exited (130)` 应该是你让它停的、停干净了；别的数字就看日志。

**每天看一次**：打上面这行。Windows 或 WSL 自己更新、停电、重开机以后，线会停，而且不会自己重开。哪条线不是 `Up`、它的日志最后一行也不是 `done`，就照下面「再开线」用带 `RETRY_FIRST=1` 那行重开。docker 命令卡住或报错的话，先把 Docker Desktop 完全退出（右下角图示 → Quit）再打开，还不行就重开机。顺便看 C 盘还剩多少（容器里看到的空间是 Docker 的虚拟硬盘，不是 C 盘的真实空间）：

```powershell
Get-PSDrive C
```

`Free` 那一栏（单位 GB）剩不到 20：先停线（下面），告诉 Claude。

**停线**（应该在 2 分钟内结束：它会先把手上那个档放回「还没转」）：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml stop lane-a lane-b lane-c
```

停干净的话，日志最后两行是 `…按要求停了…` 和 `[lane A] done …`。

**再开线**：线不会自己重开；重开机以后要先登入 Windows、等 Docker Desktop 起来，再自己打。上次是用上面的 stop 停干净的，打：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml up -d lane-a lane-b lane-c
```

已经跑完（`Exited (0)`）的线再开一次也没关系：它会把清单再走一遍，转过的档直接跳过，几分钟就结束。

重开机、停电、Docker Desktop 关掉或当掉以后（日志最后一行不是 `done`），或拿不准的时候，改用「第一次开线」那一行（带 `RETRY_FIRST=1`）。多加了也只是让以前没转成的档各再试一次：真的会让程序卡住的档，每次这样开线最多多花约一小时（看门狗等它 62 分钟）。线开起来会先自己把那时转到一半的档（包括压缩档里的附件）放回「还没转」，三条线都会，日志有一行 `上次这条线是被强制结束的…`；要是接着出现 `放回没做完…`，告诉 Claude。

**两件别做的事**：

- 开线以后**不要再跑 extract**：它会把 update.tar 里 Mac 的旧缓存盖回来，几个压缩档又会被记成失败（extract 会自己拒绝，但别去试）。以后程序要更新，Claude 会另外给你只有程序码的新档（code.tar）和命令。
- **不要删 `nknu-work`、`nknu-models` 这两个磁碟区**，也别在 Docker Desktop 里按 Clean / Purge data 或 Reset to factory defaults：转好的文字只存在 nknu-work 里。

## 6. Mac 不再转了

Mac 上的两条转文字线 11-03 中午就永久停了；它转好的 4,111 份结果都在 update.tar 里，已经带过来。从现在起，剩下的档**只在这台转**，Mac 上不用、也不要再开转文字。这台 nknu-work 磁碟区里的缓存就是唯一还在增加的那一份。转好的结果和第二阶段的产物什么时候、怎么拿回 Mac，Claude 到时候说（一样只走 U 盘或 SSH）。

## 7. 第二阶段：建库和离线检查（Claude 说可以才跑）

日志里的步骤编号就是计划的编号。处理的是导出清单（第 10 节）里的每一个网站：

- 3 记账：列出每个档转得怎样，确保没有档悄悄漏掉（所有网站写在同一份 `totext/summary.md`）；
- 4 组装：把每个网站转好的文字和网页、附件对上，各拼成一个 `corpus/<网站>.json`；
- 5 建库：把所有网站做成同一个 `aca.db`，同时用 bge-m3 模型给每一小段算一组数字（叫「向量」，让用词不同、意思相近的问法也找得到）；用显卡，最久。接着写 `site_report.md`：每个网站一行（各种文件几份、没有文字的网页、没日期的文件、用标题自己搜搜不搜得到），有 ⚠ 的要看一下，但它不挡建库；
- 5b 题库抽样量测（11-22 起）：从新题库 `research/eval_2026-11-18/` 平时那一半钉死抽一份（B 组学生角度的题为主），在学生现在用的库上量正确文件排第几，写 `student_eval.json`。程序是 `eval/student_eval.py`，搜索控制在 5 分钟内，最多跑 600 秒（`STUDENT_EVAL_LIMIT`）。它只是量测，失败或超时只记一笔，不挡换库。题库不进 git，要手放到实验室的 `/work/research/eval_2026-11-18/`（`questions.json`、`agent_queries.json`），缺了这一步就跳过。
- 6 标准出处、7 离线检查：旧的 33 题（只问教务处），11-22 起每晚不跑，加 `--q33` 才跑。
  - 6 把 33 题标准答案的出处对到新库。
  - 7 量标准出处排第几、答案原句有没有送到，和 11-01 的骨架库并排比，每晚约 17 分钟。
- 8 真资料检查：22 项，写在 `checks.md`，每晚照跑，约 4 分钟。

清单里没有教务处的导出时，6～8 不跑；5b 照跑。

估计 40 分钟到 2 小时（没在这台量过；每一步的实际时间会记在日志里）。分两次跑：

学报考题以外都转完（progress 说 `可以建第一版库`；或「线 A 其余」只剩几个让程序崩溃过的档，progress 会一直把它们算成「还剩」，Claude 看过说可以）、Claude 说可以，建**第一版库**（A 线可以继续转学报考题）：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm -e BUILD_OPTS="--first" build
```

学报考题也转完、三条线都结束以后，建**完整的库**：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm build
```

中途失败或停了，日志会写从第几步接着跑（`--from N`），例如第 7 步：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm -e BUILD_OPTS="--from=7" build
```

注意：

- 别和建映像那行搞混：`… compose.yaml build` 是建映像，`… run --rm build` 才是第二阶段。
- 跑的时候别关那个 PowerShell 视窗。要停就按 Ctrl+C：旧的 `aca.db` 不会坏，新库完整建好才会换上。
- 太早跑，它会自己停下，退出码 10（还有档没转）或 11（转文字线可能还在转），什么都不会坏。
- 跑完时有一行 `全部跑完。资料库 …；结果在 research/pipeline_2026-11-02/：…`。最重要的是 `offline_report_r3.md`、`offline_report_r4.md`、`checks.md` 和 `site_report.md`，交给 Claude 看；怎么拿出来 Claude 会说。这些档里一样有学生的姓名和学号，只走 U 盘或 SSH。
- 以前的 `corpus.json`、`assemble_report.json`（只有教务处时的写法）不再更新，留着没关系；现在是 `corpus/` 资料夹里每个网站一个。
- **试建**（先试几个网站、不想盖掉现在的 `aca.db`）：Claude 会给你带 `--db` 的命令，例如 `docker compose -f C:\nknu-transfer\build\compose.yaml run --rm -e BUILD_OPTS="--db research/pipeline_2026-11-02/aca-pilot.db" build`。新库是 `aca-pilot.db`，这次的语料、记账、报告都写在旁边的 `aca-pilot/` 资料夹；现在的 `aca.db` 和它的报告都不会动。试建的库只能直接放在 `research/pipeline_2026-11-02/` 里（和 `aca.db` 放在一起），放别处会被拒绝。
- 真 agent 测试（花 token）不在这里跑，Claude 会另外问你。

进度在画面上，也写在 `research/pipeline_2026-11-02/lab_pc/build.log`；另开一个视窗看最后 30 行：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm shell tail -n 30 research/pipeline_2026-11-02/lab_pc/build.log
```

## 8. SSH（11-03 已经设好）

SSH 是一种加密的远端登入。11-03 下午已经设好：Claude 从 Mac 经 Tailscale（在两台电脑之间搭的加密线）用钥匙登入这台的系统管理员帐号，防火墙只让 Tailscale 的地址（100.64.0.0/10）连进来。所以**这一节不用再做什么**，也请别改 SSH 的设定（尤其别把登入后的 shell 改成 PowerShell：Claude 那边是照 cmd 写的）。

经 SSH，Claude 主要是传档、看进度、看日志、拿结果；第 3～5 节的命令还是你在这台电脑前打（经 SSH 时 Docker Desktop 的登入凭证用不了，Claude 要换一套设定，建映像这类要下载的步骤没在 SSH 上试过）。

以后不想让 Claude 连进来（「以系统管理员身份执行」的 PowerShell）：

```powershell
Stop-Service sshd; Set-Service -Name sshd -StartupType Disabled
```

## 9. 出问题时

| 看到什么 | 怎么办 |
|---|---|
| `docker` 打不出来，或 `error during connect`、`docker_engine` 之类 | Docker Desktop 没开：从开始菜单打开，等它显示 Engine running |
| Docker Desktop 说虚拟化（virtualization）没开 | 进 BIOS 打开 Intel 虚拟化（VT-x），再开机 |
| `external volume "nknu-work" not found` | 先做 3.1 的 `docker volume create` 那行 |
| 错误里有 `nvidia` 或 `gpu`（例如 `could not select device driver "nvidia"`），或 check 说看不到 GPU | 驱动要 580 以上（`nvidia-smi` 看）；`wsl --update`；重开机；再跑第 1 节的显卡测试 |
| 建映像时下载失败、超时 | 再打同一行；一直失败可能是学校网络要设代理，告诉 Claude |
| `SHA-256 不符` | 那个 tar 从 Mac 重新复制 |
| check 说检索模型（bge）不全 | 开线以前：先打下面的「重解 base.tar」，再跑 check；还不行才打「重新下载检索模型」 |
| check 说 Docling 模型不全 | 开线以前：打下面的「重解 base.tar」，再跑 check。Docling 的模型不从网上下载：网上最新版可能和 Mac 用的不一样，冒烟测试会不过 |
| 冒烟测试 `FAIL` 或 `CANNOT-COMPARE` | 先别开线；整屏和 `$LASTEXITCODE` 传给 Claude |
| 线的日志最后一行不是 `done`（重开机、停电、Docker 或 WSL 更新、Docker 关掉过） | 用第 5 节「第一次开线」那行重开（带 `RETRY_FIRST=1`）；日志出现 `放回没做完…` 就告诉 Claude |
| extract 说 `不解 update.tar：这台已经开过转文字线…` | 正常：开过线以后不能再解 update.tar。要更新程序等 Claude 给 code.tar |
| 日志说 `已经有一条 A 线在跑` | 它已经在跑了，不用再开 |
| 日志说 `没有 GPU，没开跑` | 跑 check 看原因 |
| 日志里有 `CUDA out of memory`（显卡内存不够） | 打下面的「只停 C 线」，告诉 Claude |
| 三条线都 `done` 了，progress 还剩几个 | 那几个是让程序崩溃过的档，告诉 Claude |
| 线、progress 或第二阶段说 `导出清单不能用：…` | 清单里有一行不对（资料夹不在、没有 `manifest.json`、同一个网站放了两份）：什么都没坏，把那一行传给 Claude，它改清单 |
| 第二阶段说 `清单里没有教务处的导出…` | 正常：题库只问教务处，这次只建库和写 `site_report.md` |
| 第二阶段退出码 10 或 11 | 还没到建库的时候，什么都没坏 |
| C 盘快满了（`Get-PSDrive C` 的 Free 不到 20） | 先停线、告诉 Claude；映像建好以后可以打下面的「清建映像的暂存」（约 6 GB，问你时打 y；空间不一定马上还给 C 盘）；不要删磁碟区 |
| 看不懂 | 整屏文字复制给 Claude |

重解 base.tar（只在开线以前用；它只解 base.tar，不碰 update.tar）：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm extract base.tar
```

重新下载检索模型（只下载 bge-m3 和 bge-reranker-v2-m3 两个、照固定版本，要连网，约 4.6 GB；只在开线以前用，下载完再跑 check 和冒烟测试）：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm models-download
```

只停 C 线：

```powershell
docker compose -f C:\nknu-transfer\build\compose.yaml stop lane-c
```

清建映像的暂存：

```powershell
docker builder prune
```

## 10. 多个网站：导出清单（11-08 起）

**是什么**：一个文字档，告诉三条转文字线、progress 和第二阶段要处理哪些网站的导出。它在磁碟区里：`research/pipeline_2026-11-02/exports.txt`。一行一个导出资料夹（从 `/work` 算起，例如 `archive-export/aca-20261102`），`#` 开头的行是注解，空行不算。**没有这个档**，就跟以前一样只处理教务处的 `archive-export/aca-20261102`。这个档由 Claude 经 SSH 写，你不用自己改。

**怎么运作**：

- 三条线照清单的顺序一份一份转。所有网站共用一个转文字缓存（按档的内容存）：同一个档在几个网站的导出里都有，也只转一次。
- 线走完清单会再读一次：线开着时加进清单的网站，会接着转；没有新的才结束。
- 每份导出第一次被看时，程序先把它的档读一遍、记下每个档走哪条线（存在 `research/pipeline_2026-11-02/totext_jobs/<网站>.json`，导出换了会自己重做），之后 progress 和建库前的检查就不用再读；第二阶段的记账和组装每次建库还会把每个档读一遍（导出越多越久）。
- 第二阶段把每个网站组装成 `corpus/<网站>.json`（网站名里的 `/` 换成 `_`，例如 `c.nknu.edu.tw_aca`），再把所有网站建进同一个 `aca.db`，另外写 `site_report.md`（第 7 节）。
- 一个网站只能放一份导出：同一个网站的新导出，要换掉清单里旧的那一行。

**清单写错**：线、progress、第二阶段都会停下来，说哪一行有问题（资料夹不在、没有 `manifest.json`、同一个网站放了两份）。什么都不会坏，告诉 Claude 改清单就好。

**另一份清单**：试几个网站时，Claude 可能另写一份清单（例如 `exports-pilot.txt`），命令前面会加 `$env:EXPORTS="research/pipeline_2026-11-02/exports-pilot.txt";`（线和 progress），或在 `run --rm` 后面加 `-e EXPORTS=…`（第二阶段）。照 Claude 给的整行打就好。

## 11. 每晚进库（11-16 起）

爬虫每晚抓完、导出以后，会自己叫 `C:\nknu-transfer\build\nightly_ingest.cmd`，把新资料转文字、重建、换库。平常不用管。
想知道昨晚怎样，在 PowerShell 打（一行）：

```
docker compose -f C:\nknu-transfer\build\compose.yaml run --rm ingest status
```

- 「换上了新库」「没有新资料，没重建」都是正常的。
- 「把关没过，没换库」：新库留在旁边没换上，正式库还是昨天那份。有两种原因，`status` 会写是哪一种：
  - 某个网站的文件数掉到原来的一半以下；
  - 11-17 起：说不出原因就离开库的文件太多（全库超过 30 份，或一站超过 15 份）。换库前程序会把「正式库有、新库没有」的
    每一份文件都对一次原因：并进另一份、学校自己删掉或转走的，算说得通；导出那一行没有档了、整行不见了、有档却没读进来、
    读不到字、内容被掏空，算说不通。`status` 会列出是哪几份、在哪个网站。
  把这几行给 Claude 看。第二晚会自动再建一次，上游修好了就自己过。
- 「失败，库没动」：还是用昨天的库，把这一行给 Claude 看。11-17 起会写原因：C 盘不到 15 GB（不建库）、建库超过 3 小时（停掉）。
- 想先停几晚不换库：在 `/work/research/pipeline_2026-11-02/` 放一个叫 `ingest.pause` 的档（请 Claude 放）；拿掉就恢复。
- 换上以后发现不对、要退回前一份库：`docker compose -f C:\nknu-transfer\build\compose.yaml run --rm ingest rollback`，
  再 `docker compose -f C:\nknu-transfer\inspect\compose.yaml restart datasette`。退回以后会自动暂停每晚换库。

11-19 起多了几种情况，`status` 都会写：

- 「转文字到点还没转完、这一晚没换新资料的网站」：有网站的大档两小时内转不完。只有这几站这一晚留在原来那份资料，别的网站照常换库；下一晚接着转。
- 「把关退回、这一晚没换新资料的网站」：把关拦下新库，问题出在这几站。它们留在原来那份资料，别的网站重建一次、照常换库。被退回的那份导出记在 `/work/research/pipeline_2026-11-02/ingest-refused.txt`，之后不再重试，等爬虫给这一站新的导出；看过觉得没问题，删掉那一行就会重试。
- 想让某个网站先不换新资料：在 `/work/research/pipeline_2026-11-02/ingest-hold.txt` 写一行网站键（请 Claude 写）；拿掉那一行就恢复。
- 白天手动进一次：`docker compose -f C:\nknu-transfer\build\compose.yaml run --rm ingest run`。要先试建、检查过再换：放 `ingest.pause`，建成 `--db research/pipeline_2026-11-02/<名字>.db` 的试建库，检查过用 `... run --rm ingest promote <名字>` 直接换上（Claude 做）。

## 12. 换程序（Claude 做）

在 Mac 上，仓库里：

```
deploy/lab-pipeline/pack.sh /tmp/nknu-lab-pipeline            # 只带这条线的程序和测试
deploy/lab-pipeline/pack.sh /tmp/nknu-lab-pipeline --with-src # 另带建库读的 src/nknu/content、totext、retrieval、core 和 config/
scp /tmp/nknu-lab-pipeline/lab-pipeline.tar /tmp/nknu-lab-pipeline/lab-pipeline.tar.sha256 nknu-lab:C:/nknu-transfer/out/
scp /tmp/nknu-lab-pipeline/install.sh nknu-lab:C:/nknu-transfer/tools/lab_pipeline_install.sh
```

在实验室电脑上（没有转文字、建库、进库在跑的时候；避开 01:00～04:00）：

```
docker run --rm -v nknu-work:/work -v C:\nknu-transfer\tools:/tools:ro -v C:\nknu-transfer\out:/out -w /work -e NKNU_CONFIG_DIR=/work/config -e PYTHONPATH=/work/src nknu-pipeline bash /tools/lab_pipeline_install.sh
```

它会核对包的 SHA-256、确认没有东西在跑、把被换掉的档存成 `/work/previous/lab-pipeline-<时间>.tar`、解开、跑这条线的测试。

11-22 晚上换过一次，包是这样打的：
- 底子是 git 里已提交的 `deploy/lab-pipeline` 和 `tests/pipeline`。
- 再放上这次的改动（同一晚提交在 `78049e0`）：`build.sh` 的 5b 和 `--q33`、`nightly_ingest.py` 的 `student_eval`、新档 `eval/student_eval.py`、两个测试档、本说明。
- 工作区里内容那边「删掉不再用的开关」（`build.sh` 第 9 步、`offline_eval.py`、`check_realdata.py`、`hand/lab_trial_checks.sh`）不在里面，那几个档用的都是已提交的版本。
- 装完的情形：34 个档，实验室上测试 115 个全过，被换掉的档在 `/work/previous/lab-pipeline-20261122-220205.tar`。
- 模拟跑一次，步骤是 3、4、5、5b、8。实际量一次抽样，用了 226 秒。
- 约 22:11 又装了一次，只多了一个改动：手动 `promote` 时，状态也记下试建那次的抽样量测。实验室上测试 117 个全过。这一次被换掉的是上一次装的版本，存在 `/work/previous/lab-pipeline-20261122-221139.tar`；要退回今晚改之前，用 `20261122-220205` 那一份。

所以在 Mac 上直接跑 `pack.sh` 会把工作区里那些「先不要」的改动一起打进去；那些改动提交或丢掉以前，要照上面这样另外打包。`compose.yaml` 或 `nightly_ingest.cmd` 有改的话，另外 scp 到 `C:\nknu-transfer\build\`（旧的先改名留着）。装完跑一次 `... run --rm ingest status` 和 `... run --rm progress` 确认还读得到。

## 13. 这台电脑坏了，在另一台重架

只靠仓库重架的是程序；资料要另外有：爬虫的存档和导出（爬虫那边的说明 `deploy/lab-crawler/`）、转文字缓存和库（没有备份就从导出重转、重建，转文字要几天）。

1. Windows 准备、Docker、显卡：第 1 节。
2. 把仓库放进磁碟区 `nknu-work` 当 `/work`（`git clone` 到 Mac 再照第 12 节带 `--with-src` 打包送过去，或直接在容器里 clone）；`pyproject.toml`、`uv.lock`、`deploy/lab-pipeline/Dockerfile`、`compose.yaml`、`nightly_ingest.cmd` 放到 `C:\nknu-transfer\build\`。
3. 建映像、下载两个模型到 `nknu-models`：第 3 节（`models-download` 服务）。
4. 资料：`/work/archive-export/sites/`（爬虫的导出和 `current.tsv`）；`/work/research/pipeline_2026-11-02/exports.txt`（一行一个导出资料夹；可从 `current.tsv` 生成）；`/work/research/eval_2026-11-01/`（题库 `core20.json`、`noanswer22.json`、`gold_docs.json`、`runs/`）和 `/work/research/skeleton_2026-11-01/aca.db`（离线检查的基准库）来自备份；没有它们，建库加 `--from 3` 只做到第 5 步也能出库（第 6 步以后的检查会跳过或失败，不影响换库）。
5. `... run --rm check` 看缺什么；`... up -d lane-a lane-b lane-c` 转文字；转完 `... run --rm build` 建第一份库。
6. 每晚：Windows 工作排程器跑爬虫的每晚脚本，它最后叫 `C:\nknu-transfer\build\nightly_ingest.cmd`。
7. 面板：`deploy/lab-inspect/README.md`（资料总览 11-21 起是检视台的一页，没有自己的容器了；11-22 起叫「內容」，在 /sites）。


## 14. 手动操作的脚本（`hand/`，Claude 在 Mac 上用）

都经 `ssh nknu-lab` 操作实验室电脑；实验室那一半的小脚本在 `/work/deploy/lab-pipeline/hand/`，不用另外放到 `C:\nknu-transfer\tools`。

| 脚本 | 做什么 |
|---|---|
| `hand/ingest_now.sh` | 白天手动进一次库，照每晚的把关；库一换就重启 Datasette |
| `hand/trial.sh 名字 [上一份试建的名字]` | 先试建、对 33 题和真资料检查，不动正式库；结果抓回 `research/pipeline_2026-11-02/trials/<名字>/` |
| `hand/promote.sh 名字` | 检查过的试建库直接换成正式库，重启 Datasette，拿掉暂停记号 |
| `hand/reports_later.sh 名字` | 换完以后，等试建那次建库的收尾跑完，把报告补搬到正式库旁边 |
| `hand/hold_site.sh hold 网站键 "说明"`、`let-go 网站键` | 让一个网站先留在原来那份导出、或再放开 |
| `hand/lab_export_vs_library.py` | 一份新导出进库以前，对着正式库看这一站的每份文件还在不在（在实验室的映像里跑） |
| `hand/lab_state.sh` | 看库、暂停记号、把关结果（别的脚本叫它） |

过的标准（11-18 起用的）：33 题每一格最多比上一份少 1 题；真资料检查不比正式库少过；逐份核对没拦。11-22 起每晚建库不再量 33 题（改量题库抽样，第 7 节 5b）。手动试建的 `hand/trial.sh` 是自己调 `lab_trial_checks.sh` 量 33 题，这次没改，过的标准照旧。

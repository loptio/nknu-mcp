# 爬虫在实验室电脑上：每晚跑什么、怎么装、怎么重建

爬虫只在实验室电脑上跑（Windows＋Docker，从 Mac 用 `ssh nknu-lab` 连）。这个资料夹放的是**会一再跑、或者机器坏了重建时要用**的东西。一次性的调查脚本、每次安装的经过留在 `research/`（不进 git）。

现在的全貌看 `docs/crawler-wrapup.md`；逐层的讲解看 `docs/crawler-walkthrough.md`；连实验室的窍门看 `docs/lessons.md`「实验室电脑」那一节。

## 这里有什么，各放在实验室的哪里

| 仓库里 | 实验室电脑上 | 做什么 |
|---|---|---|
| `compose.yaml` | `C:\nknu-crawl\compose.yaml` | 六个服务：`nightly`（每晚的定期更新，手动跑脚本也用它）、`coverage`（每晚各网站的抓取覆盖数字，给面板）、`warc-backup`（原档的每晚备份）、`stop`、`status`、`rejudge`（手动工具）。没有会听端口的服务 |
| `nightly_update.cmd` | `C:\nknu-crawl\nightly_update.cmd` | 排程跑的那一个档：先跑 `nightly`，再叫内容那边的每晚进库（`C:\nknu-transfer\build\nightly_ingest.cmd`），然后跑 `coverage` 记这一晚各网站的抓取覆盖数字，最后跑 `warc-backup` 把原档拷一份到备份的硬盘，各记一行到 `nightly-task.log` |
| `nightly_hidden.vbs` | `C:\nknu-crawl\nightly_hidden.vbs` | 不开视窗地跑上面那个档（有人把视窗关掉，那一晚就断了） |
| `lab/nightly_update.sh` | `/crawl/code-update/lab/` | 每晚的步骤：备份账本 → 放回今晚要看的 → 课表学期 → 抓取 → 复核 → 开浏览器的 5 站 → 导出有变的站 → 写 `seen.tsv` |
| `lab/export_sites.sh` | `/crawl/code-update/lab/` | 导出一批网站（每晚叫它；白天手动导也用它） |
| `lab/js_sites.txt` | `/crawl/code-update/lab/` | 每晚要开浏览器的网站 |
| `browser_image/Dockerfile` | `C:\nknu-transfer\browser\Dockerfile` | 映像 `nknu-browser`：`nknu-pipeline` 加 Chrome，`nightly` 用它 |
| `pack.sh` | 在 Mac 上跑 | 打包 |
| `install.sh`、`check_lab.sh`、`proof_night.sh` | 送到 `C:\nknu-crawl\in\deploy\` 再跑 | 安装、核对「实验室上的和仓库里的是不是同一份」、空跑证明每晚那条路是通的 |
| `export_and_compare.sh`、`compare_export.py` | 同上 | 导出几个网站，再把每站现用的资料夹对着正式库用的那一份逐行比；交导出给内容以前跑 |

爬虫的程序本身（`src/nknu/archive/`、`src/nknu/admin/cli.py`、名单 `config/archive_round1.json`）在实验室上有两份，各给一种用途，换程序时两份一起换：

- `/crawl/code-update`：每晚的更新和手动跑的脚本用。
- `/crawl/export-code`：白天单独导出时用（`-e PYTHONPATH=/crawl/export-code/src`）。

看进度、看存下来的网页用检视台（`deploy/lab-inspect/`），它自己读账本和原档。爬虫的旧看板（`viewer` 服务、8877 端口、它自己那一份程序）11-21 收掉了；`src/nknu/archive/viewer.py` 还留在程序里，因为「缺内容区的网页放回去重抓」那一步（`batch.no_area_back`）用它读存档记录。

不在这里、也不进 git 的：账本 `/crawl/archive/frontier.db`、抓下来的原档 `E:\nknu\warc`（11-23 从 `D:\nknu-crawl\warc` 搬来；在容器里是 `/crawl/archive/warc`，账本记的是这底下的相对路径）、账本每晚的备份 `D:\nknu-backup\crawler-nightly`（`nightly-*.db`，留最新 7 份；动账本以前手动做的备份在 `D:\nknu-backup\crawler-before-changes`，11-23 以前这些都在 `D:\nknu-crawl\backup` 底下）、Google 金钥 `C:\nknu-crawl\secrets\google.env`（绝不显示、不印出）。

原档每晚拷一份到另一颗硬盘 `D:\nknu-backup\crawler-warc`（下面「原档的每晚备份」）。原档的位置写在 `compose.yaml` 的四个服务（`status`、`rejudge`、`warc-backup`、`nightly`）和检视台的 `deploy/lab-inspect/compose.yaml` 里，搬位置时这五处一起改。挂载的路径写错时 Docker 不会报错，会自己建一个空资料夹；所以每晚脚本开工前、还有空跑，都会对着账本查一次（`update warc-there`，只读）：账本记的最新一个档不在，就当成挂错了资料夹，这一晚停在 preflight、什么都不动；只少旧的档会在 `update.log` 记一行、照常跑。

## 每晚的排程

Windows 工作排程器的工作 `nknu-nightly-update`：每天 01:00，动作是

```text
wscript.exe //B //Nologo C:\nknu-crawl\nightly_hidden.vbs
```

「只在使用者登入时执行」（Docker Desktop 要有人登入才在），72 小时没结束才强制停。重建时照这样建：

```text
schtasks /create /tn nknu-nightly-update /sc daily /st 01:00 /it /tr "wscript.exe //B //Nologo C:\nknu-crawl\nightly_hidden.vbs"
```

每晚 01:00～04:00 在跑，这段时间不装程序、不重启东西。那一晚的经过在存档的 `update.log`，结果在 `reports/update-status.json`；排程自己只在 `C:\nknu-crawl\nightly-task.log` 记开始和结束。想停一晚：在 `/crawl/archive` 放一个 `update.pause`。

## 每晚各网站的抓取覆盖数字

面板要画「抓到 ÷ 该抓」的每晚走势，还要显示某一站在某一晚抓到多少、没抓到的各有多少。它原来是从每晚的账本备份算的，但面板的容器读不到备份，备份也只留 7 份。所以排程在内容的进库回来以后跑 `coverage` 服务，把这一晚的数字记下来。程序是 `src/nknu/archive/coverage.py`。

- 写在 `/crawl/archive/reports/coverage.jsonl`，一晚一行，照日期排；同一晚重算会换掉那一行。面板的容器只读挂着这个磁碟区，直接读。
- 算法和面板自己的一样（`nknu.ops.pipeline.crawl_buckets`）：账本的 `url_result` 按网站和结果数，用 `outcome.user_bucket` 归类；第二轮开着的批次（`round2_promoted` 的行和开批以后新增的行）分开放。爬虫在实验室的那份程序里没有 `nknu.ops`，所以这段查询在爬虫这边也写了一份：面板那边改算法，这边要跟着改。
- 一行的栏位：`night`（开跑那天；这一行是那一次跑完以后账本的样子）、`ledger`（`live`，或补历史时用的备份档名）、`written_at`、`round2_open`、`sites`（每个网站键底下 `buckets` 各类几个、`review` 待确认的按结果码各几个、第二轮开着的网站另有 `batch`）。不写全校总数：哪些网站算在内（正式库有的网站）是面板定的。
- 只读账本，不连网，不改排程的退出码；失败了只是那一晚少一行，手动补：

```text
docker compose -f C:\nknu-crawl\compose.yaml run --rm coverage
docker compose -f C:\nknu-crawl\compose.yaml run --rm -v D:/nknu-backup/crawler-nightly:/b:ro coverage --db /b/nightly-<年月日>-<时分秒>.db
```

第一行照现在的账本补最近这一晚；第二行从一份每晚备份补（备份是那一天开跑前的账本，所以算成前一天那一行）。白天手动动过账本的日子，变动会算在当天那一行里。

## 原档的每晚备份

E 槽的原档是唯一完整的一份，里面有些网页学校已经删了、没办法重抓。所以排程每晚最后（内容的进库回来以后）跑 `warc-backup` 服务，把原档拷一份到另一颗硬盘 `D:\nknu-backup\crawler-warc`。程序是 `src/nknu/archive/warcbackup.py`。

- 只加不删：原档那边少了的档，备份里照留。备份里有、但大小和原档不一样的档不覆盖，旧的改名留成 `<档名>.old-<时间>`，再拷原档那份。
- 原档唯读挂进去，程序出错也写不了 E 槽。
- 每个档先写成 `<档名>.part`，读回来 SHA-256 和原档一样才改成正名；拷的时候原档还在变（还在写）的留到下一次。
- 每次都比整棵资料夹（档名和大小），所以漏了一晚或几晚，下一次自己补齐。
- 有抓取拿着 `run.lock` 就不拷。备份的资料夹是空的也不拷：挂载的路径写错时 Docker 会建一个空资料夹，会在那里再拷出一整份；真的是第一次（或换了一颗备份硬盘）才加 `--first`。
- 它排在进库之后，出什么事都拦不到进库，也不改排程的退出码。最多跑 7 小时：备份的硬盘卡住时，不会拖到下一晚的排程开不了。

早上看结果：

```text
docker run --rm --network none -v nknu-crawl:/crawl:ro nknu-pipeline cat /crawl/archive/reports/warc-backup.json
```

- `ok` 是 `true`：备份里有原档的每一个档。`false`：`behind` 是落后几个档，`why` 和 `message` 说原因（`run_in_progress` 有抓取在跑；`copy_failed` 有档没拷成，`failed` 列出是哪些；`backup_folder_empty`、`source_empty` 是资料夹空的，多半挂错了）。
- `state` 还是 `running`、`started_at` 又是好几个小时以前：那一次被砍掉了。
- `ended_at` 不是今天：这一步昨晚没跑，看 `C:\nknu-crawl\nightly-task.log` 的 `warc backup` 两行。
- `only_in_backup` 不是 0：有档备份里有、原档那边没有了，要查 E 槽是不是少了档。

每一次一行的纪录在旁边的 `warc-backup.log`。落后不用手动补，下一晚会自己补；想马上补、或只看要拷几个（什么都不拷、不写）：

```text
docker compose -f C:\nknu-crawl\compose.yaml run --rm warc-backup
docker compose -f C:\nknu-crawl\compose.yaml run --rm warc-backup --check
```

手动跑照样避开 01:00～04:00。E 槽坏了要用备份时：把 `D:\nknu-backup\crawler-warc` 整个拷回原档的位置，档名带 `.old-` 的和 `.part` 结尾的不用拷。

## 换程序

1. 在 Mac 上打包、送过去（只经 SSH）：

   ```bash
   deploy/lab-crawler/pack.sh
   ```

   它印出接着要打的 `scp` 那一行。小包只带爬虫自己的档，别的 session 改到一半的不会跟过去。
2. 在实验室电脑上（经 SSH 时每行前面加 `set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" &`）：

   ```text
   docker run --rm --network none -e TZ=Asia/Taipei -v nknu-crawl:/crawl -v C:/nknu-crawl/in/deploy:/in:ro nknu-pipeline bash /in/install.sh
   ```

   `install.sh` 只在 04:00～23:00 做，有抓取在跑就不做；换掉的档都留成 `<档名>.prev-<时间>`，退回就是把它们改回原名。
3. 主机上的三个档有改的话，各自先留一份再换（例：`copy C:\nknu-crawl\compose.yaml C:\nknu-crawl\compose.yaml.prev-<日期>`，再 `copy C:\nknu-crawl\in\deploy\compose.yaml C:\nknu-crawl\compose.yaml`）。
4. 核对和空跑：

   ```text
   docker run --rm --network none -v nknu-crawl:/crawl:ro -v nknu-work:/work:ro -v C:/nknu-crawl:/host:ro -v C:/nknu-crawl/in/deploy:/in:ro nknu-pipeline bash /in/check_lab.sh
   docker compose -f C:\nknu-crawl\compose.yaml run --rm -v C:/nknu-crawl/in/deploy:/in:ro nightly bash /in/proof_night.sh
   docker compose -f C:\nknu-crawl\compose.yaml run --rm warc-backup --check
   ```

   第一行要说「不一样的 0 个」，它还会说内容那边读的那份名单和这一包的一不一样（不算进个数；不一样时该不该换，看下面「和内容那条线接的地方」）；第二行不抓网页、不导出，只证明排程那条路是通的，其中「账本记的存档档 … 个都在」那一行证明原档的资料夹挂对了，「备份的资料夹能写（7 份）」那一行证明备份的资料夹挂对了（份数是 0 就是挂到了一个新建的空资料夹）；第三行什么都不拷，只证明原档备份那个服务的两处挂载是对的：要说「备份里都有」或「要拷」几个，说资料夹是空的就是挂错了。

改了会影响导出内容的程序，装之前先在账本的副本上让新旧程序各导一遍、逐行比（做法和脚本的样子在 `research/crawler_design_2026-11-19/README.md` 9.10）。

## 把导出交给内容以前

```text
docker compose -f C:\nknu-crawl\compose.yaml run --rm -v C:/nknu-crawl/in/deploy:/in:ro -e "SITES=<网站键 网站键 …>" nightly bash /in/export_and_compare.sh
```

它导出这几站（没变动的会跳过），再把每站现用的资料夹对着内容那边 `exports-official.txt` 里的那一份比。看三样，输出一起交给内容：

- **消失的行**：原来有档的行消失了，要说得出原因。
- **有档变没档的行**：每一行都要说得出原因（定了不收的会带着它的原因代码）。
- **网页里的图配得上档的张数**：内容是照网页档里 `<img>` 的网址去清单找图的。行都在、档也在，但图片那一行的网址和网页里写的不一样，图里的字就贴不回网页（11-19 协作平台的站出过这个问题）。配得上的变少的页会逐个列出来，应该是 0 页。

## 手动跑

都用 `nightly` 这个服务（它带着程序、锁、金钥和要挂的资料夹），脚本放在 `C:\nknu-crawl\in\<资料夹>\`：

```text
docker compose -f C:\nknu-crawl\compose.yaml run --rm -v C:/nknu-crawl/in/<资料夹>:/in:ro nightly bash /in/<脚本>.sh
```

- 只读地看账本，用不连网的容器：`docker run --rm --network none -v nknu-crawl:/crawl:ro … nknu-pipeline python /in/<脚本>.py`，账本用 `mode=ro` 打开。
- 同一时间只有一个程序对学校发请求（全机锁）；每台服务器一条连线，间隔 1～2 秒。
- 礼貌、范围、robots 有没有违规，每晚不查，要看就手动跑（不连网，别在 01:00～04:00）：`docker compose -f C:\nknu-crawl\compose.yaml run --rm status`。它把结果写到 `reports/problems-latest.log`，有硬违规时退出码是 2。改了抓取间隔、排程，或让两个程序碰同一台服务器以后值得跑一次。
- 经 SSH 到 cmd 的命令里别写 `|`、`>NUL` 和成对的引号套引号：写成脚本档再跑。

## 机器坏了，从头重建

1. 装 Docker Desktop；建两个具名磁碟区 `nknu-crawl`、`nknu-work`（后一个是内容那边的，照 `deploy/lab-pipeline/`）。
2. 做映像：`nknu-pipeline` 照 `deploy/lab-pipeline/`；`nknu-browser` 把 `browser_image/Dockerfile` 放到 `C:\nknu-transfer\browser\`，`docker build -t nknu-browser C:\nknu-transfer\browser`。
3. 放资料：把备份的账本（`D:\nknu-backup\crawler-nightly` 里最新的一份 `nightly-*.db`）放回 `/crawl/archive/frontier.db`，原档从 `D:\nknu-backup\crawler-warc` 拷回 `E:\nknu\warc`（资料夹里直接是 `2026\` 这样的年份资料夹），金钥档放到 `C:\nknu-crawl\secrets\google.env`。没有备份就只能重爬。
4. 放程序：`deploy/lab-crawler/pack.sh --full`（照 git 里已提交的那一版取整个程序），送过去，`install.sh` 加 `-e ANY_HOUR=1`；三个主机档 copy 到 `C:\nknu-crawl\`。
5. 建排程（上面那一行），跑 `check_lab.sh` 和 `proof_night.sh`。
6. 第一晚之后看 `update.log` 和检视台的「每晚運行」。

## 和内容那条线接的地方

- 网站名单 `config/archive_round1.json` 在实验室上有第三份，是内容那边的：`/work/config/archive_round1.json`。转文字和建库从它读「第一轮有哪些网站」（判断一个网页是不是学校自己的），面板读站名、首页、imp、第几轮。名单动了第一轮有哪些网站，或站名、首页、imp、platform、why，就告诉内容那边把那份也换掉（它的做法是先把旧档存进 `/work/previous/` 再换）；只动爬虫自己的栏位（caps、revisit、skip）不用管。`check_lab.sh` 每次都会说那一份和这一包的一不一样。
- `nightly_update.cmd` 在抓取之后叫 `C:\nknu-transfer\build\nightly_ingest.cmd`。那个档搬了位置，这里要跟着改一行。
- `export_sites.sh` 把导出写到 `/work/archive-export/sites/`，每站留现用的和上一份；内容那边 `/work/research/pipeline_2026-11-02/` 里 `exports*.txt` 写着的资料夹一律不删。那几份清单搬了位置，要改这支脚本开头的 `EXPORT_LISTS`。那个资料夹里一份 `exports*.txt` 都找不到时（搬走了、改了名），更旧的导出一个都不删，`export.log` 会写「找不到内容那边的导出清单」：看到这一句就是 `EXPORT_LISTS` 指错了地方，旧导出会一直攒着，要去改。
- 每晚最后写 `/work/archive-export/sites/seen.tsv` 和 `current.tsv`，内容的每晚进库读它们。
- 内容读每个导出资料夹的 `manifest.json`。图片行有三栏它靠着用，写法不能随便改：「网址」（它拿网页里 `<img>` 的网址来对，所以协作平台的图要写成网页档现在写的那个网址）、「本地档案」（几行可以共用一个档）、「链到它的网页」（它的换库把关靠这一栏认「这张图的字并进了哪一页」，要一直写显示这张图的那几页）。改这三栏以前先跟内容对好。

## 和面板接的地方

检视台（程序在 `src/nknu/ops/`，装法在 `deploy/lab-inspect/`）不经爬虫的任何服务，直接用下面这些。改名、改栏位、改措辞多半不会报错，只会让面板上的数字变空，所以改以前先跟面板那边说，改完跑 `tests/ops`（日志的措辞测试盖不到）。

- **函数**：`progress.connect`（只读开账本）、`postback.read_response(root, warc, offset)`（读一笔存下来的记录）、`outcome`、`profiles`、`scope`、`links`（连私有的 `links._base`）、`google_api`、`export.redecode`。
- **账本**：视图 `url_result`、`action_result`；表 `url`、`link`、`action`、`page_res`、`drive_item`、`site_plan`、`round2_promoted`、`event`（认 `run_start`、`run_end`、`jsfetch_start`、`jsfetch_end`，后两个还读讯息里各站的数字）、`meta`（键 `round2:open`）。
- **状态档**（都在存档的 `reports/`；另有 `warc-backup.json` 是原档每晚备份的结果、`coverage.jsonl` 是每晚各网站的抓取覆盖数字，栏位见上面各自那一节，改栏位以前先跟面板说）：`update-status.json`（`night_started`、`counts`、`sites_changed`、`revisit_tonight`、`revisit_idle`、`put_back.revisit`）、`schedule-term.json`、`export-owed.json`；还有存档根目录的 `update.pause`。
- **`update.log` 里的原话**（`lab/nightly_update.sh` 写的，面板靠它们切出每晚的各个时段）：「定期更新开始」「有 update.pause」「上一晚的定期更新还在跑」「备份好了」「放回好了」「run 结束」「变动清单算好了」「run 没有好好结束」「停在：」「导出好了」「今晚没有要导出的网站」「定期更新结束」「JS 网站开始」「JS 网站结束」。
- **导出资料夹**（`/work/archive-export/sites/`）：`current.tsv`、`failed.tsv`、`export.log`，每站的 `manifest.csv`（「存档编号」的 `u<编号>`、`a<编号>`，「结果」栏的「抓到了」，「缺什么」栏的用语）。

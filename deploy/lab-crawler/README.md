# 爬虫在实验室电脑上：每晚跑什么、怎么装、怎么重建

爬虫只在实验室电脑上跑（Windows＋Docker，从 Mac 用 `ssh nknu-lab` 连）。这个资料夹放的是**会一再跑、或者机器坏了重建时要用**的东西。一次性的调查脚本、每次安装的经过留在 `research/`（不进 git）。

现在的全貌看 `docs/crawler-wrapup.md`；逐层的讲解看 `docs/crawler-walkthrough.md`；连实验室的窍门看 `docs/lessons.md`「实验室电脑」那一节。

## 这里有什么，各放在实验室的哪里

| 仓库里 | 实验室电脑上 | 做什么 |
|---|---|---|
| `compose.yaml` | `C:\nknu-crawl\compose.yaml` | 五个服务：`nightly`（每晚的定期更新，手动跑脚本也用它）、`viewer`（旧看板）、`stop`、`status`、`rejudge`（手动工具） |
| `nightly_update.cmd` | `C:\nknu-crawl\nightly_update.cmd` | 排程跑的那一个档：先跑 `nightly`，再叫内容那边的每晚进库（`C:\nknu-transfer\build\nightly_ingest.cmd`），各记一行到 `nightly-task.log` |
| `nightly_hidden.vbs` | `C:\nknu-crawl\nightly_hidden.vbs` | 不开视窗地跑上面那个档（有人把视窗关掉，那一晚就断了） |
| `lab/nightly_update.sh` | `/crawl/code-update/lab/` | 每晚的步骤：备份账本 → 放回今晚要看的 → 课表学期 → 抓取 → 复核 → 开浏览器的 5 站 → 导出有变的站 → 写 `seen.tsv` |
| `lab/export_sites.sh` | `/crawl/code-update/lab/` | 导出一批网站（每晚叫它；白天手动导也用它） |
| `lab/js_sites.txt` | `/crawl/code-update/lab/` | 每晚要开浏览器的网站 |
| `browser_image/Dockerfile` | `C:\nknu-transfer\browser\Dockerfile` | 映像 `nknu-browser`：`nknu-pipeline` 加 Chrome，`nightly` 用它 |
| `pack.sh` | 在 Mac 上跑 | 打包 |
| `install.sh`、`check_lab.sh`、`proof_night.sh` | 送到 `C:\nknu-crawl\in\deploy\` 再跑 | 安装、核对「实验室上的和仓库里的是不是同一份」、空跑证明每晚那条路是通的 |
| `export_and_compare.sh`、`compare_export.py` | 同上 | 导出几个网站，再把每站现用的资料夹对着正式库用的那一份逐行比；交导出给内容以前跑 |

爬虫的程序本身（`src/nknu/archive/`、`src/nknu/admin/cli.py`、名单 `config/archive_round1.json`）在实验室上有三份，各给一种用途，换程序时三份一起换：

- `/crawl/code-update`：每晚的更新和手动跑的脚本用。
- `/crawl/export-code`：白天单独导出时用（`-e PYTHONPATH=/crawl/export-code/src`）。
- `/crawl/viewer-code`：旧看板用。

不在这里、也不进 git 的：账本 `/crawl/archive/frontier.db`、抓下来的原档 `D:\nknu-crawl\warc`、账本每晚的备份 `D:\nknu-crawl\backup\nightly`、Google 金钥 `C:\nknu-crawl\secrets\google.env`（绝不显示、不印出）。

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

## 换程序

1. 在 Mac 上打包、送过去（只经 SSH）：

   ```bash
   deploy/lab-crawler/pack.sh
   ```

   它印出接着要打的 `scp` 那一行。小包只带爬虫自己的档，别的 session 改到一半的不会跟过去。
2. 在实验室电脑上（经 SSH 时每行前面加 `set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" &`）：

   ```text
   docker compose -f C:\nknu-crawl\compose.yaml stop viewer
   docker run --rm --network none -e TZ=Asia/Taipei -v nknu-crawl:/crawl -v C:/nknu-crawl/in/deploy:/in:ro nknu-pipeline bash /in/install.sh
   docker compose -f C:\nknu-crawl\compose.yaml up -d viewer
   ```

   `install.sh` 只在 04:00～23:00 做，有抓取在跑就不做；换掉的档都留成 `<档名>.prev-<时间>`，退回就是把它们改回原名。
3. 主机上的三个档有改的话，各自先留一份再换（例：`copy C:\nknu-crawl\compose.yaml C:\nknu-crawl\compose.yaml.prev-<日期>`，再 `copy C:\nknu-crawl\in\deploy\compose.yaml C:\nknu-crawl\compose.yaml`）。
4. 核对和空跑：

   ```text
   docker run --rm --network none -v nknu-crawl:/crawl:ro -v C:/nknu-crawl:/host:ro -v C:/nknu-crawl/in/deploy:/in:ro nknu-pipeline bash /in/check_lab.sh
   docker compose -f C:\nknu-crawl\compose.yaml run --rm -v C:/nknu-crawl/in/deploy:/in:ro nightly bash /in/proof_night.sh
   ```

   第一行要说「不一样的 0 个」；第二行不抓网页、不导出，只证明排程那条路是通的。

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
- 经 SSH 到 cmd 的命令里别写 `|`、`>NUL` 和成对的引号套引号：写成脚本档再跑。

## 机器坏了，从头重建

1. 装 Docker Desktop；建两个具名磁碟区 `nknu-crawl`、`nknu-work`（后一个是内容那边的，照 `deploy/lab-pipeline/`）。
2. 做映像：`nknu-pipeline` 照 `deploy/lab-pipeline/`；`nknu-browser` 把 `browser_image/Dockerfile` 放到 `C:\nknu-transfer\browser\`，`docker build -t nknu-browser C:\nknu-transfer\browser`。
3. 放资料：把备份的账本放回 `/crawl/archive/frontier.db`，原档放回 `D:\nknu-crawl\warc`，金钥档放到 `C:\nknu-crawl\secrets\google.env`。没有备份就只能重爬。
4. 放程序：`deploy/lab-crawler/pack.sh --full`（照 git 里已提交的那一版取整个程序），送过去，`install.sh` 加 `-e ANY_HOUR=1`；三个主机档 copy 到 `C:\nknu-crawl\`。
5. 建排程（上面那一行），`up -d viewer`，跑 `check_lab.sh` 和 `proof_night.sh`。
6. 第一晚之后看 `update.log` 和检视台的「每晚運行」。

## 和内容那条线接的地方

- `nightly_update.cmd` 最后叫 `C:\nknu-transfer\build\nightly_ingest.cmd`。那个档搬了位置，这里要跟着改一行。
- `export_sites.sh` 把导出写到 `/work/archive-export/sites/`，每站留现用的和上一份；内容那边 `/work/research/pipeline_2026-11-02/` 里 `exports*.txt` 写着的资料夹一律不删。那几份清单搬了位置，要改这支脚本开头的 `EXPORT_LISTS`。
- 每晚最后写 `/work/archive-export/sites/seen.tsv` 和 `current.tsv`，内容的每晚进库读它们。
- 内容读每个导出资料夹的 `manifest.json`。图片行有三栏它靠着用，写法不能随便改：「网址」（它拿网页里 `<img>` 的网址来对，所以协作平台的图要写成网页档现在写的那个网址）、「本地档案」（几行可以共用一个档）、「链到它的网页」（它的换库把关靠这一栏认「这张图的字并进了哪一页」，要一直写显示这张图的那几页）。改这三栏以前先跟内容对好。

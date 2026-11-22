# 实验室的第二个测试模型：Qwen3.5-9B（llama.cpp）

拿来和 Strata 上的 Qwen3.8-Flash-Next 对照着测我们的 MCP：换一个小的、整个放得进显卡的模型，看它查几次、答不答得对。**平时不开，测的时候才开，测完关掉。**

## 用什么

| 东西 | 用的是 | 说明 |
|---|---|---|
| 模型 | Qwen3.5-9B（Qwen 2026-02 发布，Apache-2.0），unsloth 压的 `Qwen3.5-9B-Q8_0.gguf`，9.53 GB | 8 位元，和原版几乎一样。sha256 `809626574d0cb43d4becfa56169980da2bb448f2299270f7be443cb89d0a6ae4` |
| 服务程序 | llama.cpp 官方映像 `ghcr.io/ggml-org/llama.cpp:server-cuda`，11-22 拉的那一版（build 12627，commit `225a5345b`），compose 里钉住它的 digest `sha256:f46a7938…cace609`，约 7.1 GB | OpenAI 格式的接口，答题程序照用 |
| 放在哪 | 模型在 Docker 卷 `nknu-llm-models`；compose 在实验室 `C:\nknu-llm\compose.yaml`（就是本资料夹的同名档） | 只开本机 `127.0.0.1:8790` |

为什么不用 Strata 架：

- Strata 的两份模型清单里都没有这个模型。
- 它整个放得进显卡，用不着 Strata 在内存和显卡之间搬专家的本事。
- llama.cpp 能同时答两题，Strata 一次只答一题。
- 用 Docker 跑，Windows 的「智慧型应用程式控制」挡不到（它挡过 Strata 的读图元件）。

## 显卡

- 这个服务约占 14 GiB：模型 8.9 GiB，两段对话的缓存 4 GiB，计算用的约 1 GiB。
  - 每段对话最长 64K token。32 层里只有 8 层要逐字存缓存，一个 token 32 KiB，比一般模型小很多。
- 我们常驻的（共用的检索模型和 Windows）约 3.8 GB，搜索时再多用约 1.2 GB。加起来约 19 GB，卡是 20 GB。
- **和 Strata 不能同时开。** 开它之前先卸下 Strata；它开着时不要载 Strata。
- **它到点自己停。** 开的时候设 `LLM_TIMEOUT`，容器里的 `timeout` 时间一到就停掉服务，容器也不会自己重开。测试脚本把它算成到 00:40 为止，赶在 00:45 卸 Strata、01:00 每晚更新之前。没设就是 3 小时。

## 开、看、关（在实验室上）

开（例：最多开 3 小时）：

```bat
set "LLM_TIMEOUT=3h" && set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" && docker compose -f C:\nknu-llm\compose.yaml up -d
```

看载好了没有：`curl -s http://127.0.0.1:8790/health` 回 `{"status":"ok"}` 就好了，`/v1/models` 列出 `qwen3.5-9b`。

关：

```bat
set "DOCKER_CONFIG=%USERPROFILE%\.docker-ssh" && docker compose -f C:\nknu-llm\compose.yaml down
```

## 答题程序怎么用它

- 从 Mac 开隧道：`ssh -N -L 18794:127.0.0.1:8790 -L 18792:127.0.0.1:8770 nknu-lab`。
- `research/eval_2026-11-18/tools/strata_agent.py OUT --llm http://127.0.0.1:18794 --request '<JSON>' --workers 2`。
- 取样设定照官方模型卡（`--request` 的 JSON）：
  - 不思考：`{"temperature":0.7,"top_p":0.8,"top_k":20,"min_p":0,"presence_penalty":1.5,"chat_template_kwargs":{"enable_thinking":false}}`
  - 思考（官方模板默认开着）：`{"temperature":1.0,"top_p":0.95,"top_k":20,"min_p":0,"presence_penalty":1.5,"max_tokens":12288,"chat_template_kwargs":{"enable_thinking":true}}`
- 已知问题：llama.cpp 的 issue #20837，开着思考时这个模型常把工具调用写进思考里，服务认不出来；关掉思考就正常。那个 issue 11-14 被关掉，但关它的修改只动了 Ministral-3 的解析，Qwen 这边没确认修好。

## 验过什么

- 11-22 16:33～16:45 下载：映像拉好；模型用映像里的 curl 下到卷里，sha256 和 Hugging Face 上列的一样；映像里有 `timeout`（GNU coreutils 9.4）。
- 11-22 16:55 第一次开：载好约 8 秒；整张卡从 3.8 GB 到 16.4 GB，这个服务约占 12.6 GB。
- 思考开关：同一个小问题，`enable_thinking` 为 true 时写 171 token（思考 511 字），false 时写 2 token、没有思考。Strata 上的模型也认这个参数（48 对 2 token）。
- 试 3 题（B030、B088、B074）：不思考时每题都只搜 1 次，约 30 秒；思考时搜了 1、1、2 次。工具调用都正常传回，这 6 题没碰到 #20837 那种把调用写进思考里的情况。
- 11-22 17:00～18:25 全量（83 题三种跑法，同时答两题）：
  - 直接问、不思考 19 分钟，每题中位 27 秒；直接问、思考 34 分钟，41 秒；做事模式、不思考 31 分钟，39 秒。
  - 三种跑法的回答里都没有 #20837 那种写成文字的工具调用，也没有到 4 轮被逼着作答的。
  - 中途断线两次：思考那组 1 题是模型服务这一头；做事模式那组 18:06 起有一路跟我们服务的连线断了，25 题第二轮才补上。答题程序已经改成断线就开新连线重答一次。
  - 18:24 关掉后显卡回到 3.8 GB。
- 批改结果（83 题，Strata 昨晚是 81 对）：直接问不思考 42 对、24 部分对、16 错、1 没答到；直接问思考 44 对、25 部分对、10 错、4 没答到；做事模式 25 对、20 部分对、32 错、6 没答到。原因和对照见 `research/eval_2026-11-18/runs/compare_1122.md`。这个模型适合拿来看「搜得少的 AI」会怎样，不适合当正式的答题基准。

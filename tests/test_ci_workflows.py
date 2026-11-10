# -*- coding: utf-8 -*-
"""CI 工作流结构门（三端 test/build 最小闭环，2026-10-11）。

**为什么需要这个文件**：`pr-test.yml` 与三条 `build-*.yml` 是三端验收的入口，
但它们的正确性此前没有任何自动化覆盖——改坏了（误删 matrix、把 PR 检出从合并
提交改成 head、放开产物上传、把 release 权限改了）只有等到某次 PR 或发布才暴露。
本文件把「矩阵真实存在」「PR 保持集成语义」「手动运行不发布」「素材供给在同一处、
在三端同一口径下走缓存+校验」这几条钉成断言。

**为什么不跑 YAML 解析做主口径**：`requirements.txt` 没有 PyYAML，把测试硬依赖
在它上面会让本文件在干净 CI 环境里直接 ImportError。主口径是**源码文本结构断言**
（块提取 + 位置比较），它不依赖任何第三方库，且能表达 YAML 解析拿不到的语义
（例如「这一步必须排在 PyInstaller 之前」）。PyYAML 在本地存在时**额外**跑一条
语法/事件键校验，并且必须处理裸 ``on:`` 被解析成布尔 ``True`` 的坑（见
``TestWorkflowYamlShape``）。

**为什么 `if:` 条件要按事件真值表求值**：「产物上传/发布 Release 的开关」是
语义契约，光断 `"refs/tags/" in step` 这类词面子串抓不到逻辑错——例如原来那条
`startsWith(github.ref, 'refs/tags/') || ...` 词面齐全，却在「手动 dispatch 把 ref
指到 tag」时误上传（评审 L03）。所以 `_eval_if` 把 `if:` 表达式按 GitHub 语义
在本地求值，三端逐行跑同一张真值表（见 `TestArtifactUploadBoundary`）。同一套求值
（`_eval_expression`）在 2026-10-12 批里扩到 `runner.os` 与 `failure()`/`success()`：
主套件命令的 `${{ runner.os == 'Linux' && ... || '' }}` 按三个 OS 逐腿展开求值，
诊断重跑步骤的 `if:` 也按 (runner.os, 之前是否失败, 主套件自身是否红) 跑真值表
（见 `TestPrTestCaptureAndFailureDiagnostics`）。

**范围**：只断言工作流文件的结构契约，不执行任何 CI、不联网、不碰远端。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PR_TEST = WORKFLOWS / "pr-test.yml"
BUILDS = {
    "windows": WORKFLOWS / "build-windows.yml",
    "macos": WORKFLOWS / "build-macos.yml",
    "linux": WORKFLOWS / "build-linux.yml",
}
# 构建入口（三端各自的真实命令），用于「素材供给必须排在构建之前」的位置断言
BUILD_MARKERS = {
    "windows": "build_onedir.ps1",
    "macos": "scripts/build_macos.sh",
    "linux": "scripts/build_linux.sh",
}
PREPARE = "scripts/prepare_frameseq_bundle.py"
CACHE_PATH = "assets/characters/*/frameseq"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _without_comments(text: str) -> str:
    """去掉整行注释后返回 YAML 本体（本仓库工作流的注释都是整行，无行内注释）。

    断言必须落在真正执行的 YAML 上：注释里出现 ``continue-on-error`` 这类字样
    （解释「本步没有它」）不该把门判红。
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _block(text: str, marker: str) -> str:
    """取包含 ``marker`` 的那一行起、到缩进回到该行同级或更浅为止的 YAML 片段。

    缩进感知的块提取：比正则稳，也不引入 parser 依赖。空行与更深的行都算块内，
    同级或更浅的非空行即块结束（``# 注释`` 同缩进也按块结束处理）。
    """
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if marker in line)
    base = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= base:
            break
        out.append(line)
    return "\n".join(out)


def _step_block(text: str, name: str) -> str:
    """按 ``- name: <name>`` 取一个 workflow step 的原文（缩进感知）。"""
    return _block(text, f"- name: {name}")


def _if_condition(step: str) -> str:
    """取 step 块里 ``if:`` 的表达式原文（本仓库的 ``if:`` 都是单行）。"""
    lines = [line for line in step.splitlines() if line.strip().startswith("if:")]
    assert len(lines) == 1, f"每步应有且只有一条 if:，实得 {lines}"
    return lines[0].split("if:", 1)[1].strip()


class _GithubContext:
    """把嵌套 dict 包成 GitHub 表达式的属性访问上下文。

    与 GitHub 一致的取空语义：不存在的键返回一个空上下文（等价于 ``null``），
    它 ``== 'true'`` 恒为 False、布尔值为 False，所以 push 事件上没有
    ``github.event.inputs`` 时不会抛异常，也不会误判为已授权上传。
    真实存在的标量值（字符串）原样返回，不包装——否则拿不到 ``'push'`` 和 ref。
    """

    def __init__(self, data: dict):
        self._data = data

    def __getattr__(self, name: str):
        value = self._data.get(name)
        if isinstance(value, dict):
            return _GithubContext(value)
        return _GithubContext({}) if value is None else value

    def __bool__(self) -> bool:
        return bool(self._data)

    def __repr__(self) -> str:  # 便于断言失败时看清求值现场
        return f"null{self._data or ''}"


def _eval_expression(
    expression: str,
    *,
    event_name: str,
    ref: str,
    inputs: dict | None = None,
    runner_os: str = "",
    failed: bool = False,
    main_suite_failed: bool = False,
):
    """按 GitHub 表达式语义求值一条表达式，返回**求值结果本身**（不强制布尔）。

    只实现本仓库工作流用到的子集（``github.*`` / ``runner.os`` / ``steps.<id>.outcome``
    属性、``&&``/``||``/``==``、``startsWith``、``failure()``/``success()``/``always()``、
    ``true``/``false``/``null`` 字面量）；工作流若改用别的东西，这里会以 NameError
    炸出来而不是静默放行。**不联网、不执行任何 CI**——纯本地表达式求值。

    ``failed`` 是 ``failure()`` 的本地替身（GitHub 语义：之前任一步骤失败）。
    ``main_suite_failed`` 是 ``steps.main_suite.outcome`` 的本地替身（本仓库只用到
    主套件这一个 step id：诊断重跑要确认「红的正是主套件」，而不是 ruff/bridge 门）。
    """
    source = expression.strip()
    assert "${{" not in source, f"此处按 GitHub 原生表达式求值，不应带 ${{{{ }}}}：{source}"
    source = source.replace("&&", " and ").replace("||", " or ")
    source = re.sub(r"\bstartsWith\(", "starts_with(", source)
    context = _GithubContext({
        "event_name": event_name,
        "ref": ref,
        "event": {"inputs": inputs or {}},
    })
    namespace = {
        "github": context,
        # runner_os 留空 = 取空上下文（等价 null），与 GitHub 未设置时的取空语义一致
        "runner": _GithubContext({"os": runner_os or None}),
        # 只暴露主套件一个 id：outcome 是该步原始结果（不受 continue-on-error 影响）
        "steps": _GithubContext({
            "main_suite": {"outcome": "failure" if main_suite_failed else "success"},
        }),
        "failure": lambda: failed,
        "success": lambda: not failed,
        "always": lambda: True,
        "cancelled": lambda: False,
        # GitHub 的小写字面量与 Python 不同名，显式补上（裸 true/false/null）
        "true": True,
        "false": False,
        "null": None,
        # startsWith 只接受字符串：null 前缀判断恒 false（GitHub 同为 false）
        "starts_with": lambda value, prefix: isinstance(value, str) and value.startswith(prefix),
    }
    return eval(source, {"__builtins__": {}}, namespace)  # 只求值上面构造的固定子集


def _eval_if(expression: str, **kwargs) -> bool:
    """``_eval_expression`` 的布尔形态：一条 ``if:`` 是否成立。"""
    return bool(_eval_expression(expression, **kwargs))


def _run_text(step: str) -> str:
    """取 step 的 ``run:`` 命令原文（含 YAML 折叠块的续行，拼成一行）。

    只取命令本体：step 的注释里也会出现 ``tests/xxx.py`` 这类字样，断言必须落在
    真正执行的那条命令上。
    """
    lines = step.splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip().startswith("run:"))
    head_indent = len(lines[index]) - len(lines[index].lstrip())
    first = lines[index].split("run:", 1)[1].strip()
    body = [] if first in {">", ">-", ">+", "|", "|-", "|+"} else [first]
    for line in lines[index + 1:]:
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) <= head_indent:
            break
        body.append(line.strip())
    return " ".join(body)


# 三个 matrix 腿 → GitHub runner.os 的名称（按 OS 分流的主套件命令要按它求值）
RUNNER_OS = {
    "windows-latest": "Windows",
    "ubuntu-latest": "Linux",
    "macos-latest": "macOS",
}


def _render_run(run: str, *, runner_os: str) -> str:
    """把 ``run`` 里的 ``${{ ... }}`` 片段求值成字符串（本仓库只用于按 OS 拼参数）。

    主套件命令里 ``${{ runner.os == 'Linux' && '--capture=tee-sys' || '' }}`` 必须按
    三个 OS 各自展开：Linux 得到开关，其余得到空串。求值走 ``_eval_expression``，
    所以工作流改用别的上下文会以 NameError 炸出来。
    """
    rendered = re.sub(
        r"\$\{\{(.+?)\}\}",
        lambda match: str(_eval_expression(
            match.group(1), event_name="pull_request", ref="refs/heads/main",
            runner_os=runner_os)),
        run,
    )
    assert "${{" not in rendered, f"表达式未被求值：{rendered}"
    return rendered


# --------------------------------------------------------------------- pr-test


class TestPrTestWorkflow:
    """PR 门禁：三 OS 真实 runner 矩阵 + 手动对齐入口 + 元数据留痕。"""

    def test_keeps_three_os_matrix_and_pr_integration_trigger(self):
        text = _text(PR_TEST)
        # PR 集成语义（合并提交后才判红绿）+ main push 回归，不许为「同一 SHA」
        # 把 PR 触发删掉或换成 head 检出
        assert "pull_request:" in text
        assert "branches: [main]" in text
        matrix = _block(text, "matrix:")
        for os_name in ("windows-latest", "ubuntu-latest", "macos-latest"):
            assert os_name in matrix, f"{os_name} 真实 runner 必须保留在矩阵里"

    def test_has_manual_dispatch_with_source_ref(self):
        text = _text(PR_TEST)
        dispatch = _block(text, "workflow_dispatch:")
        assert "source_ref" in dispatch
        # 手动运行必须能指定同一棵树，供三端跨平台对齐
        checkout = _step_block(text, "Checkout")
        assert "github.event.inputs.source_ref || github.sha" in checkout

    def test_pr_checkout_keeps_merge_commit_not_head(self):
        checkout = _step_block(_text(PR_TEST), "Checkout")
        # PR 事件下 github.sha = 合并提交（refs/pull/N/merge）：保留「集成后」语义
        assert "github.sha" in checkout
        assert "pull_request.head.sha" not in checkout, (
            "PR 门禁不得改成检出分支 head——那会丢掉 merge-ref 集成验证语义"
        )

    def test_records_resolved_sha_and_both_pr_refs(self):
        text = _text(PR_TEST)
        step = _step_block(text, "Record resolved source SHA")
        assert "git rev-parse HEAD" in step
        # PR 事件要同时记录 head 与合并提交两条 SHA（报告里可对账）
        assert "github.event.pull_request.head.sha" in step
        assert "github.sha" in step
        assert "GITHUB_STEP_SUMMARY" in step

    def test_records_dependency_and_node_versions(self):
        text = _text(PR_TEST)
        assert "pip freeze" in text
        assert "node --version" in text
        # Node 版本显式固定（bridge 门禁依赖 node --test；runner 预装版本会漂移）
        node = _step_block(text, "Setup Node")
        assert "actions/setup-node" in node
        assert "node-version" in node

    def test_summary_steps_run_on_windows_bash(self):
        # 三端同一条命令必须用同一种 shell：Windows runner 的默认 shell 是 pwsh，
        # `>> "$GITHUB_STEP_SUMMARY"` 在 pwsh 下不成立
        text = _text(PR_TEST)
        for name in ("Record resolved source SHA", "Record toolchain versions"):
            assert "shell: bash" in _step_block(text, name), name

    def test_no_retry_around_record_steps(self):
        # 元数据记录不许被 `||` 重跑蒙混（真实失败码必须冒泡）
        for name in ("Record resolved source SHA", "Record toolchain versions"):
            assert "||" not in _step_block(_text(PR_TEST), name), name


class TestPrTestCaptureAndFailureDiagnostics:
    """主套件捕获方式按 OS 分流 + Linux 失败后的受限前缀诊断（2026-10-12 批）。

    这一批只加「看得到原生崩溃」和「崩溃点在哪个用例」的能力，不新增判据：
    主套件仍是唯一决定红绿的主套件运行（只有一条命令、仍是 `-q`），诊断重跑不覆盖
    它的结果。
    """

    MAIN_SUITE = "Pytest (main suite)"
    PREFIX_DIAGNOSTICS = "Pytest (Linux prefix diagnostics after main-suite failure)"

    def test_main_suite_is_one_step_not_per_os_copies(self):
        # 主套件的判据是它自己那份 --ignore 清单：按 OS 复制出第二个主套件步骤 =
        # 每轮多跑 13-15 分钟，而且两处命令必然漂移
        assert _text(PR_TEST).count("--ignore=tests/test_webm_reader_lifecycle.py") == 1

    @pytest.mark.parametrize("os_name", sorted(RUNNER_OS))
    def test_capture_flag_follows_runner_os(self, os_name):
        step = _step_block(_text(PR_TEST), self.MAIN_SUITE)
        rendered = _render_run(_run_text(step), runner_os=RUNNER_OS[os_name])
        assert rendered.startswith("python -m pytest -q "), rendered
        if RUNNER_OS[os_name] == "Linux":
            assert "--capture=tee-sys" in rendered, (
                "Linux 主套件要改用 tee-sys：默认 fd 捕获把原生写 fd 的输出"
                "（glibc abort 文本、Qt 默认消息处理器、faulthandler 转储）吞进临时文件"
            )
        else:
            assert "--capture=tee-sys" not in rendered, f"{os_name} 应保持默认 fd 捕获 + -q"
        # 三个 OS 跑的是同一条命令：忽略清单必须都还在（只有捕获开关按 OS 展开）
        for marker in ("--ignore=tests/test_webm_reader_lifecycle.py",
                       "--ignore=tests/test_webm_clip_lifecycle.py",
                       "--ignore=tests/test_webm_first_frame_lock.py",
                       "--ignore=tests/test_low_priority_warm_interaction_yield.py"):
            assert marker in rendered, marker

    def test_prefix_diagnostics_condition_is_linux_failure_only(self):
        condition = _if_condition(_step_block(_text(PR_TEST), self.PREFIX_DIAGNOSTICS))
        # 必须**显式**带状态函数：GitHub 在 `if:` 里没有 failure()/success()/always()
        # 时隐式套一层 success()，前序步骤一红本步就被跳过——诊断恰恰要在红时跑。
        assert condition.startswith("failure()"), (
            f"条件必须以 failure() 开头（缺了会被 GitHub 隐式 success() 跳过）：{condition}"
        )
        truth_table = [
            # (runner.os, 之前有步骤失败, 主套件自己是否红, 期望本步是否运行)
            ("Linux", True, True, True),
            ("Linux", False, False, False),
            # ruff/bridge 门在 Linux 上红、主套件没红：不再白跑这 ~2.5 min 前缀
            ("Linux", True, False, False),
            ("Windows", True, True, False),
            ("macOS", True, True, False),
        ]
        for runner_os, failed, main_suite_failed, expected in truth_table:
            actual = _eval_if(condition, event_name="pull_request", ref="refs/heads/main",
                              runner_os=runner_os, failed=failed,
                              main_suite_failed=main_suite_failed)
            assert actual is expected, (
                f"runner.os={runner_os} failed={failed} main_suite_failed={main_suite_failed}"
                f" → 运行={actual}，期望 {expected}；条件原文：{condition}"
            )

    def test_prefix_diagnostics_condition_reads_main_suite_step_outcome(self):
        # 条件里引用的 step id 必须真的挂在主套件那一步上：id 写错/被改掉时
        # GitHub 求值成 null，条件是恒假——诊断步骤会静默永不运行（比红更难发现）。
        main_suite = _step_block(_text(PR_TEST), self.MAIN_SUITE)
        assert "id: main_suite" in main_suite, "主套件步骤必须带 id: main_suite"
        assert "steps.main_suite.outcome" in _if_condition(
            _step_block(_text(PR_TEST), self.PREFIX_DIAGNOSTICS)
        )

    def test_prefix_diagnostics_covers_agent_cost_to_config_schema_range(self):
        step = _step_block(_text(PR_TEST), self.PREFIX_DIAGNOSTICS)
        run = _run_text(step)
        files = re.findall(r"tests/[A-Za-z0-9_]+\.py", run)
        assert files[0] == "tests/test_agent_cost.py", "前缀起点必须是收集序首个测试文件"
        assert files[-1] == "tests/test_config_schema.py", "前缀终点是 test_config_schema.py"
        assert files == sorted(set(files)), "前缀清单必须按收集序去重升序（顺序决定 nodeid 对齐）"
        # 有界性：升序 + 终点 = test_config_schema.py 已经保证清单不会越到该文件之后
        # （那就不再是「有界前缀」，而是大半个主套件）；这里再钉两件事——收集序首文件
        # 一改（新增更靠前的测试文件）前缀起点就得跟着改，以及清单里的文件必须真的
        # 存在（改名后这一步会当场 exit 4，不会静默跑了个空）。
        names = sorted(path.name for path in (ROOT / "tests").glob("test_*.py"))
        assert names[: names.index("test_config_schema.py") + 1][0] == "test_agent_cost.py", (
            "收集序首文件变了，前缀起点要跟着改"
        )
        for name in files:
            assert (ROOT / name).is_file(), f"{name} 不存在：改名后这一步会 exit 4"
        # 诊断重跑用 -vv：崩溃点在原生信号杀掉进程时只剩「最后一行 nodeid」这一条线索
        assert "--capture=tee-sys" in run and "-vv" in run.split(), run

    def test_prefix_diagnostics_does_not_override_the_failure(self):
        step = _step_block(_text(PR_TEST), self.PREFIX_DIAGNOSTICS)
        # 诊断重跑不是「重跑覆盖失败」：不许 continue-on-error，也不许用 `||` 兜底/重试
        assert "continue-on-error" not in step
        assert "||" not in _run_text(step), _run_text(step)

    def test_pr_gate_never_swallows_failures_or_uploads_artifacts(self):
        text = _without_comments(_text(PR_TEST))
        # 门禁全程不许有 continue-on-error；PR 门禁也不上传产物
        assert "continue-on-error" not in text
        assert "upload-artifact" not in text, "PR 门禁不上传产物（打包上传归 build-*.yml）"

    def test_crash_diagnostics_readback_is_the_last_step(self):
        # 面包屑回读必须留在最后一步：所有 pytest 步骤跑完才读得到「最后死在哪一例」
        names = re.findall(r"- name: (.+)", _text(PR_TEST))
        assert names[-1] == "Pytest crash diagnostics", names[-1]
        step = _step_block(_text(PR_TEST), "Pytest crash diagnostics")
        assert _if_condition(step) == "failure()"
        assert "tail -n 30" in step, "只回读末尾，不整篇进日志"
        # 两个诊断目录都要回读：主套件那份 + Linux 受限前缀重跑那份（前缀步骤红时
        # 才有）。只读一份就会漏掉另一半现场——前缀重跑跑完通过时，主套件那份
        # 仍记录着「死在谁、哪个阶段」，反过来也一样。
        for name in ("dspet-pytest-diag", "dspet-pytest-diag-linux-prefix"):
            assert name in step, f"回读步骤必须覆盖 {name}"


# ----------------------------------------------------------------- build 共用


class TestBuildWorkflowStructure:
    """三条 build-*.yml 的共用契约（参数化，逐端跑同一套断言）。"""

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_has_source_ref_and_upload_switch_off_by_default(self, name):
        text = _text(BUILDS[name])
        dispatch = _block(text, "workflow_dispatch:")
        assert "source_ref" in dispatch, "手动构建必须能指定要构建的 ref/SHA"
        switch = _block(text, "upload_artifacts:")
        assert "default: false" in switch, "上传开关默认必须为 false"

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_checkout_takes_manual_ref_but_keeps_event_default(self, name):
        checkout = _step_block(_text(BUILDS[name]), "Checkout")
        assert "github.event.inputs.source_ref || github.sha" in checkout
        # tag 推送仍检出该 tag 的提交（github.sha），既有发布行为不变
        assert "ref: " in checkout

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_records_resolved_sha_and_toolchain(self, name):
        text = _text(BUILDS[name])
        assert "git rev-parse HEAD" in text
        assert "GITHUB_STEP_SUMMARY" in text
        assert "pip freeze" in text
        # node 只在宿主 runner 上有：Linux 构建跑在 ubuntu:22.04 容器里（issue
        # #432，见 TestLinuxBuildBaseline），容器无 node 且构建链不用它。
        if name != "linux":
            assert "node --version" in text

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_frameseq_prepare_runs_once_before_build_with_cache(self, name):
        text = _text(BUILDS[name])
        # 同一份素材只准备一次（106 段全量转换约 28 min，重复即三倍成本）
        assert text.count(PREPARE) == 2, "只应有「算 key」与「准备素材」两处引用"
        assert "--print-cache-key" in text
        cache = _step_block(text, "Restore frameseq material cache")
        assert CACHE_PATH in cache
        assert "steps.frameseq-cache.outputs.key" in cache
        # 算 key → 取缓存 → 准备素材 → 构建（顺序错任何一步都白跑一次 28 min）
        assert (text.index(PREPARE)
                < text.index("actions/cache@")
                < text.rindex(PREPARE)
                < text.index(BUILD_MARKERS[name]))

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_prepare_step_does_not_retry_or_pipe_output(self, name):
        step = _step_block(_text(BUILDS[name]), "Prepare frameseq material")
        assert PREPARE in step
        # 不许用 `||` 重试或 `| tail` 之类管道吞掉真实失败码
        assert "||" not in step
        assert "|" not in step

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_upload_artifact_needs_tag_or_explicit_optin(self, name):
        step = _step_block(_text(BUILDS[name]), "Upload artifact")
        assert "upload-artifact@v4" in step
        assert "refs/tags/" in step, "tag 推送照旧上传"
        assert "upload_artifacts" in step, "手动必须显式 opt-in 才上传"

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_publish_release_only_on_tag_push(self, name):
        step = _step_block(_text(BUILDS[name]), "Publish Release")
        assert "refs/tags/" in step
        assert "github.event_name == 'push'" in step, (
            "手动 dispatch（含在 tag ref 上手动跑）不得发布 Release"
        )

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_no_unsafe_uninstall_cleanup_smoke(self, name):
        # `--uninstall-cleanup` 会动 hooks/registry，不许当启动冒烟用
        for path in (PR_TEST, BUILDS[name]):
            assert "--uninstall-cleanup" not in _text(path), path.name


class TestLinuxBuildBaseline:
    """Linux 构建基线守卫（issue #432）：22.04 容器 + uv Python，缺一即红。

    事故复盘：`ubuntu-latest` 别名整体漂到 24.04（glibc 2.38）没人察觉，叠加
    setup-python 的 CPython 工件也换到 24.04 基线重编——PyInstaller 把构建机
    的系统库整份拷进包，Ubuntu 22.04 用户启动即 GLIBC not found，从第一个
    Linux 包起如此。下面两条守卫防的就是"有人把容器行简化掉 / 换回
    setup-python"这两个静默复发入口；产物地板本身由
    scripts/check_elf_symbol_floor.py 在 build_linux.sh 里逐变体硬门
    （接线由 tests/test_elf_symbol_floor.py 守）。
    """

    def test_linux_job_pins_2204_container(self):
        job = _block(_text(BUILDS["linux"]), "build-linux:")
        assert "container: ubuntu:22.04" in _without_comments(job), (
            "Linux 构建必须钉在 ubuntu:22.04 容器上（issue #432 的发布基线）；"
            "拿掉容器 = 基线跟着会漂移/退役的托管镜像走，产物地板立刻失控"
        )

    def test_linux_build_uses_uv_python_not_setup_python(self):
        body = _without_comments(_text(BUILDS["linux"]))
        assert "setup-python" not in body, (
            "setup-python 的 CPython 工件已换到 24.04 基线（libpython 要求 "
            "GLIBC_2.38），在 22.04 容器里连启动都过不了（issue #432）"
        )
        assert "uv python install 3.11" in body
        assert "uv pip install" in body and "requirements.txt" in body


# ------------------------------------------------- 产物上传 / Release 事件边界

# 事件输入真值表：(event_name, github.ref, workflow_dispatch inputs, 期望是否上传)
#
# 评审口径的四输入是前四行；后两行是 L03 的真身——**手动 dispatch 的运行 ref
# 选到一个已存在的 tag**，此时 github.ref 满足 `refs/tags/` 前缀，若条件只看
# 前缀就会绕过 upload_artifacts 默认 false 直接上传数百 MB。故真值表必须含这
# 两行，否则「词面子串齐全但逻辑仍错」照样绿。
UPLOAD_TRUTH_TABLE = [
    # tag 推送：始终上传（发布路径，未经手动开关）
    ("push", "refs/tags/v1.0.0", None, True),
    # 普通分支 push：不上传
    ("push", "refs/heads/main", None, False),
    # 手动 + 默认开关 false：不上传
    ("workflow_dispatch", "refs/heads/main", {"upload_artifacts": "false"}, False),
    # 手动 + 显式打开：上传（任何 ref 都算，包括分支 ref）
    ("workflow_dispatch", "refs/heads/main", {"upload_artifacts": "true"}, True),
    # L03：手动把运行 ref 选到 tag + 默认开关 false —— 不许上传
    ("workflow_dispatch", "refs/tags/v1.0.0", {"upload_artifacts": "false"}, False),
    # 手动同一 ref + 显式打开 —— 允许上传（授权语义只由开关决定）
    ("workflow_dispatch", "refs/tags/v1.0.0", {"upload_artifacts": "true"}, True),
]

# Release 事件真值表：只有 push + tag 才发布，手动任何 ref（含 tag）都不许发布
RELEASE_TRUTH_TABLE = [
    ("push", "refs/tags/v1.0.0", True),
    ("push", "refs/heads/main", False),
    ("workflow_dispatch", "refs/tags/v1.0.0", False),
    ("workflow_dispatch", "refs/heads/main", False),
]


class TestArtifactUploadBoundary:
    """产物上传的事件边界（评审 L03）：三端同一条表达式，按真值表求值。"""

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_upload_condition_matches_event_truth_table(self, name):
        condition = _if_condition(_step_block(_text(BUILDS[name]), "Upload artifact"))
        for event_name, ref, inputs, expected in UPLOAD_TRUTH_TABLE:
            actual = _eval_if(condition, event_name=event_name, ref=ref, inputs=inputs)
            assert actual is expected, (
                f"{name}: event={event_name} ref={ref} inputs={inputs} "
                f"→ 上传={actual}，期望 {expected}；条件原文：{condition}"
            )

    def test_three_workflows_share_one_upload_condition(self):
        # 三端必须是同一条表达式：一端漏改就是「某平台默默上传」
        conditions = {
            name: _if_condition(_step_block(_text(path), "Upload artifact"))
            for name, path in BUILDS.items()
        }
        assert len(set(conditions.values())) == 1, conditions


class TestReleaseBoundary:
    """发布边界：只有 push + tag 发布，手动在 tag ref 上跑也绝不发布。"""

    @pytest.mark.parametrize("name", sorted(BUILDS))
    def test_release_condition_matches_event_truth_table(self, name):
        condition = _if_condition(_step_block(_text(BUILDS[name]), "Publish Release"))
        for event_name, ref, expected in RELEASE_TRUTH_TABLE:
            actual = _eval_if(condition, event_name=event_name, ref=ref)
            assert actual is expected, (
                f"{name}: event={event_name} ref={ref} → 发布={actual}，"
                f"期望 {expected}；条件原文：{condition}"
            )

    def test_three_workflows_share_one_release_condition(self):
        conditions = {
            name: _if_condition(_step_block(_text(path), "Publish Release"))
            for name, path in BUILDS.items()
        }
        assert len(set(conditions.values())) == 1, conditions


# ------------------------------------------------------------ Windows 安装器


class TestWindowsInstaller:
    """Windows 安装器两处真实不一致（三端读数报告 E6）。"""

    def test_webm_installer_uses_documented_app_id(self):
        text = _text(BUILDS["windows"])
        webm_args = _block(text, "$webm = @(")
        # 与 docs/ONEDIR_PACKAGING.md 记载的 webm 唯一标识一致：两个安装包
        # 共用 AppId 会互装覆盖对方卸载登记
        assert "{{ED2590E4-A968-4E8D-B7C4-75DFE012D0E9}" in webm_args
        assert "{{3424d6cc-af3c-4383-8797-ab520b923aa6}" not in webm_args
        # 文档是这条唯一标识的出处，两边必须一致（改一处即红）
        doc = (ROOT / "docs" / "ONEDIR_PACKAGING.md").read_text(encoding="utf-8")
        assert "ED2590E4-A968-4E8D-B7C4-75DFE012D0E9" in doc

    def test_chat_installer_gets_version_from_single_source(self):
        text = _text(BUILDS["windows"])
        # webm-chat 走 .iss 默认定义，但版本不能漏传：.iss 里 MyAppVersion 是
        # 硬编码默认值，不传即与 pet/__init__.py 单一来源漂移
        chat_args = _block(text, "$chat = @(")
        assert "MyAppVersion" in chat_args
        assert "$version" in chat_args
        assert "ISCC.exe @chat" in text
        # 版本仍从 pet/__init__.py 读，不许改成第二处硬编码
        assert "__version__ = '([^']+)'" in text


# ------------------------------------------------------------ YAML 语法（可选）


class TestWorkflowYamlShape:
    """本地有 PyYAML 时附带的语法/事件键校验（CI 无该依赖则跳过）。

    ``requirements.txt`` 没有 PyYAML——本类只作本地加固，不是主口径。
    """

    @staticmethod
    def _load(path: Path) -> dict:
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    @staticmethod
    def _events(data: dict) -> dict:
        """取 ``on:`` 事件表；PyYAML 把裸 ``on`` 键解析成布尔 ``True``。"""
        events = data.get("on")
        if events is None:
            events = data.get(True)
        assert isinstance(events, dict), "on: 必须是事件表（裸 on 被解析成 True 也要能取到）"
        return events

    @pytest.mark.parametrize("path", [PR_TEST, *BUILDS.values()], ids=lambda p: Path(p).name)
    def test_parses_and_declares_expected_events(self, path):
        data = self._load(path)
        events = self._events(data)
        assert "workflow_dispatch" in events
        assert "push" in events
        if path.name == "pr-test.yml":
            assert "pull_request" in events

    @pytest.mark.parametrize("path", [PR_TEST, *BUILDS.values()], ids=lambda p: Path(p).name)
    def test_raw_on_key_is_not_quoted_away(self, path):
        # 源码里必须是 `on:`（不写成 "on": / 'on':）——后者是无效事件表
        text = _text(path)
        assert "\non:" in text or text.startswith("on:")

    def test_dispatch_inputs_are_typed(self):
        for path in [PR_TEST, *BUILDS.values()]:
            dispatch = self._events(self._load(path))["workflow_dispatch"]
            inputs = dispatch.get("inputs") or {}
            for key, spec in inputs.items():
                assert spec.get("type") in {"string", "boolean"}, f"{path.name}:{key} 缺 type"


class TestWindowsShardRunner:
    """Windows 主套件分片执行器（2026-10-13，hosted runner AV 规避）的契约守卫。

    背景：Windows CI 单进程整跑在 ~2/3 处反复 access violation（堆腐蚀签名，
    漂移崩点；mac/Linux/本机不复现），分片（四个独立进程）两轮诊断零崩。
    """

    SHARD_STEP = "Pytest (main suite, Windows shards)"

    def test_shard_step_exists_windows_only_and_runs_script(self):
        step = _step_block(_text(PR_TEST), self.SHARD_STEP)
        assert "python scripts/ci_shards.py" in _run_text(step)
        assert "runner.os == 'Windows'" in _if_condition(step)

    def test_monolithic_main_suite_skips_windows(self):
        # 两个步骤合起来仍是「每端恰好一条主套件路径」
        condition = _if_condition(_step_block(
            _text(PR_TEST), TestPrTestCaptureAndFailureDiagnostics.MAIN_SUITE))
        assert "runner.os != 'Windows'" in condition

    def test_shard_script_ignores_parity_with_main_suite(self):
        # 分片脚本里的忽略清单必须与主套件步骤同源同内容——漂移=漏跑一族
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ci_shards", ROOT / "scripts" / "ci_shards.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run = _run_text(_step_block(
            _text(PR_TEST), TestPrTestCaptureAndFailureDiagnostics.MAIN_SUITE))
        for path in mod.IGNORES:
            assert f"--ignore={path}" in run, f"主套件步骤缺 {path}"
        assert len(mod.IGNORES) == 4 and len(set(mod.IGNORES)) == 4

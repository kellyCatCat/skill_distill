# 项目说明

将 IPRAN 网络运维排障文档批量蒸馏为网管 agent 可用的 skill 文件。

- `skill_self_distill_pipeline.py`：主流水线。扫描源文档树（`result/v01/tree`），按"一级目录/二级目录"分组，调用大模型把每组文档合并转换为一个 skill（输出为 `<一级中文>/<二级中文>.md`），并生成 `skill_tree_structure.txt` 与 `conversion_report.json`。散落在一级目录下的文档会自动并入名称最相似的既有二级分组；若该一级目录没有二级目录，则全部合并为 `<一级名去前缀>故障案例.md`（如 `故障处理：QoS` → `QoS故障案例.md`），归并明细会在运行时打印。
- `skill_case_merge_pipeline.py`：增量并入流水线。读取 `cases/*.json` 里的新故障案例，先定位每个案例组应并入哪个既有 skill（或需要新建），再判定 `covered`（已覆盖）/ `append`（追加小节）/ `create`（新建 skill）并写入，最后输出 `reports/skill_change_report_<mm-dd>.md` 变更说明。`DRY_RUN=True` 只出报告不落盘。
- `validate_skills.py`：校验生成的 skill 是否完整合规（frontmatter、截断、残留引用、禁用短语等），存在 ERROR 时退出码为 1。
- `cases/`：新增故障案例的原始数据（入库）；`reports/`：变更说明，其中运行时生成的 `skill_change_report_*.md` 不入库。

源文档树和生成的 skill 不入库（见 `.gitignore`）。

# Git 工作流

- 所有修改直接基于 `main` 开发，提交后合入（推送到）`main`。
- 不要创建新分支。

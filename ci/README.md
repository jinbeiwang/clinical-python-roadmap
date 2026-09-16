# CI 配置

`ci.yml` 是 GitHub Actions 的工作流定义，对应教程
[第 17 章 · 工程化与后续进阶](../docs/17-工程化与后续进阶.md)。

它做四件事：

1. **多 Python 版本冒烟测试**（3.10 / 3.12 / 3.13）
   —— 逐个跑通 `cases/` 下全部案例，**跑完所有再报告**（异常隔离）
2. **校验输出产物** —— 检查 `outputs/` 下 7 个产物是否都生成了
3. **校验 case05 的双编程验证结论** —— 若"完全一致"的变量数下降就失败
4. **上传产物** 供下载查看

## 为什么放在这里而不是 `.github/workflows/`

GitHub 对**创建/修改工作流文件**有额外的权限要求：
推送者的 Personal Access Token 必须带 `workflow` scope。
为了不把"克隆下来就能用"变成"必须先配置权限"，
这里以**模板**的形式提供。

## 怎么启用

```bash
mkdir -p .github/workflows
cp ci/ci.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "ci: 启用 GitHub Actions 冒烟测试"
git push
```

或者直接在 GitHub 网页上：**Actions → set up a workflow yourself**，
把 `ci/ci.yml` 的内容粘进去。

> ⚠️ 如果你的推送被拒绝并提示
> `refusing to allow a Personal Access Token to create or update workflow`，
> 说明你的 token 缺少 `workflow` scope。
> 到 **GitHub → Settings → Developer settings → Personal access tokens**
> 给该 token 勾上 `workflow`，或改用带该权限的新 token。

## 为什么"跑完所有案例再报告"很重要

这是一个**设计选择**，也是案例 06 演示的核心思想：

```bash
failed=0
for f in cases/case0*.py; do
  if python "$f" > /tmp/out.txt 2>&1; then
    echo "✓ 通过"
  else
    echo "✗ 失败"
    failed=1          # 记录，但不退出
  fi
done
exit $failed          # 最后统一报告
```

如果第一个失败就退出，你只能看到一个问题；
跑完再报告，你能**一次看到所有问题** —— 修一轮而不是修 N 轮。

这是它和传统 SAS 批处理程序（一个 DATA 步出错就整个中断）最大的区别。

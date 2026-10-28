# 本地文献库自动同步

本仓库按本地资料变更同步，不自动检索新论文，也不读取浏览器、学校登录或其他文件夹。

## 更新范围

- 本地工作流登记的文献题录、下载状态、版本说明。
- 原始Excel文献总表、RIS文献库和分类清单；原文件不被同步脚本修改。
- 已核对题名/DOI、文件SHA256及再分发许可的PDF。
- 新增文件须先登记到文献工作流中。未登记的PDF不会被直接公开，避免误传其他文件。

本地路径、下载数据库、许可解析缓存、进程状态和日志放在被Git忽略的`.local/`或本地工作目录中。
学校订阅权限和作者平台可读取权限不能替代公开再分发许可；全文许可不明时只同步题录、来源及状态。
保存版本不同于正式发表版本时，不自动套用正式发表版本的许可。

## 运行方式

1. 在本机`.local/config.json`填写`source_dir`（文献输出文件夹）及`work_dir`（题录/下载状态工作文件夹）。
2. 使用包含`pypdf`的Python运行环境，安装Git、Git LFS，并完成GitHub推送认证。
3. 初次发布由人工检查后提交；后台程序仅维护其生成的`catalog/`、`papers/`、`licenses/`及首页。
4. Windows登录任务启动`watch_library.py`，每5秒检测一次；连续10秒无新变更后进行同步。

配置示例（仅放在本机，不提交Git）：

```json
{
  "source_dir": "D:/research/paper-library",
  "work_dir": "D:/research/paper-work",
  "poll_seconds": 5,
  "debounce_seconds": 10,
  "retry_seconds": 120
}
```

手动同步：

```sh
python scripts/sync_library.py --config .local/config.json
```

只重建导出文件、暂不提交上传：

```sh
python scripts/sync_library.py --config .local/config.json --build-only
```

在PowerShell中安装登录启动任务：

```powershell
.\scripts\install_watch_task.ps1 -PythonPath 'C:\path\to\pythonw.exe' -StartNow
```

任务名称：`RobotMaterialPaperset_LocalSync`。关闭浏览器不影响这个任务。
电脑关机、未登录或离线时不能实时上传；恢复登录和网络后，会重新检查本地资料。
上传耗时取决于PDF体积和网络；这里的“自动同步”不是毫秒级或云端全天候服务。

## 查看与停止

- 最新本机状态：`.local/runtime_status.json`。
- 最近成功提交：`.local/sync_state.json`，记录已经与远端核对的提交SHA。
- 运行日志：`.local/watch.log`，保留轮转日志。
- Windows任务计划程序中查看、禁用或删除`RobotMaterialPaperset_LocalSync`。

暂停当前进程并禁用下次启动：

```powershell
Stop-ScheduledTask -TaskName 'RobotMaterialPaperset_LocalSync'
Disable-ScheduledTask -TaskName 'RobotMaterialPaperset_LocalSync'
```

恢复：

```powershell
Enable-ScheduledTask -TaskName 'RobotMaterialPaperset_LocalSync'
Start-ScheduledTask -TaskName 'RobotMaterialPaperset_LocalSync'
```

## 冲突与异常

程序不使用强制推送，只接受`pull --ff-only`。发现人工未提交修改、分支分叉、认证失败或网络失败时，会记录错误，保留原始资料。
网络失败在默认120秒后重试；认证需要用户重新登录GitHub。存在人工修改时先检查并提交该修改，再恢复自动同步。
Git和Git LFS的推送都成功且本地提交等于远端`main`后，才记录同步成功。

#!/bin/bash
# 每日报告定时任务入口（兼容 GitHub Actions 与本地运行）
set -o pipefail

# 1. 动态获取当前项目根目录
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

export US_HEADLESS=1

# 2. 移除硬编码的 PATH，确保 GitHub Actions 的 Python 环境变量有效

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/daily_$(date +%Y-%m-%d).log"

# 3. 兼容 Python 执行指令
PYTHON_CMD="python"
if ! command -v python &> /dev/null; then
    PYTHON_CMD="python3"
fi

# 美东时间与窗口判断（TZ=America/New_York 在 GitHub 的 Ubuntu 环境同样支持）
read -r ET_H ET_M ET_HM <<<"$(TZ=America/New_York date '+%H %M %H:%M')"
ET_MIN=$(( 10#$ET_H * 60 + 10#$ET_M ))

REVIEW_START=$(( 19 * 60 + 30 ))   # 收盘复盘窗口：美东 19:30-21:30
REVIEW_END=$(( 21 * 60 + 30 ))
PREOPEN_START=$(( 9 * 60 + 10 ))   # 盘前窗口：美东 09:10-09:30，目标 09:20
PREOPEN_END=$(( 9 * 60 + 30 ))

if [ "$US_FORCE" = "1" ]; then
    WINDOW="手动强制"
elif [ "$ET_MIN" -ge "$REVIEW_START" ] && [ "$ET_MIN" -le "$REVIEW_END" ]; then
    WINDOW="收盘复盘"
elif [ "$ET_MIN" -ge "$PREOPEN_START" ] && [ "$ET_MIN" -le "$PREOPEN_END" ]; then
    WINDOW="开盘前提醒"
else
    MSG="$(date '+%Y-%m-%d %H:%M:%S') 跳过：美东 $ET_HM 不在复盘(19:30-21:30)或盘前(09:10-09:30)窗口内"
    echo "$MSG" | tee -a "$LOG_FILE"
    exit 0
fi

echo "===== 开始 $(date '+%Y-%m-%d %H:%M:%S')（美东 $ET_HM · $WINDOW）=====" | tee -a "$LOG_FILE"

# 4. 执行 python 脚本，并将输出同时打印到 GitHub 网页控制台与日志文件
"$PYTHON_CMD" "$PROJECT_DIR/main.py" 2>&1 | tee -a "$LOG_FILE"
STATUS=${PIPESTATUS[0]}

echo "===== 结束 $(date '+%Y-%m-%d %H:%M:%S') 退出码=$STATUS =====" | tee -a "$LOG_FILE"

# 清理历史日志
find "$LOG_DIR" -name 'daily_*.log' -mtime +30 -delete 2>/dev/null

exit $STATUS

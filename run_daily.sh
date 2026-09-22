#!/bin/bash
# 每日报告定时任务入口。launchd 不继承 shell 环境，所以这里显式设置工作目录和 PATH。
set -o pipefail

PROJECT_DIR="/Users/maomao/Desktop/US"
cd "$PROJECT_DIR" || exit 1

export US_HEADLESS=1
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/daily_$(date +%Y-%m-%d).log"

# launchd 只认本地钟点，不懂美股夏令时。plist 里配了三个北京时间触发点
# （09:00 复盘、21:20 与 22:20 盘前），具体哪个真正执行由美东时间决定：
#   夏令时 EDT：北京 21:20 → 美东 09:20 ✓ ；北京 22:20 → 美东 10:20 ✗
#   冬令时 EST：北京 21:20 → 美东 08:20 ✗ ；北京 22:20 → 美东 09:20 ✓
# 这样夏令时切换时不用手改 plist。US_FORCE=1 可跳过判断，用于手动测试。
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
    echo "$(date '+%Y-%m-%d %H:%M:%S') 跳过：美东 $ET_HM 不在复盘(19:30-21:30)或盘前(09:10-09:30)窗口内" >>"$LOG_FILE"
    exit 0
fi

echo "===== 开始 $(date '+%Y-%m-%d %H:%M:%S')（美东 $ET_HM · $WINDOW）=====" >>"$LOG_FILE"
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/main.py" >>"$LOG_FILE" 2>&1
STATUS=$?
echo "===== 结束 $(date '+%Y-%m-%d %H:%M:%S') 退出码=$STATUS =====" >>"$LOG_FILE"

# 只保留最近 30 天日志
find "$LOG_DIR" -name 'daily_*.log' -mtime +30 -delete 2>/dev/null

exit $STATUS

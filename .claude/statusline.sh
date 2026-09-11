#!/bin/bash
# Claude Code statusLine script
# Shows: model name | context window bar+% | session allocation bar+%

input=$(cat)

model=$(printf '%s' "$input" | jq -r '.model.display_name // "Claude"')
ctx_size=$(printf '%s' "$input" | jq -r '.context_window.context_window_size // 200000')
ctx_pct=$(printf '%s' "$input" | jq -r '.context_window.used_percentage // 0')

session_pct=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
session_resets_at=$(printf '%s' "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')

bar_width=40

if [ "$ctx_size" -ge 1000 ] 2>/dev/null; then
  ctx_label="$((ctx_size / 1000))K"
else
  ctx_label="$ctx_size"
fi

render_bar() {
  local pct="$1" width="$2"
  local pct_int filled empty bar color

  pct_int=$(printf '%.0f' "$pct" 2>/dev/null)
  [ -z "$pct_int" ] && pct_int=0

  filled=$((pct_int * width / 100))
  [ "$filled" -gt "$width" ] && filled=$width
  [ "$filled" -lt 0 ] && filled=0
  empty=$((width - filled))

  bar=""
  [ "$filled" -gt 0 ] && bar+=$(printf '▓%.0s' $(seq 1 "$filled"))
  [ "$empty" -gt 0 ] && bar+=$(printf '░%.0s' $(seq 1 "$empty"))

  if [ "$pct_int" -ge 80 ]; then
    color='\033[31m'
  elif [ "$pct_int" -ge 50 ]; then
    color='\033[33m'
  else
    color='\033[32m'
  fi

  printf '%b%s\033[00m %s%%' "$color" "$bar" "$pct_int"
}

format_countdown() {
  local target="$1" now delta h m
  now=$(date +%s)
  delta=$((target - now))
  [ "$delta" -lt 0 ] && delta=0
  h=$((delta / 3600))
  m=$(((delta % 3600) / 60))
  if [ "$h" -gt 0 ]; then
    printf '%dh%02dm' "$h" "$m"
  else
    printf '%dm' "$m"
  fi
}

ctx_bar=$(render_bar "$ctx_pct" "$bar_width")

out=$(printf '\033[01;36m%s\033[00m \033[2m%s ctx\033[00m  %s' "$model" "$ctx_label" "$ctx_bar")

if [ -n "$session_pct" ]; then
  session_bar=$(render_bar "$session_pct" "$bar_width")
  reset_label=""
  if [ -n "$session_resets_at" ]; then
    reset_label=$(printf ' \033[2m(resets in %s)\033[00m' "$(format_countdown "$session_resets_at")")
  fi
  out+=$(printf '  \033[2msession\033[00m  %s%s' "$session_bar" "$reset_label")
fi

printf '%s' "$out"
